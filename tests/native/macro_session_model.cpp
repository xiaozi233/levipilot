#include "mod/MacroSessionModel.h"

#include <cassert>
#include <cstdlib>
#include <new>

using namespace probe;
using namespace probe::detail;
std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size)) return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }

int main() {
    MacroSession session;
    session.playback.load(Macro(2), "aggregate");
    assert(session.playback.begin(0, 2, false, false));
    session.recording.begin(3, [] {});
    assert(session.playback.active() && session.recording.active());

    auto const before = allocations.load();
    session.cancelPlayback(MacroEnd::InternalError);
    assert(!session.playback.active() && session.recording.active());
    assert(allocations.load() == before);

    session.worldLeft();
    assert(session.recordWorldLeft.load() && !session.playback.active());
    assert(session.recording.active());
    assert(allocations.load() == before);

    session.stopAll(MacroEnd::Disabled, MacroRecordEnd::Disabled);
    assert(!session.playback.active() && !session.recording.active());
    assert(allocations.load() == before);
}
