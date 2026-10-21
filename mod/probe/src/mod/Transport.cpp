#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::enqueue(std::string line) {
    std::lock_guard lock(mQueueMutex);
    if (mQueue.size() >= 20000) {
        mQueue.pop_front();
        mQueueDropped.fetch_add(1, std::memory_order_relaxed);
    }
    mQueue.push_back(std::move(line));
}

void ProbeMod::bootLog(std::string const& text) {
    std::ofstream out(getSelf().getModDir() / "harness-boot.log", std::ios::app);
    if (!out) {
        return;
    }
    SYSTEMTIME st{};
    GetLocalTime(&st);
    char stamp[64]{};
    std::snprintf(stamp, sizeof(stamp), "%02u:%02u:%02u.%03u ", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
    out << stamp << text << '\n';
}

void ProbeMod::queueServerTask(std::function<void()> fn) {
    auto server = ll::service::bedrock::getServerInstance();
    if (!server) {
        enqueue(R"({"event":"error","reason":"no_server_instance"})");
        return;
    }
    server->queueForServerThread([this, fn = std::move(fn)] {
        try {
            fn();
        } catch (std::exception const& e) {
            enqueue(
                nlohmann::json{
                    {"event",   "error"             },
                    {"reason",  "server_task_failed"},
                    {"message", e.what()            },
            }
                    .dump()
            );
        } catch (...) {
            enqueue(R"({"event":"error","reason":"server_task_failed"})");
        }
    });
}

void ProbeMod::serverLoop() {
    HANDLE pipe      = INVALID_HANDLE_VALUE;
    bool   connected = false;

    while (mRunning.load(std::memory_order_relaxed)) {
        try {
            if (pipe == INVALID_HANDLE_VALUE) {
                pipe = CreateNamedPipeW(
                    PipeName,
                    PIPE_ACCESS_DUPLEX,
                    PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                    1,
                    PipeBuffer,
                    PipeBuffer,
                    0,
                    nullptr
                );
                if (pipe == INVALID_HANDLE_VALUE) {
                    Sleep(500);
                    bootLog("pipe: create failed, retrying");
                    continue;
                }
                mCommandBuffer.clear();
                connected = false;
                bootLog("pipe: instance created");
            }

            if (!connected) {
                BOOL ok = ConnectNamedPipe(pipe, nullptr);
                if (!mRunning.load(std::memory_order_relaxed)) {
                    break;
                }
                if (!ok && GetLastError() != ERROR_PIPE_CONNECTED) {
                    bootLog("pipe: connect failed");
                    CloseHandle(pipe);
                    pipe = INVALID_HANDLE_VALUE;
                    continue;
                }
                connected = true;
                bootLog("pipe: client connected");
                {
                    std::lock_guard lock(mQueueMutex);
                    mQueue.clear();
                }
                auto const hello   = std::string(R"({"event":"hello","protocol":3})") + "\n";
                DWORD      written = 0;
                BOOL const helloOk = WriteFile(pipe, hello.data(), static_cast<DWORD>(hello.size()), &written, nullptr);
                bootLog(
                    "pipe: hello written=" + std::to_string(helloOk ? written : 0)
                    + " error=" + std::to_string(helloOk ? 0 : GetLastError())
                );
            }

            DWORD available = 0;
            if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
                bootLog("pipe: peek failed");
                connected = false;
                DisconnectNamedPipe(pipe);
                CloseHandle(pipe);
                pipe = INVALID_HANDLE_VALUE;
                continue;
            }

            if (available > 0) {
                std::array<char, 4096> buf{};
                DWORD                  toRead = available < buf.size() ? available : static_cast<DWORD>(buf.size());
                DWORD                  read   = 0;
                if (!ReadFile(pipe, buf.data(), toRead, &read, nullptr) || read == 0) {
                    bootLog("pipe: read failed");
                    connected = false;
                    DisconnectNamedPipe(pipe);
                    CloseHandle(pipe);
                    pipe = INVALID_HANDLE_VALUE;
                    continue;
                }
                mCommandBuffer.append(buf.data(), read);

                size_t pos = 0;
                while ((pos = mCommandBuffer.find('\n')) != std::string::npos) {
                    std::string line = mCommandBuffer.substr(0, pos);
                    mCommandBuffer.erase(0, pos + 1);
                    if (!line.empty() && line.back() == '\r') {
                        line.pop_back();
                    }
                    if (!line.empty()) {
                        handleLine(line);
                    }
                }
            }

            std::deque<std::string> batch;
            {
                std::lock_guard lock(mQueueMutex);
                batch.swap(mQueue);
            }

            bool failed = false;
            for (auto& line : batch) {
                std::string out    = line + "\n";
                size_t      offset = 0;
                // Byte-mode pipes may accept a partial write; a split JSON line
                // would desynchronise the client, so keep writing until done.
                while (offset < out.size()) {
                    DWORD       written = 0;
                    DWORD const chunk   = static_cast<DWORD>(out.size() - offset);
                    if (!WriteFile(pipe, out.data() + offset, chunk, &written, nullptr) || written == 0) {
                        failed = true;
                        break;
                    }
                    offset += written;
                }
                if (failed) {
                    break;
                }
            }

            if (failed) {
                bootLog("pipe: write failed");
                connected = false;
                DisconnectNamedPipe(pipe);
                CloseHandle(pipe);
                pipe = INVALID_HANDLE_VALUE;
                continue;
            }

            Sleep(1);
        } catch (std::exception const& e) {
            getSelf().getLogger().error("harness: pipe loop error: {}", e.what());
            if (pipe != INVALID_HANDLE_VALUE) {
                if (connected) {
                    DisconnectNamedPipe(pipe);
                }
                CloseHandle(pipe);
                pipe = INVALID_HANDLE_VALUE;
            }
            connected = false;
            Sleep(100);
        } catch (...) {
            getSelf().getLogger().error("harness: pipe loop error: unknown exception");
            if (pipe != INVALID_HANDLE_VALUE) {
                if (connected) {
                    DisconnectNamedPipe(pipe);
                }
                CloseHandle(pipe);
                pipe = INVALID_HANDLE_VALUE;
            }
            connected = false;
            Sleep(100);
        }
    }

    if (pipe != INVALID_HANDLE_VALUE) {
        if (connected) {
            DisconnectNamedPipe(pipe);
        }
        CloseHandle(pipe);
    }
}


} // namespace probe
