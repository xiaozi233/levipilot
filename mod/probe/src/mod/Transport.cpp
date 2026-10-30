#include "mod/Internal.h"
#include "mod/PipeChannel.h"
#include "mod/TransportModel.h"

using namespace probe::detail;

namespace {
bool runServerTaskWithSeh(std::function<void()> const& task) {
    __try {
        task();
        return true;
    } __except (probe::detail::isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        return false;
    }
}
} // namespace

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
    auto reportRecordFault = [this] {
        if (recordTakeFault()) {
            enqueue(R"({"event":"error","reason":"internal_error","phase":"record_tick","message":"structured exception"})");
        }
    };
    // The spool thread is the only consumer of task/chat events; if one bad
    // payload ever threw, the whole task pipeline would freeze (event ring
    // fills, tickTask stops). Failures become error events instead.
    bool faultReported = false;
    while (mRunning.load(std::memory_order_relaxed)) {
        Sleep(25);
        try {
            drainTaskEvents();
            drainChatHistory();
            reportRecordFault();
            flushSpool(mSpoolForce.exchange(false, std::memory_order_acq_rel));
            faultReported = false;
        } catch (std::exception const& e) {
            mSpoolErrors.fetch_add(1, std::memory_order_relaxed);
            if (!faultReported) {
                faultReported = true;
                enqueue(
                    nlohmann::json{
                        {"event",   "error"          },
                        {"reason",  "internal_error" },
                        {"phase",   "spool_thread"   },
                        {"message", e.what()         },
                }
                        .dump()
                );
            }
        } catch (...) {
            mSpoolErrors.fetch_add(1, std::memory_order_relaxed);
            if (!faultReported) {
                faultReported = true;
                enqueue(R"({"event":"error","reason":"internal_error","phase":"spool_thread","message":"unknown"})");
            }
        }
    }
    try {
        drainTaskEvents();
        drainChatHistory();
        reportRecordFault();
    } catch (...) {
    }
    flushSpool(true);
}

// Re-append rows that could not be written so they survive until the next
// flush attempt. Capped so a permanently failing disk cannot grow memory.
void ProbeMod::requeueSpool(std::string& data) {
    std::lock_guard lock(mQueueMutex);
    if (mSpoolBuffer.size() + data.size() > (8u << 20)) {
        mSpoolErrors.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    data += mSpoolBuffer;
    mSpoolBuffer.swap(data);
}

std::uint64_t ProbeMod::flushSpool(bool force) {
    // F6: one lock covers "swap the buffer" and "write it", so concurrent
    // flushes cannot reorder rows. enqueue() only takes mQueueMutex and is
    // never blocked by file IO.
    std::lock_guard serial(mSpoolWriteMutex);
    std::string     data;
    {
        std::lock_guard     lock(mQueueMutex);
        std::uint64_t const now = GetTickCount64();
        bool const          due = force || mSpoolBuffer.size() >= (64u << 10) || now - mLastFlushMs >= 100;
        if (!due) {
            return 0;
        }
        mLastFlushMs = now;
        data.swap(mSpoolBuffer);

        // Queue overflow itself is an event worth keeping next to the data.
        std::uint64_t const dropped = mQueueDropped.load(std::memory_order_relaxed);
        if (dropped != mSpoolDroppedMarked) {
            mSpoolDroppedMarked = dropped;
            data +=
                nlohmann::json{
                    {"event", "queue_dropped"},
                    {"count", dropped        },
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
            // The swapped-out rows must not vanish with a transient open
            // failure (disk full, antivirus lock); retry on the next flush.
            requeueSpool(data);
            return 0;
        }
    }
    mSpoolFile.write(data.data(), static_cast<std::streamsize>(data.size()));
    mSpoolFile.flush();
    if (!mSpoolFile) {
        // Same contract as the open failure: count, requeue, and force a
        // fresh open next time instead of trusting a failed stream.
        mSpoolErrors.fetch_add(1, std::memory_order_relaxed);
        mSpoolFile.close();
        requeueSpool(data);
        return 0;
    }
    mSpoolFileBytes    += data.size();
    std::uint64_t rows  = 0;
    for (char ch : data) {
        if (ch == '\n') {
            ++rows;
        }
    }
    mSpoolRows.fetch_add(rows, std::memory_order_relaxed);
    mSpoolFlushes.fetch_add(1, std::memory_order_relaxed);

    // F6: keep a single run from growing one file forever.
    constexpr std::uint64_t kSpoolRotateBytes = 64ull << 20;
    if (mSpoolFileBytes >= kSpoolRotateBytes) {
        mSpoolFile.close();
        std::error_code ec;
        auto const      pid     = std::to_string(GetCurrentProcessId());
        bool            rotated = false;
        for (int n = ++mSpoolRotation; n < mSpoolRotation + 1000 && !rotated; ++n) {
            auto candidate = mSpoolPath.parent_path() / ("spool-" + pid + "-" + std::to_string(n) + ".jsonl");
            if (!std::filesystem::exists(candidate, ec)) {
                std::filesystem::rename(mSpoolPath, candidate, ec);
                rotated = !ec;
            }
        }
        mSpoolFileBytes = 0;
        mSpoolFile.open(mSpoolPath, std::ios::app | std::ios::binary);
        if (!mSpoolFile) {
            mSpoolErrors.fetch_add(1, std::memory_order_relaxed);
            return rows;
        }
    }
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
        gServerThreadId.store(GetCurrentThreadId(), std::memory_order_relaxed);
        try {
            if (!runServerTaskWithSeh(fn)) {
                enqueue(R"({"event":"error","reason":"server_task_failed","message":"seh"})");
            }
        } catch (std::exception const& e) {
            enqueue(
                nlohmann::json{
                    {"event",   "error"             },
                    {"reason",  "server_task_failed"},
                    {"message", e.what()              },
            }
                    .dump()
            );
        } catch (...) {
            enqueue(R"({"event":"error","reason":"server_task_failed"})");
        }
    });
}

