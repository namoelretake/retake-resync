#include "runtime_launcher.hpp"

#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <regex>
#include <spawn.h>
#include <stdexcept>
#include <system_error>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace resync::premiere {
namespace {

void check_spawn_result(int result, const char* operation) {
    if (result != 0) {
        throw std::system_error(result, std::generic_category(), operation);
    }
}

struct SpawnActions {
    posix_spawn_file_actions_t value;
    SpawnActions() { check_spawn_result(posix_spawn_file_actions_init(&value), "initialize worker file actions"); }
    ~SpawnActions() { posix_spawn_file_actions_destroy(&value); }
};

struct SpawnAttributes {
    posix_spawnattr_t value;
    SpawnAttributes() { check_spawn_result(posix_spawnattr_init(&value), "initialize worker spawn attributes"); }
    ~SpawnAttributes() { posix_spawnattr_destroy(&value); }
};

bool has_nul(const std::string& path) { return path.find('\0') != std::string::npos; }

struct stat checked_regular(const std::filesystem::path& path, const char* label) {
    struct stat status {};
    const auto text = path.string();
    if (has_nul(text) || ::lstat(text.c_str(), &status) != 0 || !S_ISREG(status.st_mode)) {
        throw std::invalid_argument(std::string(label) + " must be an existing regular file without a symlink");
    }
    return status;
}

void checked_directory(const std::filesystem::path& path, const char* label) {
    struct stat status {};
    if (::lstat(path.c_str(), &status) != 0 || !S_ISDIR(status.st_mode)) {
        throw std::invalid_argument(std::string(label) + " must be an existing directory without a symlink");
    }
}

void checked_executable(const std::filesystem::path& path, const char* label) {
    checked_regular(path, label);
    if (::access(path.c_str(), X_OK) != 0) {
        throw std::invalid_argument(std::string(label) + " is not executable");
    }
}

std::filesystem::path checked_job(const std::string& text) {
    if (has_nul(text)) {
        throw std::invalid_argument("job path contains a NUL byte");
    }
    const std::filesystem::path path(text);
    static const std::regex basename("resync_[0-9a-f]{24}\\.job\\.json");
    if (!path.is_absolute() || !std::regex_match(path.filename().string(), basename)) {
        throw std::invalid_argument("job must have an absolute resync_<24 lowercase hex>.job.json path");
    }
    const auto status = checked_regular(path, "job");
    if (status.st_size < 0 || status.st_size > 16384) {
        throw std::invalid_argument("job exceeds the 16384-byte protocol limit");
    }
    return std::filesystem::canonical(path);
}

}  // namespace

JobHandle::JobHandle(pid_t pid) noexcept
    : active_pid_(pid), status_{JobState::running, pid, -1, 0} {}

JobHandle::JobHandle(JobHandle&& other) noexcept
    : active_pid_(std::exchange(other.active_pid_, 0)), status_(other.status_) {
    other.status_ = {};
}

JobHandle& JobHandle::operator=(JobHandle&& other) noexcept {
    if (this != &other) {
        cleanup();
        active_pid_ = std::exchange(other.active_pid_, 0);
        status_ = other.status_;
        other.status_ = {};
    }
    return *this;
}

void JobHandle::finish(int wait_status) noexcept {
    active_pid_ = 0;
    if (WIFEXITED(wait_status)) {
        status_.state = JobState::exited;
        status_.exit_code = WEXITSTATUS(wait_status);
    } else if (WIFSIGNALED(wait_status)) {
        status_.state = JobState::signaled;
        status_.terminating_signal = WTERMSIG(wait_status);
        status_.exit_code = 128 + status_.terminating_signal;
    } else {
        status_.state = JobState::unavailable;
    }
}

void JobHandle::cleanup() noexcept {
    if (active_pid_ <= 0) {
        return;
    }
    int wait_status = 0;
    pid_t waited;
    do {
        waited = ::waitpid(active_pid_, &wait_status, WNOHANG);
    } while (waited < 0 && errno == EINTR);
    if (waited == active_pid_) {
        finish(wait_status);
        return;
    }
    if (waited < 0) {
        // Never signal a PID/group after losing child ownership to a host
        // SIGCHLD handler; its numeric ID might already belong to another job.
        active_pid_ = 0;
        status_.state = JobState::unavailable;
        return;
    }
    // POSIX_SPAWN_SETPGROUP with pgroup=0 made the worker its group's leader.
    // No process outside this job is intentionally signaled during cleanup.
    ::kill(-active_pid_, SIGKILL);
    do {
        waited = ::waitpid(active_pid_, &wait_status, 0);
    } while (waited < 0 && errno == EINTR);
    if (waited == active_pid_) {
        finish(wait_status);
    } else {
        active_pid_ = 0;
        status_.state = JobState::unavailable;
    }
}

