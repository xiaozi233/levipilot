#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>

namespace probe::detail {

inline constexpr std::size_t MaxQueueCount       = 20000;
inline constexpr std::size_t MaxQueueBytes       = 32u << 20;
inline constexpr std::size_t MaxSpoolBufferBytes = 16u << 20;

inline bool commandAllowedBeforeAuth(std::string_view cmd) noexcept { return cmd == "auth" || cmd == "ping"; }

inline bool verifyPipeToken(std::string_view expected, std::string_view candidate) noexcept {
    if (expected.empty()) {
        return true;
    }
    if (candidate.size() != expected.size()) {
        return false;
    }
    unsigned char diff = 0;
    for (std::size_t i = 0; i < expected.size(); ++i) {
        diff |= static_cast<unsigned char>(candidate[i]) ^ static_cast<unsigned char>(expected[i]);
    }
    return diff == 0;
}

inline std::string attachReqId(std::string line, std::uint64_t reqId) {
    if (reqId == 0 || line.size() < 2 || line.front() != '{' || line.back() != '}') {
        return line;
    }
    if (line.find("\"req_id\":") != std::string::npos) {
        return line;
    }
    std::string suffix  = line.size() == 2 ? "\"req_id\":" : ",\"req_id\":";
    suffix             += std::to_string(reqId);
    suffix.push_back('}');
    line.pop_back();
    line.append(suffix);
    return line;
}

template <class Queue>
std::uint64_t pushBoundedLine(
    Queue&       queue,
    std::size_t& queueBytes,
    std::string  line,
    std::size_t  maxCount = MaxQueueCount,
    std::size_t  maxBytes = MaxQueueBytes
) {
    std::uint64_t     dropped  = 0;
    std::size_t const nextSize = line.size();
    while (!queue.empty() && (queue.size() >= maxCount || nextSize > maxBytes || queueBytes > maxBytes - nextSize)) {
        std::size_t const frontSize = queue.front().size();
        queueBytes                  = queueBytes >= frontSize ? queueBytes - frontSize : 0;
        queue.pop_front();
        ++dropped;
    }
    queueBytes += nextSize;
    queue.push_back(std::move(line));
    return dropped;
}

// Pipe-thread owned; only the unfinished line is retained between reads.
// Callbacks consume the line synchronously and must not retain its reference.
template <std::size_t MaxLineBytes = (1u << 20)>
class CommandFramer {
public:
    void reset() {
        mPartial.clear();
        mDiscard = false;
    }

    template <class OnLine, class OnTooLong>
    void feed(std::string_view chunk, OnLine&& onLine, OnTooLong&& onTooLong) {
        while (!chunk.empty()) {
            auto const newline = chunk.find('\n');
            auto const part    = chunk.substr(0, newline);
            if (!mDiscard) {
                if (part.size() > MaxLineBytes - mPartial.size()) {
                    auto const bytes = mPartial.size() + part.size();
                    mPartial.clear();
                    mDiscard = true;
                    onTooLong(bytes);
                } else {
                    mPartial.append(part);
                }
            }
            if (newline == std::string_view::npos) {
                break;
            }
            if (!mDiscard) {
                if (!mPartial.empty() && mPartial.back() == '\r') {
                    mPartial.pop_back();
                }
                if (!mPartial.empty()) {
                    onLine(mPartial);
                }
            }
            reset();
            chunk.remove_prefix(newline + 1);
        }
    }

    std::size_t pendingBytes() const { return mPartial.size(); }
    bool        discarding() const { return mDiscard; }

private:
    std::string mPartial;
    bool        mDiscard = false;
};

// Reuse a bounded scratch buffer across iterations. Large rows already own
// their storage and can be written directly, avoiding a second full copy.
template <std::size_t MaxBatchBytes, class Rows, class Write>
bool writeLineBatch(Rows& rows, std::string& buffer, Write&& write) {
    static_assert(MaxBatchBytes > 0);
    buffer.clear();
    auto flush = [&] {
        if (buffer.empty()) return true;
        if (!write(buffer)) return false;
        buffer.clear();
        return true;
    };
    for (auto& line : rows) {
        if (line.size() >= MaxBatchBytes) {
            if (!flush()) return false;
            line.push_back('\n');
            if (!write(line)) return false;
        } else {
            if (line.size() + 1 > MaxBatchBytes - buffer.size() && !flush()) return false;
            buffer.append(line);
            buffer.push_back('\n');
        }
    }
    return flush();
}

} // namespace probe::detail