void ProbeMod::serverLoop() {
    PipeChannel pipe;
    bool   connected = false;
    CommandFramer<> commands;
    std::string writeBuffer;
    writeBuffer.reserve(PipeBuffer);
    auto writeFailed = [this] {
        auto const error = GetLastError();
        if (error == ERROR_SEM_TIMEOUT) {
            mPipeWriteTimeouts.fetch_add(1, std::memory_order_relaxed);
        }
        bootLog("pipe: write failed error=" + std::to_string(error));
    };

    while (mRunning.load(std::memory_order_relaxed)) {
        try {
            if (!pipe.valid()) {
                if (!pipe.open(PipeName, PipeBuffer)) {
                    Sleep(500);
                    bootLog("pipe: create failed, retrying");
                    continue;
                }
                commands.reset(); // Framing state belongs to this pipe instance.
                connected = false;
                bootLog("pipe: instance created");
            }

            // lock_input is scoped to a client session: the moment the script
            // is gone (disconnect, crash, closed client) real input must come
            // back, or the person at the keyboard cannot even click Resume.
            if (!connected && mHardwareInputLocked.load(std::memory_order_relaxed)) {
                mHardwareInputLocked.store(false, std::memory_order_relaxed);
                bootLog("input_lock: released (no client)");
            }

            if (!connected) {
                bool const ok = pipe.connect(mRunning);
                if (!mRunning.load(std::memory_order_relaxed)) {
                    break;
                }
                if (!ok) {
                    bootLog("pipe: connect failed");
                    pipe.close();
                    continue;
                }
                connected = true;
                bootLog("pipe: client connected");
                if (mLockInputOnConnect.load(std::memory_order_relaxed)) {
                    mHardwareInputLocked.store(true, std::memory_order_relaxed);
                    bootLog("input_lock: armed (client connected)");
                }
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
                    {"event",         "hello"             },
                    {"protocol",      4                   },
                    {"spool",         mSpoolPath.string() },
                    {"replay_rows",   replay              },
                    {"queue_dropped", mQueueDropped.load()},
                };
                helloJson["capabilities"] = nlohmann::json::array(
                    {"tick_json",
                     "bin_record",
                     "mem",
                     "watch",
                     "signin",
                     "hook",
                     "ecs",
                     "task_lifecycle",
                     "chat_history"}
                );
                helloJson["build_id"]      = buildId();
                helloJson["version_ok"]    = versionOk();
                helloJson["hooks_allowed"] = hooksAllowed();
                helloJson["compat"]        = allowVersionMismatch();
                helloJson["profile"]       = profileJson();
                auto const hello           = helloJson.dump() + "\n";
                if (!pipe.writeAll(hello, mRunning)) {
                    writeFailed();
                    connected = false;
                    pipe.close();
                    continue;
                }
                bootLog("pipe: hello written=" + std::to_string(hello.size()));
            }

            DWORD available = 0;
            if (!pipe.peek(available)) {
                bootLog("pipe: peek failed");
                connected = false;
                pipe.close();
                continue;
            }

            if (available > 0) {
                std::array<char, 4096> buf{};
                DWORD                  toRead = available < buf.size() ? available : static_cast<DWORD>(buf.size());
                DWORD                  read   = 0;
                if (!pipe.read(buf.data(), toRead, read, mRunning) || read == 0) {
                    bootLog("pipe: read failed");
                    connected = false;
                    pipe.close();
                    continue;
                }
                commands.feed(
                    std::string_view(buf.data(), read),
                    [this](std::string const& line) { handleLine(line); },
                    [this](std::size_t bytes) {
                        enqueue(
                            nlohmann::json{
                                {"event",  "error"        },
                                {"reason", "line_too_long"},
                                {"bytes",  bytes          },
                            }.dump()
                        );
                    }
                );
            }

            std::deque<std::string> batch;
            {
                std::lock_guard lock(mQueueMutex);
                batch.swap(mQueue);
            }

            bool const failed = !writeLineBatch<PipeBuffer>(batch, writeBuffer, [&](std::string const& bytes) {
                return pipe.writeAll(bytes, mRunning);
            });

            if (failed) {
                writeFailed();
                connected = false;
                pipe.close();
                continue;
            }

            Sleep(1);
        } catch (std::exception const& e) {
            getSelf().getLogger().error("harness: pipe loop error: {}", e.what());
            pipe.close();
            connected = false;
            Sleep(100);
        } catch (...) {
            getSelf().getLogger().error("harness: pipe loop error: unknown exception");
            pipe.close();
            connected = false;
            Sleep(100);
        }
    }

    mHardwareInputLocked.store(false, std::memory_order_relaxed);
}


} // namespace probe
