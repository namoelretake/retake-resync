#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <spawn.h>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

extern char** environ;

std::string escaped(const std::string& value) {
    std::ostringstream out;
    for (const unsigned char c : value) {
        if (c == '\\' || c == '"') { out << '\\' << c; }
        else if (c == '\n') { out << "\\n"; }
        else if (c == '\r') { out << "\\r"; }
        else if (c == '\t') { out << "\\t"; }
        else { out << c; }
    }
    return out.str();
}

std::string environment(const char* key) {
    const auto value = std::getenv(key);
    return value ? value : "";
}

int main(int argc, char** argv) {
    if (argc == 3 && std::strcmp(argv[1], "--descendant") == 0) {
        std::ofstream(argv[2]) << ::getpid();
        std::this_thread::sleep_for(std::chrono::seconds(60));
        return 0;
    }
    if (argc != 3 || std::strcmp(argv[1], "--job") != 0) {
        return 2;
    }
    const auto job = std::filesystem::path(argv[2]);
    const auto folder = job.parent_path();
    std::ifstream input(job);
    const std::string content((std::istreambuf_iterator<char>(input)), {});
    pid_t child = 0;
    if (content.find("\"spawn_child\":true") != std::string::npos) {
        const auto marker = (folder / "child.ready").string();
        char* child_argv[] = {argv[0], const_cast<char*>("--descendant"),
                             const_cast<char*>(marker.c_str()), nullptr};
        if (::posix_spawn(&child, argv[0], nullptr, nullptr, child_argv, environ) != 0) {
            return 9;
        }
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (!std::filesystem::exists(marker) && std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
    }
    struct stat zero {}, one {}, two {}, null_status {};
    ::fstat(0, &zero); ::fstat(1, &one); ::fstat(2, &two); ::stat("/dev/null", &null_status);
    const bool redirected = S_ISCHR(zero.st_mode) && S_ISCHR(one.st_mode) &&
        S_ISCHR(two.st_mode) && zero.st_rdev == null_status.st_rdev &&
        one.st_rdev == null_status.st_rdev && two.st_rdev == null_status.st_rdev;
    std::ofstream report(folder / "probe.json");
    report << "{\"argc\":" << argc << ",\"argument\":\"" << escaped(argv[1])
           << "\",\"job\":\"" << escaped(argv[2]) << "\",\"pid\":" << ::getpid()
           << ",\"pgroup\":" << ::getpgrp() << ",\"child\":" << child
           << ",\"redirected\":" << (redirected ? "true" : "false")
           << ",\"path\":\"" << escaped(environment("PATH")) << "\""
           << ",\"pythonpath\":\"" << escaped(environment("PYTHONPATH")) << "\""
           << ",\"pythonhome\":\"" << escaped(environment("PYTHONHOME")) << "\""
           << ",\"dyld\":\"" << escaped(environment("DYLD_LIBRARY_PATH")) << "\""
           << ",\"custom_host_secret\":\"" << escaped(environment("RESYNC_TEST_HOST_SECRET")) << "\"}\n";
    report.close();
    std::ofstream(folder / "probe.ready") << "ready";
    std::cout << "worker stdout must not leak into the host" << std::endl;
    std::cerr << "worker stderr must not leak into the host" << std::endl;
    if (content.find("\"slow\":true") != std::string::npos) {
        std::this_thread::sleep_for(std::chrono::seconds(60));
    }
    std::smatch match;
    if (std::regex_search(content, match, std::regex("\"exit_code\":([0-9]+)"))) {
        return std::stoi(match[1]);
    }
    return 0;
}
