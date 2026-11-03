#include "mod/PacketInjectionModel.h"

#include <cassert>
#include <cstdlib>
#include <new>
#include <thread>

using namespace probe::detail;
std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size)) return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }
bool is(char const* text, std::string_view wanted) { return text && text == wanted; }
PlayerIdentity owner{7, 8, 3, 12, 1};

void templates() {
    CommandTemplateStore store;
    assert(store.initialize());
    CommandTemplate out;
    assert(is(store.latest(out, owner, 4), "no_command_template"));
    auto const before = allocations.load();
    store.capture(owner, 4, [](auto& row) {
        row.uuidA = 20; // Origin UUID is not assumed to equal player UUID.
        row.uuidB = 30;
        row.playerId = -5;
        row.setRequestId(std::string_view("ab\0cd", 5));
        return true;
    });
    assert(!store.latest(out, owner, 4));
    assert(out.uuidA == 20 && out.playerId == -5 && out.requestIdView() == std::string_view("ab\0cd", 5));
    assert(is(store.latest(out, owner, 5), "command_template_stale"));
    auto other = owner;
    ++other.uuidB;
    assert(is(store.latest(out, other, 4), "command_template_owner_mismatch"));
    ++other.epoch;
    assert(is(store.latest(out, other, 4), "command_template_stale"));
    store.capture(owner, 4, [&](auto&) {
        // Once a newer capture has reserved, readers must not reuse the old row.
        assert(is(store.latest(out, owner, 4), "command_template_busy"));
        return false;
    });
    assert(is(store.latest(out, owner, 4), "command_template_fault"));
    std::array<char, 513> longId{};
    auto const attempts = store.attempts();
    store.capture(owner, 4, [&](auto& row) { row.setRequestId({longId.data(), 513}); return true; });
    assert(store.attempts() == attempts + 1);
    assert(is(store.latest(out, owner, 4), "command_template_too_long"));
    store.capture(owner, 4, [&](auto& row) { row.setRequestId({longId.data(), 512}); return true; });
    assert(!store.latest(out, owner, 4) && out.requestIdSize == 512);
    store.capture(owner, 4, [](auto&) -> bool { throw 42; });
    assert(is(store.latest(out, owner, 4), "command_template_fault"));
    store.capture({}, 4, [](auto&) { assert(false); return true; });
    assert(is(store.latest(out, owner, 4), "command_template_stale"));
    auto noOwner = owner;
    noOwner.valid = 0;
    store.capture(noOwner, 4, [](auto&) { assert(false); return true; });
    assert(is(store.latest(out, owner, 4), "command_template_owner_mismatch"));
    assert(allocations.load() == before);
}

PacketInjectionQuery query() {
    PacketInjectionQuery q;
    q.owner = q.origin.owner = owner;
    q.generation = q.origin.generation = 4;
    q.deadline = 2000;
    q.id = 1;
    return q;
}

void operations() {
    auto q = query();
    assert(!packetInjectionAdmission(q, 4, 3, true, 1999));
    assert(is(packetInjectionAdmission(q, 4, 3, true, 2000), "packet_inject_timeout"));
    assert(is(packetInjectionAdmission(q, 5, 3, true, 0), "world_left"));
    assert(is(packetInjectionAdmission(q, 4, 4, true, 0), "world_left"));
    assert(is(packetInjectionAdmission(q, 4, 3, false, 0), "world_left"));
    ++q.origin.owner.uuidA;
    assert(is(packetInjectionAdmission(q, 4, 3, true, 0), "command_template_owner_mismatch"));
    q = query();
    q.origin.generation++;
    assert(is(packetInjectionAdmission(q, 4, 3, true, 0), "command_template_stale"));
    q = query();
    unsigned prepared = 0, sent = 0;
    std::uint64_t now = 1;
    auto admit = [&] { return packetInjectionAdmission(q, 4, 3, true, now); };
    auto prepare = [&]() -> char const* { ++prepared; return nullptr; };
    auto send = [&] { ++sent; };
    auto fault = [](PacketInjectionTest) {};
    auto const before = allocations.load();
    PacketInjectionSnapshot out;
    applyPacketInjection(out, admit, prepare, send, fault);
    assert(prepared == 1 && sent == 1 && out.sent && out.sendStarted && !out.error);
    out = {};
    applyPacketInjection(out, admit, [&]() -> char const* { now = 2000; return nullptr; }, send, fault);
    assert(is(out.error, "packet_inject_timeout") && !out.sendStarted && sent == 1);
    now = 1;
    out = {};
    try {
        applyPacketInjection(out, admit, prepare, send, [](auto point) {
            if (point == PacketInjectionTest::AfterSend) throw 42;
        });
        assert(false);
    } catch (int) {}
    assert(out.sent && sent == 2); // Cannot undo or retry a send that already happened.
    assert(allocations.load() == before);
}

void lifecycle() {
    PacketInjectionCapture capture;
    auto old = capture.begin(query(), 0);
    assert(old && !capture.begin(query(), 0) && old->dispatch());
    auto expired = capture.take(2000);
    assert(is(expired.error, "capture_timeout") && expired.release());
    auto fresh = capture.begin(query(), 2001);
    bool called = false;
    old->run(PacketInjectionStage::Client, 2001, [&](auto&) { called = true; return PacketInjectionStage::Complete; });
    assert(!called && fresh->dispatch());
    capture.cancel("world_left");
    fresh->run(PacketInjectionStage::Client, 2002, [&](auto&) { called = true; return PacketInjectionStage::Complete; });
    assert(!called);
    auto left = capture.take(2003);
    assert(is(left.error, "world_left") && left.release());
    auto active = capture.begin(query(), 3000);
    assert(active->dispatch());
    std::atomic_bool running{false}, proceed{false};
    std::thread client([&] {
        active->run(PacketInjectionStage::Client, 3001, [&](auto& out) {
            running = true;
            while (!proceed.load()) std::this_thread::yield();
            out.sent = true;
            return PacketInjectionStage::Complete;
        });
    });
    while (!running.load()) std::this_thread::yield();
    auto timeout = capture.take(5000);
    assert(is(timeout.error, "capture_timeout") && timeout.release()); // Don't read snapshot.
    auto next = capture.begin(query(), 5001);
    proceed = true;
    client.join();
    assert(capture.current() == next && !capture.take(5002).request);
    capture.reset();
    assert(next->dispatch());
    next->run(PacketInjectionStage::Client, 5003, [&](auto&) { called = true; return PacketInjectionStage::Complete; });
    assert(!called && !capture.take(5004).request);
}

void concurrent_templates() {
    CommandTemplateStore store;
    assert(store.initialize());
    std::atomic_bool stop{false};
    std::thread reader([&] {
        CommandTemplate out;
        while (!stop.load()) if (!store.latest(out, owner, 4)) {
            assert(out.playerId == static_cast<std::int64_t>(out.uuidA));
            assert(out.uuidB == ~out.uuidA && out.requestIdSize == 0);
        }
    });
    for (unsigned i = 1; i <= 20000; ++i) store.capture(owner, 4, [&](auto& out) {
        out.uuidA = i;
        out.uuidB = ~out.uuidA;
        out.playerId = i;
        return true;
    });
    stop = true;
    reader.join();
    assert(store.attempts() == 20000);
}
int main() { templates(); operations(); lifecycle(); concurrent_templates(); }
