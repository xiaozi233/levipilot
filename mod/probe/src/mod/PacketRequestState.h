#pragma once

#include "mod/PacketInjectionModel.h"
#include "mod/QueryModel.h"

#include <mutex>

namespace probe::detail {

// Command-template capture and the exclusive client-send request share one
// cold-side lifecycle. The tick path never takes this lock.
struct PacketRequestState {
    std::atomic<std::uint64_t> chatSent{0};
    CommandTemplateStore       templates;
    PacketInjectionCapture     injection;
    std::mutex                 mutex;
    std::atomic<std::uint64_t> generation{0}, templateFaults{0};
    std::uint64_t              nextId = 0;
    std::atomic<CaptureTest>   templateTest{CaptureTest::None};
};

} // namespace probe::detail
