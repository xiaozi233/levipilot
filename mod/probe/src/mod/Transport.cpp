#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::enqueue(std::string line) {
    std::lock_guard lock(mQueueMutex);
    if (mQueue.size() >= 20000) {
        mQueue.pop_front();
        mQueueDropped.fetch_add(1, std::memory_order_relaxed);
    }
    // Spool before the move below. The spool thread is the only reader, and it
    // swaps the buffer under the same mutex, so the tick path stays allocation-
    // free apart from the string append it already did for the queue.
    mSpoolBuffer.append(line);
    mSpoolBuffer.push_back('\n');
    mQueue.push_back(std::move(line));
}

void ProbeMod::spoolThreadLoop() {
    while (mRunning.load(std::memory_order_relaxed)) {
        Sleep(25);
        flushSpool(mSpoolForce.exchange(false, std::memory_order_acq_rel));
    }
    flushSpool(true);
}

std::uint64_t ProbeMod::flushSpool(bool force) {
    std::string data;
    {
        std::lock_guard lock(mQueueMutex);
        std::uint64_t const now = GetTickCount64();
        bool const due = force || mSpoolBuffer.size() >= (64u << 10) || now - mLastFlushMs >= 100;
        if (!due) {
            return 0;
        }
        mLastFlushMs = now;
        data.swap(mSpoolBuffer);

        // Queue overflow itself is an event worth keeping next to the data.
        std::uint64_t const dropped = mQueueDropped.load(std::memory_order_relaxed);
        if (dropped != mSpoolDroppedMarked) {
            mSpoolDroppedMarked = dropped;
            data += nlohmann::json{
                {"event", "queue_dropped"       },
                {"count", dropped               },
            }
                        .dump();
            data += '\n';
        }
        if (data.empty()) {
            return 0;
        }
    }

    std::lock_guard lock(mSpoolFileMutex);
    if (!mSpoolFile.is_open()) {
        std::error_code ec;
        std::filesystem::create_directories(mSpoolPath.parent_path(), ec);
        mSpoolFile.open(mSpoolPath, std::ios::app | std::ios::binary);
        if (!mSpoolFile) {
            mSpoolErrors.fetch_add(1, std::memory_order_relaxed);
            return 0;
        }
    }
    mSpoolFile.write(data.data(), static_cast<std::streamsize>(data.size()));
    mSpoolFile.flush();
    std::uint64_t rows = 0;
    for (char ch : data) {
        if (ch == '\n') {
            ++rows;
        }
    }
    mSpoolRows.fetch_add(rows, std::memory_order_relaxed);
    mSpoolFlushes.fetch_add(1, std::memory_order_relaxed);
    return rows;
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

    HANDLE threadHandle = nullptr;
    if (DuplicateHandle(
            GetCurrentProcess(),
            GetCurrentThread(),
            GetCurrentProcess(),
            &threadHandle,
            0,
            FALSE,
            DUPLICATE_SAME_ACCESS
        )) {
        mServerThreadHandle.store(threadHandle, std::memory_order_release);
    }

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
                // Do not drop rows accumulated while no client was attached:
                // rotate them to the spool first, then deliver them on this
                // connection. The client sees how many rows to expect.
                std::size_t replay = 0;
                {
                    std::lock_guard lock(mQueueMutex);
                    replay = mQueue.size();
                }
                flushSpool(true);
                nlohmann::json helloJson{
                    {"event", "hello"                },
                    {"protocol", 4                   },
                    {"spool", mSpoolPath.string()    },
                    {"replay_rows", replay           },
                    {"queue_dropped", mQueueDropped.load()},
                };
                helloJson["capabilities"] = nlohmann::json::array(
                    {"tick_json", "bin_record", "mem", "watch", "signin", "hook", "ecs"}
                );
                helloJson["build_id"]   = buildId();
                helloJson["version_ok"] = versionOk();
                auto const hello   = helloJson.dump() + "\n";
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
                if (mDiscardPartial) {
                    // Resync: drop everything up to and including the newline
                    // that ends the oversized line.
                    DWORD start = 0;
                    while (start < read && buf[start] != '\n') {
                        ++start;
                    }
                    if (start < read) {
                        mDiscardPartial = false;
                        mCommandBuffer.append(buf.data() + start + 1, read - start - 1);
                    }
                } else {
                    mCommandBuffer.append(buf.data(), read);
                }

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

                // F1: a client that never sends '\n' must not grow the partial
                // line without bound. Complete lines above are still handled,
                // and the tail is discarded until the next newline so later
                // commands are not glued to the oversized line.
                constexpr std::size_t kMaxCommandLine = 1u << 20;
                if (mCommandBuffer.size() > kMaxCommandLine) {
                    std::uint64_t const bytes = mCommandBuffer.size();
                    mCommandBuffer.clear();
                    mDiscardPartial = true;
                    enqueue(
                        nlohmann::json{
                            {"event",  "error"       },
                            {"reason", "line_too_long"},
                            {"bytes",  bytes          },
                    }
                            .dump()
                    );
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