JobHandle::~JobHandle() noexcept { cleanup(); }

JobStatus poll(JobHandle& job) {
    if (job.active_pid_ <= 0) {
        return job.status_;
    }
    int wait_status = 0;
    pid_t waited;
    do {
        waited = ::waitpid(job.active_pid_, &wait_status, WNOHANG);
    } while (waited < 0 && errno == EINTR);
    if (waited == job.active_pid_) {
        job.finish(wait_status);
    } else if (waited < 0) {
        if (errno != ECHILD) {
            throw std::system_error(errno, std::generic_category(), "poll ReSync worker");
        }
        job.active_pid_ = 0;
        job.status_.state = JobState::unavailable;
    }
    return job.status_;
}

JobHandle start_job(const std::filesystem::path& native_binary_directory,
                    const std::string& absolute_job_path) {
    const auto job = checked_job(absolute_job_path);
    if (has_nul(native_binary_directory.string()) || !native_binary_directory.is_absolute()) {
        throw std::invalid_argument("native binary directory must be a trusted absolute path");
    }
    const auto native = std::filesystem::canonical(native_binary_directory);
    const auto runtime = native / "runtime";
    checked_directory(runtime, "bundled runtime");
    checked_directory(runtime / "bin", "bundled decoder directory");
    checked_directory(runtime / "resync-premiere-worker", "bundled worker directory");
    const auto manifest = checked_regular(runtime / "runtime.json", "runtime manifest");
    if (manifest.st_size <= 0 || manifest.st_size > 65536) {
        throw std::invalid_argument("runtime manifest must be nonempty and at most 65536 bytes");
    }
    // Never select an executable, arguments or process root from JSON or JS.
    const auto worker = runtime / "resync-premiere-worker" / "resync-premiere-worker";
    const auto decoder = runtime / "bin" / "ffmpeg";
    checked_executable(worker, "bundled ReSync worker");
    checked_executable(decoder, "bundled FFmpeg");

    SpawnActions actions;
    check_spawn_result(posix_spawn_file_actions_addopen(&actions.value, STDIN_FILENO,
                       "/dev/null", O_RDONLY, 0), "redirect worker stdin");
    check_spawn_result(posix_spawn_file_actions_addopen(&actions.value, STDOUT_FILENO,
                       "/dev/null", O_WRONLY, 0), "redirect worker stdout");
    check_spawn_result(posix_spawn_file_actions_addopen(&actions.value, STDERR_FILENO,
                       "/dev/null", O_WRONLY, 0), "redirect worker stderr");
    SpawnAttributes attributes;
    short flags = POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF;
#if defined(__APPLE__) && defined(POSIX_SPAWN_CLOEXEC_DEFAULT)
    flags |= POSIX_SPAWN_CLOEXEC_DEFAULT;
#endif
    check_spawn_result(posix_spawnattr_setflags(&attributes.value, flags), "set worker spawn flags");
    check_spawn_result(posix_spawnattr_setpgroup(&attributes.value, 0), "create private worker process group");
    sigset_t signals;
    sigemptyset(&signals);
    check_spawn_result(posix_spawnattr_setsigmask(&attributes.value, &signals), "unblock worker signals");
    for (const auto signal : {SIGPIPE, SIGINT, SIGTERM, SIGHUP, SIGQUIT, SIGCHLD}) {
        sigaddset(&signals, signal);
    }
    check_spawn_result(posix_spawnattr_setsigdefault(&attributes.value, &signals), "reset worker signal dispositions");

    const auto worker_text = worker.string();
    const auto job_text = job.string();
    std::vector<char*> argv{const_cast<char*>(worker_text.c_str()),
                            const_cast<char*>("--job"),
                            const_cast<char*>(job_text.c_str()), nullptr};
    // A clean environment prevents host/extension PYTHONPATH, PYTHONHOME,
    // DYLD_*, shell startup hooks or user PATH entries from changing the engine.
    std::string path = "PATH=" + (runtime / "bin").string() + ":/usr/bin:/bin";
    std::vector<char*> environment{path.data(), const_cast<char*>("LANG=en_US.UTF-8"),
                                  const_cast<char*>("LC_ALL=en_US.UTF-8"),
                                  const_cast<char*>("PYTHONNOUSERSITE=1"),
                                  const_cast<char*>("PYTHONSAFEPATH=1"), nullptr};
    pid_t pid = 0;
    check_spawn_result(::posix_spawn(&pid, worker_text.c_str(), &actions.value,
                                    &attributes.value, argv.data(), environment.data()),
                       "start bundled ReSync worker");
    return JobHandle(pid);
}

}  // namespace resync::premiere
