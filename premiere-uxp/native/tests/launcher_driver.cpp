#include "runtime_launcher.hpp"

#include <cerrno>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <utility>

using resync::premiere::JobState;
using resync::premiere::JobStatus;
using resync::premiere::poll;
using resync::premiere::start_job;

int main(int argc, char** argv) {
    std::string native, job;
    bool abandon = false;
    bool embedded_nul = false;
    for (int index = 1; index < argc; ++index) {
        const std::string option(argv[index]);
        if (option == "--abandon") {
            abandon = true;
        } else if (option == "--embedded-nul") {
            embedded_nul = true;
        } else if ((option == "--native-directory" || option == "--job") && index + 1 < argc) {
            (option == "--job" ? job : native) = argv[++index];
        } else {
            std::cerr << "usage: launcher_driver --native-directory DIR --job ABSJOB [--abandon]\n";
            return 2;
        }
    }
    try {
        if (embedded_nul) {
            // std::string can carry a NUL even though process argv cannot.
            job.append("\0ignored", 8);
        }
        const auto started = std::chrono::steady_clock::now();
        pid_t pid = 0;
        double spawn_ms = 0;
        JobStatus result;
        {
            auto initial = start_job(native, job);
            // Exercise ownership transfer: destroying the moved-from temporary
            // must never kill the worker retained by the future SDK wrapper.
            auto handle = std::move(initial);
            pid = handle.pid();
            spawn_ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - started).count();
            if (abandon) {
                const auto ready = std::filesystem::path(job).parent_path() / "probe.ready";
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
                while (!std::filesystem::exists(ready) && std::chrono::steady_clock::now() < deadline) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(5));
                }
                if (!std::filesystem::exists(ready)) {
                    throw std::runtime_error("probe did not become ready before cleanup test");
                }
            } else {
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
                do {
                    result = poll(handle);
                    if (result.state != JobState::running) {
                        break;
                    }
                    std::this_thread::sleep_for(std::chrono::milliseconds(5));
                } while (std::chrono::steady_clock::now() < deadline);
                if (result.state == JobState::running) {
                    throw std::runtime_error("worker did not complete before driver timeout");
                }
                const auto repeated = poll(handle);
                if (handle.pid() != 0 || repeated.exit_code != result.exit_code ||
                        repeated.state != result.state) {
                    throw std::runtime_error("terminal poll was not cached/reaped");
                }
            }
        }
        int status = 0;
        errno = 0;
        const bool reaped = ::waitpid(pid, &status, WNOHANG) == -1 && errno == ECHILD;
        std::cout << "{\"pid\":" << pid << ",\"spawn_ms\":" << spawn_ms
                  << ",\"exit_code\":" << result.exit_code
                  << ",\"signal\":" << result.terminating_signal
                  << ",\"reaped\":" << (reaped ? "true" : "false")
                  << ",\"abandoned\":" << (abandon ? "true" : "false") << "}\n";
        return reaped ? 0 : 3;
    } catch (const std::exception& error) {
        std::cerr << "launcher rejected request: " << error.what() << '\n';
        return 2;
    }
}
