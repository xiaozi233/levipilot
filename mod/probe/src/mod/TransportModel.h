#pragma once

#include <cstddef>
#include <string>
#include <string_view>

namespace probe::detail {

// Pipe-thread owned; only the unfinished line is retained between reads.
// Callbacks consume the line synchronously and must not retain its reference.
template <std::size_t MaxLineBytes = (1u << 20)> class CommandFramer {
  public:
    void reset() {
        mPartial.clear();
        mDiscard = false;
    }

    template <class OnLine, class OnTooLong> void feed(std::string_view chunk, OnLine&& onLine, OnTooLong&& onTooLong) {
        while (!chunk.empty()) {
            auto const newline = chunk.find('\n');
            auto const part = chunk.substr(0, newline);
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
    bool discarding() const { return mDiscard; }

  private:
    std::string mPartial;
    bool mDiscard = false;
};

// Reuse a bounded scratch buffer across iterations. Large rows already own
// their storage and can be written directly, avoiding a second full copy.
template <std::size_t MaxBatchBytes, class Rows, class Write>
bool writeLineBatch(Rows& rows, std::string& buffer, Write&& write) {
    static_assert(MaxBatchBytes > 0);
    buffer.clear();
    auto flush = [&] {
        if (buffer.empty())
            return true;
        if (!write(buffer))
            return false;
        buffer.clear();
        return true;
    };
    for (auto& line : rows) {
        if (line.size() >= MaxBatchBytes) {
            if (!flush())
                return false;
            line.push_back('\n');
            if (!write(line))
                return false;
        } else {
            if (line.size() + 1 > MaxBatchBytes - buffer.size() && !flush())
                return false;
            buffer.append(line);
            buffer.push_back('\n');
        }
    }
    return flush();
}

} // namespace probe::detail
