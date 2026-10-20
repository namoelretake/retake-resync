#pragma once

// SDK-independent process launcher only. This is not a UXP addon entry point.
// A future Adobe SDK wrapper must derive native_binary_directory from its own
// loaded binary (for example dladdr), never from a JavaScript argument.

#include <filesystem>
#include <string>
#include <sys/types.h>

namespace resync::premiere {

enum class JobState { running, exited, signaled, unavailable };

struct JobStatus {
    JobState state = JobState::unavailable;
    pid_t process_id = 0;
    int exit_code = -1;          // 128 + signal for a signaled worker.
    int terminating_signal = 0;
};

class JobHandle;
JobHandle start_job(const std::filesystem::path& native_binary_directory,
                    const std::string& absolute_job_path);
JobStatus poll(JobHandle& job);

// Owns one worker and its private process group; cannot be made from a caller's
// arbitrary PID. Retain the handle and poll until completion, including after a
// panel merely stops waiting. A terminal poll is cached and reaps the worker.
//
// Abandoning an active handle kills its process group (including FFmpeg) and
// synchronously reaps its direct child. No threads, callbacks or code remain to
// run after an addon unload. A future explicit cancel action can use this same
// ownership policy; this core does not change the legacy panel's cancel flow.
class JobHandle {
public:
    JobHandle(const JobHandle&) = delete;
    JobHandle& operator=(const JobHandle&) = delete;
    JobHandle(JobHandle&& other) noexcept;
    JobHandle& operator=(JobHandle&& other) noexcept;
    ~JobHandle() noexcept;

    // Positive while this handle owns an unreaped worker, zero after completion.
    pid_t pid() const noexcept { return active_pid_; }

private:
    explicit JobHandle(pid_t pid) noexcept;
    void cleanup() noexcept;
    void finish(int wait_status) noexcept;

    pid_t active_pid_ = 0;
    JobStatus status_;

    friend JobHandle start_job(const std::filesystem::path&, const std::string&);
    friend JobStatus poll(JobHandle&);
};

// Only this handle may reap its worker. If another host component reaps it or
// configures SIGCHLD=SIG_IGN, poll reports unavailable and does not signal a
// potentially reused PID. Adobe host lifecycle/signal behavior still needs a
// real SDK integration test before this core can ship as a hybrid addon.

}  // namespace resync::premiere
