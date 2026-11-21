#include "mod/Internal.h"

namespace probe {

void ProbeMod::requestChatHistory(int limit, bool deferForSelftest) {
    if (!detail::hooksAllowed()) {
        enqueue(R"({"event":"error","reason":"version_mismatch"})");
        return;
    }
    auto request = mChatCapture.begin(GetTickCount64(), detail::activeReqId());
    if (!request) {
        enqueue(R"({"event":"error","reason":"chat_history_busy"})");
        return;
    }
    if (deferForSelftest) return;
    try {
        queueClientTask([this, weak = std::weak_ptr{request}, limit] {
            if (auto active = weak.lock()) {
                active->run(GetTickCount64(), [limit](auto& snapshot) { captureChatHistory(snapshot, limit); });
                wakeSpool();
            }
        });
    } catch (...) {
        request->cancel("chat_history_failed");
        wakeSpool();
    }
}

void ProbeMod::captureChatHistory(detail::ChatCapture::Snapshot& snapshot, int limit) {
    // A bounded, allocation-free snapshot on the UI/client thread. Prefer the
    // filtered displayed text; do not expose XUIDs or unfiltered chat content.
    snapshot.count = 0;
    snapshot.total = 0;
    snapshot.error = nullptr;
    __try {
        auto client = ll::service::getClientInstance();
        if (!client) {
            snapshot.error = "no_client";
            return;
        }
        if (!client->getLocalPlayer()) {
            snapshot.error = "no_player";
            return;
        }
        auto        gui      = client->getGuiData();
        auto const& messages = *gui->mGuiMessages;
        snapshot.total       = messages.size();
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
            auto&              line         = snapshot.lines[snapshot.count++];
            line.text.assign(text);
            line.type      = static_cast<int>(message.mType);
            line.truncated = line.text.view().size() < text.size();
        }
    } __except (
        probe::detail::isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        snapshot.count = 0;
        snapshot.error = "chat_history_fault";
    }
}

void ProbeMod::drainChatHistory() {
    auto completion = mChatCapture.take(GetTickCount64());
    if (!completion.request) return;
    auto const&    snapshot = completion.request->snapshot;
    auto const     error    = completion.error ? completion.error : snapshot.error;
    nlohmann::json result{
        {"event", "chat_history"  },
        {"ok",    error == nullptr}
    };
    if (error) {
        result["reason"] = error;
    } else {
        result["total"]    = snapshot.total;
        result["messages"] = nlohmann::json::array();
        for (std::size_t i = 0; i < snapshot.count; ++i) {
            auto const& line = snapshot.lines[i];
            result["messages"].push_back({
                {"text",      line.text.c_str()},
                {"type",      line.type        },
                {"truncated", line.truncated   }
            });
        }
    }
    detail::attachReqId(result, completion.request->reqId);
    auto output = result.dump();
    enqueue(std::move(output));
}

} // namespace probe
