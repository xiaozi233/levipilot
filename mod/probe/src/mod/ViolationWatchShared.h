#pragma once

#include "mod/ViolationWatchModel.h"

#include <nlohmann/json.hpp>

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>

namespace probe::detail {

constexpr std::uint8_t kMemmoveKind      = 9;
constexpr std::uint8_t kDispatchKind     = 10;
constexpr std::uint8_t kAssignCallerKind = 11;

struct MemmoveBlob {
    std::uint64_t src = 0;
    std::uint64_t len = 0;
    char          before[16]{};
    char          after[16]{};
};

extern std::mutex gViolationInstallMutex;

nlohmann::json errorJson(char const* reason);
std::string    hexText(char const* data, std::size_t size);
std::string    printableText(char const* data, std::size_t size);
void           publishText(std::uint8_t kind, std::string_view value);
bool           initializeTextRing();

void           inboundWatchFreeze();
void           inboundWatchReset();
nlohmann::json inboundWatchStart(std::uint64_t loopAddress, std::uint64_t assignAddress, std::uint64_t dispatchAddress);
nlohmann::json inboundWatchStop();
void           inboundWatchStats(nlohmann::json& out);

inline bool plausiblePointer(std::uint64_t value) { return value >= 0x10000 && value < 0x00007FFFFFFFFFFFull; }

inline char const* violationLabel(int response) {
    switch (response) {
    case 0:
        return "none";
    case 1:
        return "warning";
    case 2:
        return "final_warning";
    case 3:
        return "terminate_connection";
    default:
        return "unknown";
    }
}

// Cold path: mirror PacketTap::drain so callers can reuse the same cursor idiom.
template <class Ring>
nlohmann::json drainRing(Ring& ring, std::size_t capacity, std::uint64_t since, std::size_t limit, char const* event) {
    std::uint64_t const head   = ring.head();
    std::uint64_t const oldest = head > capacity ? head - capacity : 0;
    std::uint64_t       start  = since;
    std::uint64_t       lost   = 0;
    if (start < oldest) {
        lost  = oldest - start;
        start = oldest;
    }
    if (start > head) start = head;
    if (limit == 0) limit = 256;
    nlohmann::json rows   = nlohmann::json::array();
    std::size_t    count  = 0;
    std::uint64_t  cursor = start;
    for (std::uint64_t index = start; index < head && count < limit; ++index) {
        ViolationRecord record{};
        auto const      read = ring.read(index, record);
        if (read == Ring::ReadResult::pending) {
            cursor = index;
            break;
        }
        if (read != Ring::ReadResult::ready) {
            ++lost;
            cursor = index + 1;
            continue;
        }
        rows.push_back(
            nlohmann::json{
                {"index", index + 1},
                {"seq", record.seq},
                {"tick", record.tick},
                {"tid", record.tid},
                {"response", record.response},
                {"verdict",
                 record.source == static_cast<std::uint8_t>(ViolationSource::Screen) ? "disconnect"
                                                                                     : violationLabel(record.response)},
                {"disconnect_reason",
                 record.source == static_cast<std::uint8_t>(ViolationSource::Screen) ? nlohmann::json(record.response)
                                                                                     : nlohmann::json(nullptr)},
                {"packet_id", record.packetId},
                {"packet_size", record.packetSize},
                {"error_value", record.errorValue},
                {"category", record.category == 0 ? nlohmann::json(nullptr) : nlohmann::json(record.category)},
                {"copies", record.pad0},
                {"assign_size", record.assignSize},
                {"source",
                 record.source == 3   ? "inbound"
                 : record.source == 2 ? "screen"
                                      : "callback"},
                {"context_hex", hexText(record.context, record.contextSize)},
                {"detail_hex", hexText(record.detail, record.detailSize)},
                {"before_hex", hexText(record.before, record.beforeSize)},
                {"parent_view", record.parentView},
                {"parent_size", record.parentSize},
                {"parent_read_ptr", record.parentReadPtr},
                {"context", printableText(record.context, record.contextSize)},
                {"detail", printableText(record.detail, record.detailSize)},
                {"detail_truncated", record.detailTruncated != 0},
                {"context_size", record.contextSize},
                {"context_truncated", record.contextTruncated != 0},
                {"synthetic", record.synthetic != 0},
        }
        );
        ++count;
        cursor = index + 1;
    }
    return nlohmann::json{
        {"event", event          },
        {"since", since          },
        {"next",  cursor         },
        {"head",  head           },
        {"lost",  lost           },
        {"count", count          },
        {"hits",  std::move(rows)},
    };
}

} // namespace probe::detail
