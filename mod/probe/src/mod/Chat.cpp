#include "mod/Internal.h"

namespace probe {

void ProbeMod::requestChatHistory(int limit) {
    if (!detail::hooksAllowed()) {
        enqueue(R"({"event":"error","reason":"version_mismatch"})");
        return;
    }
    auto expected = ChatPhase::Idle;
    if (!mChatPhase.compare_exchange_strong(expected, ChatPhase::Requested)) {
        enqueue(R"({"event":"error","reason":"chat_history_busy"})");
        return;
    }
    try {
        ll::thread::ClientThreadExecutor::getDefault().execute([this, limit] {
            try {
                captureChatHistory(limit);
            } catch (...) {
                mChatCount = 0;
                mChatError = "chat_history_failed";
            }
            mChatPhase.store(ChatPhase::Ready, std::memory_order_release);
        });
    } catch (...) {
        mChatPhase.store(ChatPhase::Idle, std::memory_order_release);
        throw;
    }
}

void ProbeMod::captureChatHistory(int limit) {
    // A bounded, allocation-free snapshot on the UI/client thread. Prefer the
    // filtered displayed text; do not expose XUIDs or unfiltered chat content.
    mChatCount = 0;
    mChatTotal = 0;
    mChatError = nullptr;
    __try {
        auto client = ll::service::getClientInstance();
        if (!client) {
            mChatError = "no_client";
            return;
        }
        auto const& messages = *client->getGuiData()->mGuiMessages;
        mChatTotal           = messages.size();
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
            auto&              line         = mChatLines[mChatCount++];
            line.text.assign(text);
            line.type      = static_cast<int>(message.mType);
            line.truncated = line.text.view().size() < text.size();
        }
    } __except (
        probe::detail::isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        mChatCount = 0;
        mChatError = "chat_history_fault";
    }
}

void ProbeMod::drainChatHistory() {
    if (mChatPhase.load(std::memory_order_acquire) != ChatPhase::Ready) return;
    nlohmann::json result{
        {"event", "chat_history"       },
        {"ok",    mChatError == nullptr}
    };
    if (mChatError) {
        result["reason"] = mChatError;
    } else {
        result["total"]    = mChatTotal;
        result["messages"] = nlohmann::json::array();
        for (std::size_t i = 0; i < mChatCount; ++i) {
            auto const& line = mChatLines[i];
            result["messages"].push_back({
                {"text",      line.text.c_str()},
                {"type",      line.type        },
                {"truncated", line.truncated   }
            });
        }
    }
    auto output = result.dump();
    mChatPhase.store(ChatPhase::Idle, std::memory_order_release);
    enqueue(std::move(output));
}

} // namespace probe
