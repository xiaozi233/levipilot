#include "mod/Internal.h"
#include "ll/api/event/command/ServerCommandRegisterEvent.h"
#include "ll/api/thread/GlobalThreadPauser.h"
#include <tlhelp32.h>

using namespace probe::detail;

namespace probe {

nlohmann::json ProbeMod::lifecycleBusy() {
    auto const     tasks    = taskStatusJson();
    RequestBlock req;
    InputLifecycleSnapshot controls;
    bool const requestReady = mRequests.read(req);
    bool const snapshotReady = mInputSession.lifecycle.read(controls, mPoseEpoch.load());
    bool const pending = !requestReady || !snapshotReady
              || controls.pending(req.inputSeq, req.jumpSeq, req.lookSeq);
    nlohmann::json busy     = {
        {"held_mask", mInputSession.controlHeldMask.load()},
        {"injecting_input", mInputSession.injecting.load()},
        {"injecting_look", mInputSession.injectingLook.load() != 0},
        {"cleanup_pending", mInputSession.controlCleanupPending.load()},
        {"snapshot_ready", snapshotReady},
        {"requests_pending", pending},
        {"movement_request", controls.inputTicks != 0},
        {"look_request", controls.lookTicks != 0},
        {"jump_pending", controls.jumpPending != 0},
        {"held_events", controls.heldEvents},
        {"events_pending", mInputSession.events.pending()},
        {"macro_playing", mMacroSession.playback.active()},
        {"macro_recording", mMacroSession.recording.active()},
        {"record_active", recordActive()},
        {"task", tasks.value("active", nlohmann::json())},
        {"tick_recovery", mTickRecovery.active()},
    };
    bool const inputIdle = !mWorld.inWorld() || (requestReady && snapshotReady && !pending && !controls.active());
    busy["idle"] = inputIdle && busy["held_mask"] == 0 && !busy["injecting_input"] && !busy["injecting_look"]
        && !busy["cleanup_pending"] && busy["events_pending"] == 0 && !busy["macro_playing"]
        && !busy["macro_recording"] && !busy["record_active"] && busy["task"].is_null() && !busy["tick_recovery"];
    return busy;
}

nlohmann::json ProbeMod::callbackStatus() const {
    nlohmann::json domains = nlohmann::json::object();
    for (unsigned i = 0; i < kCallbackDomains; ++i) {
        auto n = gCallbacks.counts(static_cast<CallbackDomain>(i));
        domains[kCallbackNames[i]] = {{"owned", n.queued}, {"running", n.running},
            {"entered", n.entered}, {"completed", n.completed},
            {"test_entered", mCallbackTestEntered[i].load()}, {"test_completed", mCallbackTestCompleted[i].load()}};
    }
    return {{"closed", gCallbacks.closed()}, {"refusal", mUnloadRefusal.load()}, {"domains", std::move(domains)}};
}

void ProbeMod::callbackTestDelay(CallbackDomain domain) {
    auto const i = static_cast<unsigned>(domain);
    // High bit arms a delay for the next successful command-packet send.
    auto ms = mCallbackDelayMs[i].load();
    do {
        if (!ms || (ms & (1u << 31))) return;
    } while (!mCallbackDelayMs[i].compare_exchange_weak(ms, 0));
    ++mCallbackTestEntered[i];
    Sleep(ms); // Explicit stress command only; normal callbacks never wait.
    ++mCallbackTestCompleted[i];
}

bool ProbeMod::attachListeners(bool on) {
    auto& bus = ll::event::EventBus::getInstance();
    bool ok = true;
    auto apply = [&](auto const& listener, auto const& eventId) {
        if (!listener) return;
        if (on) {
            if (!bus.hasListener(listener->getId(), eventId)) bus.addListener(listener, eventId);
        } else bus.removeListener(listener, eventId);
        ok = (bus.hasListener(listener->getId(), eventId) == on) && ok;
    };
    apply(mMouseListener, ll::event::getEventId<ll::event::input::MouseInputEvent>);
    apply(mKeyListener, ll::event::getEventId<ll::event::input::KeyInputEvent>);
    apply(mServerCommandListener, ll::event::getEventId<ll::event::command::ServerCommandRegisterEvent>);
    return mWorld.attachListeners(on) && ok;
}

bool ProbeMod::listenersReleased() const {
    // EventBus::publish keeps shared_ptr copies outside its lock. Retain our
    // owner during detach, so a snapshot waiting to call us is visible here.
    return mMouseListener.use_count() <= 1 && mKeyListener.use_count() <= 1
        && mServerCommandListener.use_count() <= 1 && mWorld.listenersReleased();
}

namespace {
// An entry counter retires a few instructions before a callback's actual ret.
// After sources are detached, also reject while any other game thread has a
// frame in this DLL. Walk one suspended thread at a time, without heap/locks.
// Workers are excluded because disable joins them before returning to LL.
bool stackOutsideModule(CONTEXT context, std::uintptr_t begin, std::uintptr_t end) noexcept {
    __try {
        for (unsigned n = 0; n < 512 && context.Rip; ++n) {
            if (context.Rip >= begin && context.Rip < end) return false;
            auto const oldSp = context.Rsp;
            DWORD64 image = 0;
            if (auto entry = RtlLookupFunctionEntry(context.Rip, &image, nullptr)) {
                PVOID data = nullptr;
                DWORD64 frame = 0;
                RtlVirtualUnwind(UNW_FLAG_NHANDLER, image, context.Rip, entry, &context, &data, &frame, nullptr);
            } else {
                SIZE_T read = 0;
                if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(context.Rsp), &context.Rip,
                                       sizeof(context.Rip), &read) || read != sizeof(context.Rip)) return false;
                context.Rsp += sizeof(context.Rip);
            }
            if (context.Rsp <= oldSp) return false;
        }
        return context.Rip == 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

bool callbackReturnsComplete(DWORD pipeTid, DWORD spoolTid, bool entryFence = false) {
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&callbackReturnsComplete), &module)) return false;
    auto const begin = reinterpret_cast<std::uintptr_t>(module);
    auto const dos = reinterpret_cast<IMAGE_DOS_HEADER const*>(module);
    auto const nt = reinterpret_cast<IMAGE_NT_HEADERS const*>(begin + dos->e_lfanew);
    auto const end = begin + nt->OptionalHeader.SizeOfImage;
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return false;
    THREADENTRY32 entry{};
    entry.dwSize = sizeof(entry);
    bool ok = Thread32First(snapshot, &entry) != FALSE;
    if (ok) do {
        if (entry.th32OwnerProcessID != GetCurrentProcessId() || entry.th32ThreadID == GetCurrentThreadId()
            || entry.th32ThreadID == pipeTid || entry.th32ThreadID == spoolTid) continue;
        HANDLE thread = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_LIMITED_INFORMATION,
                                   FALSE, entry.th32ThreadID);
        if (!thread) { ok = GetLastError() == ERROR_INVALID_PARAMETER; if (!ok) break; else continue; }
        auto const suspendCount = SuspendThread(thread);
        if (suspendCount == static_cast<DWORD>(-1)) {
            DWORD exitCode = STILL_ACTIVE;
            ok = GetExitCodeThread(thread, &exitCode) && exitCode != STILL_ACTIVE;
        } else {
            CONTEXT context{};
            context.ContextFlags = CONTEXT_FULL;
            ok = GetThreadContext(thread, &context);
            // During the source fence, LL must already have paused this thread.
            // Check the pre-counter entry / post-counter return instructions,
            // without calling the unwinder while all other threads are paused.
            if (ok) ok = entryFence ? suspendCount > 0 && !(context.Rip >= begin && context.Rip < end)
                                   : stackOutsideModule(context, begin, end);
            if (ResumeThread(thread) == static_cast<DWORD>(-1)) ok = false;
        }
        CloseHandle(thread);
        if (!ok) break;
    } while (Thread32Next(snapshot, &entry));
    CloseHandle(snapshot);
    return ok;
}
} // namespace

