#include "mod/Internal.h"

namespace probe {

void ProbeMod::requestChatHistory(int limit) {
    if (!detail::hooksAllowed()) {
        enqueue(R"({"event":"error","reason":"version_mismatch"})");
        return;
    }
    auto expected = ChatCapture::Phase::Idle;
    if (!mChatCapture.phase.compare_exchange_strong(expected, ChatCapture::Phase::Requested)) {
        enqueue(R"({"event":"error","reason":"chat_history_busy"})");
        return;
    }
    try {
        ll::thread::ClientThreadExecutor::getDefault().execute([this, limit] {
            try {
                captureChatHistory(limit);
            } catch (...) {
                mChatCapture.count = 0;
                mChatCapture.error = "chat_history_failed";
            }
            mChatCapture.phase.store(ChatCapture::Phase::Ready, std::memory_order_release);
        });
    } catch (...) {
        mChatCapture.phase.store(ChatCapture::Phase::Idle, std::memory_order_release);
        throw;
    }
}

void ProbeMod::captureChatHistory(int limit) {
    // A bounded, allocation-free snapshot on the UI/client thread. Prefer the
    // filtered displayed text; do not expose XUIDs or unfiltered chat content.
    mChatCapture.count = 0;
    mChatCapture.total = 0;
    mChatCapture.error = nullptr;
    __try {
        auto client = ll::service::getClientInstance();
        if (!client) {
            mChatCapture.error = "no_client";
            return;
        }
        auto const& messages = *client->getGuiData()->mGuiMessages;
        mChatCapture.total   = messages.size();
        auto const count     = std::min(messages.size(), static_cast<std::size_t>(limit));
        for (std::size_t i = messages.size() - count; i < messages.size(); ++i) {
            auto const&        message      = messages[i];
            auto const&        filteredFull = *message.mFilteredFullString;
            auto const&        filtered     = *message.mFilteredMessage;
            std::string const& full         = *message.mFullString;
            std::string const& text         = filteredFull  ? *filteredFull
                                            : filtered      ? *filtered
                                            : !full.empty() ? full
                                                            : *message.mMessage;
            auto&              line         = mChatCapture.lines[mChatCapture.count++];
            line.text.assign(text);
            line.type      = static_cast<int>(message.mType);
            line.truncated = line.text.view().size() < text.size();
        }
    } __except (
        probe::detail::isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        mChatCapture.count = 0;
        mChatCapture.error = "chat_history_fault";
    }
}

void ProbeMod::drainChatHistory() {
    if (mChatCapture.phase.load(std::memory_order_acquire) != ChatCapture::Phase::Ready) return;
    nlohmann::json result{
        {"event", "chat_history"                },
        {"ok",    mChatCapture.error == nullptr}
    };
    if (mChatCapture.error) {
        result["reason"] = mChatCapture.error;
    } else {
        result["total"]    = mChatCapture.total;
        result["messages"] = nlohmann::json::array();
        for (std::size_t i = 0; i < mChatCapture.count; ++i) {
            auto const& line = mChatCapture.lines[i];
            result["messages"].push_back({
                {"text",      line.text.c_str()},
                {"type",      line.type        },
                {"truncated", line.truncated   }
            });
        }
    }
    auto output = result.dump();
    mChatCapture.phase.store(ChatCapture::Phase::Idle, std::memory_order_release);
    enqueue(std::move(output));
}

} // namespace probe
