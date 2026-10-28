#pragma once

#include <windows.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <string_view>

namespace probe::detail {

// One worker owns this channel and issues at most one operation at a time.
// Every pending operation is canceled AND drained before its stack storage or
// buffers are released. Shutdown is observed while waiting, without another
// thread racing a CancelSynchronousIo call against the next WriteFile.
class PipeChannel {
  public:
    static constexpr DWORD DefaultTimeoutMs = 5000;
    PipeChannel() = default;
    PipeChannel(PipeChannel const&) = delete;
    PipeChannel& operator=(PipeChannel const&) = delete;
    ~PipeChannel() {
        close();
        if (mEvent)
            CloseHandle(mEvent);
    }

    bool open(wchar_t const* name, DWORD bufferBytes) {
        close();
        if (!mEvent)
            mEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!mEvent)
            return false;
        mPipe = CreateNamedPipeW(name, PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                                 PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1,
                                 bufferBytes, bufferBytes, 0, nullptr);
        return valid();
    }

    bool valid() const { return mPipe != INVALID_HANDLE_VALUE; }
    void close() {
        if (valid()) {
            DisconnectNamedPipe(mPipe);
            CloseHandle(mPipe);
            mPipe = INVALID_HANDLE_VALUE;
        }
    }

    bool connect(std::atomic_bool const& running) {
        if (!keepRunning(running))
            return false;
        auto operation = begin();
        if (ConnectNamedPipe(mPipe, &operation))
            return true;
        auto const error = GetLastError();
        if (error == ERROR_PIPE_CONNECTED)
            return true;
        if (error != ERROR_IO_PENDING)
            return false;
        DWORD ignored = 0;
        return await(operation, ignored, running, 0);
    }

    bool peek(DWORD& available) { return PeekNamedPipe(mPipe, nullptr, 0, nullptr, &available, nullptr) != FALSE; }

    bool read(void* buffer, DWORD size, DWORD& bytes, std::atomic_bool const& running) {
        bytes = 0;
        if (!keepRunning(running))
            return false;
        auto operation = begin();
        auto const deadline = GetTickCount64() + DefaultTimeoutMs;
        return finish(ReadFile(mPipe, buffer, size, &bytes, &operation), operation, bytes, running, deadline);
    }

    bool writeAll(std::string_view data, std::atomic_bool const& running, DWORD timeoutMs = DefaultTimeoutMs) {
        auto const deadline = GetTickCount64() + timeoutMs;
        while (!data.empty()) {
            if (!keepRunning(running))
                return false;
            if (GetTickCount64() >= deadline) {
                SetLastError(ERROR_SEM_TIMEOUT);
                return false;
            }
            auto operation = begin();
            auto const size = static_cast<DWORD>(std::min<std::size_t>(data.size(), 64u << 10));
            DWORD bytes = 0;
            if (!finish(WriteFile(mPipe, data.data(), size, &bytes, &operation), operation, bytes, running, deadline)) {
                return false;
            }
            if (bytes == 0) {
                SetLastError(ERROR_BROKEN_PIPE);
                return false;
            }
            data.remove_prefix(bytes);
        }
        return true;
    }

  private:
    static bool keepRunning(std::atomic_bool const& running) {
        if (running.load(std::memory_order_acquire))
            return true;
        SetLastError(ERROR_OPERATION_ABORTED);
        return false;
    }

    OVERLAPPED begin() {
        ResetEvent(mEvent);
        OVERLAPPED operation{};
        operation.hEvent = mEvent;
        return operation;
    }

    bool finish(BOOL completed, OVERLAPPED& operation, DWORD& bytes, std::atomic_bool const& running,
                std::uint64_t deadline) {
        if (completed)
            return GetOverlappedResult(mPipe, &operation, &bytes, FALSE) != FALSE;
        if (GetLastError() != ERROR_IO_PENDING)
            return false;
        return await(operation, bytes, running, deadline);
    }

    bool cancel(OVERLAPPED& operation, DWORD error) {
        CancelIoEx(mPipe, &operation);
        DWORD ignored = 0;
        GetOverlappedResult(mPipe, &operation, &ignored, TRUE);
        SetLastError(error);
        return false;
    }

    bool await(OVERLAPPED& operation, DWORD& bytes, std::atomic_bool const& running, std::uint64_t deadline) {
        for (;;) {
            if (!running.load(std::memory_order_acquire)) {
                return cancel(operation, ERROR_OPERATION_ABORTED);
            }
            auto const now = GetTickCount64();
            if (deadline && now >= deadline)
                return cancel(operation, ERROR_SEM_TIMEOUT);
            auto const waitMs = deadline ? static_cast<DWORD>(std::min<std::uint64_t>(25, deadline - now)) : 25;
            switch (WaitForSingleObject(mEvent, waitMs)) {
            case WAIT_OBJECT_0:
                return GetOverlappedResult(mPipe, &operation, &bytes, FALSE) != FALSE;
            case WAIT_TIMEOUT:
                break;
            default:
                return cancel(operation, GetLastError());
            }
        }
    }

    HANDLE mPipe = INVALID_HANDLE_VALUE;
    HANDLE mEvent = nullptr;
};

} // namespace probe::detail