bool ProbeMod::drainCallbacks() {
    auto refuse = [&](char const* reason) {
        mUnloadRefusal.store(reason);
        bootLog(std::string("disable: refused reason=") + reason);
        return false;
    };
    if (CallbackLifecycle::insideCallback()) return refuse("callback_self");
    if (mCallbackTestActive.load()) return refuse("callback_test_active");
    bool const wasQuiescing = mQuiescing.exchange(true);
    if (!gCallbacks.close()) { mQuiescing = wasQuiescing; return refuse("drain_in_progress"); }
    auto reopen = [&] { gCallbacks.reopen(); mQuiescing = wasQuiescing; };
    bool const key = mLifecycleKeyHooked.load(), mouse = mLifecycleMouseHooked.load();
    bool const player = mLifecyclePlayerHooked.load(), turn = mLifecycleTurnHooked.load();
    bool detached = false;
    auto rollback = [&] {
        bool ok = true;
        if (detached) {
            try { ok = setLifecycleKeyHook(key) && ok; } catch (...) { ok = false; }
            try { ok = setLifecycleMouseHook(mouse) && ok; } catch (...) { ok = false; }
            try { ok = setLifecyclePlayerHook(player) && ok; } catch (...) { ok = false; }
            try { ok = setLifecycleTurnHook(turn) && ok; } catch (...) { ok = false; }
            try { watchInit(); ok = watchReady() && ok; } catch (...) { ok = false; }
            try { ok = attachListeners(true) && ok; } catch (...) { ok = false; }
        }
        reopen();
        bootLog("disable: drain rollback sources=" + std::to_string(ok));
        return ok;
    };
    try {
        if (!lifecycleBusy().value("idle", false)) { reopen(); return refuse("busy"); }
        // These sessions cannot currently be restored transactionally. Refuse
        // before removing anything; their entry scopes are nevertheless tracked.
        if (diagnosticCallbacksInstalled() || mPackets.hooksInstalled()) {
            reopen(); return refuse("diagnostic_sources_active");
        }
        bootLog("disable: drain snapshot " + callbackStatus().dump());
        auto const deadline = GetTickCount64() + 1500;
        auto remaining = [&] {
            auto const now = GetTickCount64();
            return std::chrono::milliseconds(deadline > now ? deadline - now : 0);
        };
        if (!gCallbacks.wait(remaining(), [] { return true; })) { reopen(); return refuse("callback_timeout"); }
        if (diagnosticCallbacksInstalled() || mPackets.hooksInstalled()) {
            reopen(); return refuse("diagnostic_sources_active");
        }
        if (!lifecycleBusy().value("idle", false)) { reopen(); return refuse("busy"); }
        detached = true;
        bool const listeners = attachListeners(false);
        DWORD const pipeTid = mServerThread.joinable() ? GetThreadId(mServerThread.native_handle()) : 0;
        DWORD const spoolTid = mSpoolThread.joinable() ? GetThreadId(mSpoolThread.native_handle()) : 0;
        bool hooks = false;
        bool attempted = false;
        do {
            {
                // Freeze ingress and check again in the SAME pause window as
                // unhook. A live source can enter after an earlier idle read.
                ll::thread::GlobalThreadPauser pause;
                if (gCallbacks.idle() && callbackReturnsComplete(pipeTid, spoolTid, true)) {
                    attempted = true;
                    hooks = stopLifecycleHooks(false);
                }
            }
            if (attempted || GetTickCount64() >= deadline) break;
            Sleep(1);
        } while (true);
        bool const veh = watchShutdown();
        bool const drained = listeners && hooks && veh && gCallbacks.wait(remaining(), [&] {
            return listenersReleased() && callbackReturnsComplete(pipeTid, spoolTid);
        });
        if (!drained) {
            bool const restored = rollback();
            return refuse(restored ? "callback_source_timeout" : "callback_rollback_failed");
        }
    } catch (...) {
        bool const restored = rollback();
        return refuse(restored ? "callback_drain_failed" : "callback_rollback_failed");
    }
    mUnloadRefusal.store("");
    bootLog("disable: callbacks drained");
    return true;
}
} // namespace probe
