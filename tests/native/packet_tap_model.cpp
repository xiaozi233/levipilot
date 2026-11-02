#include "mod/PacketTapModel.h"
#include "mod/AtomicRing.h"

#include <atomic>
#include <cassert>
#include <cstdlib>
#include <new>
#include <string>

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
    PacketByteRecord row{};
    row.order = 42;
    row.id = 188;
    std::string payload(129, 'x');
    auto const before = allocations.load();
    copyPacketBytes(row, payload);
    assert(row.size == 129 && row.data[127] == 'x');
    assert(allocations.load() == before);

    bool busy = false;
    assert(PacketSerializationGuard::enter(busy));
    assert(!PacketSerializationGuard::enter(busy));
    { PacketSerializationGuard scope{busy}; assert(busy); }
    assert(!busy);

    AtomicRing<PacketByteRecord, 2> ring;
    assert(ring.initialize());
    auto const ringBefore = allocations.load();
    assert(ring.reserve().publish(row));
    PacketByteRecord read{};
    assert((ring.read(0, read) == AtomicRing<PacketByteRecord, 2>::ReadResult::ready));
    assert(read.order == 42 && read.size == 129 && read.data[127] == 'x');
    assert(allocations.load() == ringBefore);
}
