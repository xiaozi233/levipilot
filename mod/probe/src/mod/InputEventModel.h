#pragma once

#include "mod/QueryModel.h"
#include "mod/StagedCapture.h"

#include <string>

namespace probe::detail {

enum class InputKind { Queue, Mouse, Hid, Hardware, Raw, Text };
struct InputInjection {
    InputKind kind = InputKind::Queue;
    std::uint8_t code = 0;
    std::int8_t data = 0;
    std::int16_t dx = 0, dy = 0, x = 0, y = 0;
    bool hasPos = false, repeat = false;
    CaptureTest test = CaptureTest::None;
};
struct InputOutcome {
    std::uint64_t id = 0, generation = 0;
    InputKind kind = InputKind::Queue;
    char const* error = nullptr;
};

// One cold producer/consumer (externally serialized), one tick consumer.
// Slots remain owned until their outcome is drained, so even a stalled spool
// cannot lose an acknowledgement or overwrite an event being applied.
template <std::size_t Capacity = 128>
class InjectedEventQueue {
    static_assert(Capacity > 0);
    struct Entry {
        InputInjection input;
        std::uint64_t generation = 0;
    };
public:
    std::uint64_t push(InputInjection input) {
        auto head = mHead.load(std::memory_order_relaxed);
        if (head - mDrained.load(std::memory_order_acquire) >= Capacity) return 0;
        mEntries[head % Capacity] = {input, generation()};
        mHead.store(head + 1, std::memory_order_release);
        return head + 1;
    }
    std::uint64_t nextId() const { return mHead.load(std::memory_order_acquire) + 1; }
    std::uint64_t pending() const {
        auto tail = mTail.load(std::memory_order_acquire);
        auto head = mHead.load(std::memory_order_acquire);
        return head - tail;
    }
    std::uint64_t generation() const { return mGeneration.load(std::memory_order_acquire); }
    void cancel() { mGeneration.fetch_add(1, std::memory_order_acq_rel); }
    bool textReady(std::uint64_t id) const { return mTextReady.load(std::memory_order_acquire) == id; }
    void completeText(std::uint64_t id) {
        auto previous = mTextDone.load(std::memory_order_relaxed);
        while (previous < id && !mTextDone.compare_exchange_weak(previous, id, std::memory_order_release)) {}
    }

    template <class Apply>
    void tick(Apply&& apply, std::uint64_t expectedGeneration = 0) {
        auto tail = mTail.load(std::memory_order_relaxed);
        auto const first = tail;
        auto const head = mHead.load(std::memory_order_acquire);
        while (tail != head) {
            if (expectedGeneration && generation() != expectedGeneration) return;
            auto const entry = mEntries[tail % Capacity];
            InputOutcome result{tail + 1, entry.generation, entry.input.kind};
            if (entry.generation != generation()) result.error = "input_cancelled";
            else if (entry.input.kind == InputKind::Text) {
                if (mTextDone.load(std::memory_order_acquire) < result.id) {
                    // Allow the game to process preceding key edges before
                    // dispatching text. Following Enter cannot pass this row.
                    if (tail == first) mTextReady.store(result.id, std::memory_order_release);
                    return;
                }
            } else {
                try { result.error = apply(entry.input); }
                catch (...) { result.error = "input_failed"; }
            }
            mResults[tail % Capacity] = result;
            mTail.store(++tail, std::memory_order_release);
        }
    }
    bool pop(InputOutcome& result) {
        auto drained = mDrained.load(std::memory_order_relaxed);
        if (drained == mTail.load(std::memory_order_acquire)) return false;
        result = mResults[drained % Capacity];
        mDrained.store(drained + 1, std::memory_order_release);
        return true;
    }
private:
    std::array<Entry, Capacity> mEntries{};
    std::array<InputOutcome, Capacity> mResults{};
    std::atomic<std::uint64_t> mHead{0}, mTail{0}, mDrained{0}, mGeneration{1};
    std::atomic<std::uint64_t> mTextReady{0}, mTextDone{0};
};

enum class UiOperation { Screen, KeyState };
enum class InputStage { Client, Flush, Complete };
struct UiQuery {
    UiOperation operation = UiOperation::Screen;
    int code = 0;
    CaptureTest test = CaptureTest::None;
};
struct UiSnapshot {
    FixedText<256> name;
    bool inWorld = false, down = false, truncated = false;
    std::uint64_t events = 0, injectedSeen = 0, pending = 0;
    int lastCode = -1, lastAction = -1;
    char const* error = nullptr;
};
struct TextQuery {
    std::string text;
    std::uint64_t id = 0, generation = 0;
    CaptureTest test = CaptureTest::None;
};
struct TextSnapshot {
    std::uint64_t resumeTick = 0;
    char const* error = nullptr;
};
using UiCapture = StagedCapture<UiQuery, UiSnapshot, InputStage>;
using TextCapture = StagedCapture<TextQuery, TextSnapshot, InputStage>;

} // namespace probe::detail
