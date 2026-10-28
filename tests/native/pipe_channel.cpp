#include "mod/PipeChannel.h"

#include <cassert>
#include <chrono>
#include <string>
#include <thread>

using probe::detail::PipeChannel;

std::wstring name() {
    static unsigned sequence = 0;
    return L"\\\\.\\pipe\\levipilot-native-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
           std::to_wstring(++sequence);
}

HANDLE client(wchar_t const* path) {
    auto const handle = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    assert(handle != INVALID_HANDLE_VALUE);
    return handle;
}

void connectionAndTransfer() {
    PipeChannel pipe;
    auto const path = name();
    std::atomic_bool running{true};
    assert(pipe.open(path.c_str(), 1024));
    auto const peer = client(path.c_str()); // ERROR_PIPE_CONNECTED race.
    assert(pipe.connect(running));
    DWORD bytes = 0, available = 0;
    assert(WriteFile(peer, "ping\n", 5, &bytes, nullptr) && bytes == 5);
    assert(pipe.peek(available) && available == 5);
    char buffer[16]{};
    assert(pipe.read(buffer, sizeof(buffer), bytes, running));
    assert(std::string_view(buffer, bytes) == "ping\n");
    std::string payload(256 << 10, 'x');
    payload.back() = '\n';
    std::string received;
    std::thread reader([&] {
        char part[113];
        while (received.size() < payload.size()) {
            DWORD count = 0;
            assert(ReadFile(peer, part, sizeof(part), &count, nullptr) && count > 0);
            received.append(part, count);
        }
    });
    assert(pipe.writeAll(payload, running));
    reader.join();
    assert(received == payload);
    CloseHandle(peer);
    assert(!pipe.peek(available));
    pipe.close();
    assert(!pipe.valid());
    assert(pipe.open(path.c_str(), 1024)); // Reconnect uses fresh framing/I/O state.
    auto const next = client(path.c_str());
    assert(pipe.connect(running) && pipe.writeAll("ok\n", running));
    assert(ReadFile(next, buffer, sizeof(buffer), &bytes, nullptr) && bytes == 3);
    CloseHandle(next);
}

void cancellation(bool connected, bool write) {
    PipeChannel pipe;
    auto const path = name();
    std::atomic_bool running{true}, entered{false};
    assert(pipe.open(path.c_str(), 1024));
    HANDLE peer = INVALID_HANDLE_VALUE;
    if (connected) {
        peer = client(path.c_str());
        assert(pipe.connect(running));
    }
    bool result = true;
    DWORD error = 0;
    std::string payload(1 << 20, 'x');
    std::thread worker([&] {
        entered = true;
        char data[16];
        DWORD bytes = 0;
        result = !connected ? pipe.connect(running)
                 : write    ? pipe.writeAll(payload, running)
                            : pipe.read(data, sizeof(data), bytes, running);
        error = GetLastError();
    });
    while (!entered.load())
        std::this_thread::yield();
    Sleep(20);
    auto const start = GetTickCount64();
    running = false;
    worker.join();
    assert(!result && error == ERROR_OPERATION_ABORTED);
    assert(GetTickCount64() - start < 1500);
    if (peer != INVALID_HANDLE_VALUE)
        CloseHandle(peer);
}

void nonreadingClientTimesOut() {
    PipeChannel pipe;
    auto const path = name();
    std::atomic_bool running{true};
    assert(pipe.open(path.c_str(), 1024));
    auto const peer = client(path.c_str());
    assert(pipe.connect(running));
    std::string payload(1 << 20, 'x');
    auto const start = GetTickCount64();
    assert(!pipe.writeAll(payload, running, 40));
    assert(GetLastError() == ERROR_SEM_TIMEOUT);
    assert(GetTickCount64() - start < 1500);
    CloseHandle(peer);
}

int main() {
    connectionAndTransfer();
    cancellation(false, false);
    cancellation(true, false);
    cancellation(true, true);
    nonreadingClientTimesOut();
    DWORD before = 0, after = 0;
    assert(GetProcessHandleCount(GetCurrentProcess(), &before));
    for (int i = 0; i < 100; ++i) {
        PipeChannel pipe;
        auto const path = name();
        assert(pipe.open(path.c_str(), 1024));
    }
    assert(GetProcessHandleCount(GetCurrentProcess(), &after));
    assert(before == after);
}
