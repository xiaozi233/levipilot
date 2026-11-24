#pragma once

#include "mod/QueryModel.h"
#include "mod/StagedCapture.h"

namespace probe::detail {

enum class EcsOperation { Entity, Components, Component, Registries, Context, ContextBatch };
enum class EcsStage { Client, Server, Complete };
enum class EcsScope { Client, Server, All };

struct EcsQuery {
    static constexpr std::size_t MaxBatchItems = 8, MaxBatchBytes = 64;
    struct BatchItem {
        std::uint32_t componentId = 0, offset = 0, length = 0;
    };
    EcsOperation                         operation = EcsOperation::Entity;
    EcsScope                             scope     = EcsScope::Client;
    std::uint64_t                        runtimeId = 0, context = 0;
    std::uint32_t                        componentId = 0, length = 0;
    CaptureTest                          test      = CaptureTest::None;
    EcsStage                             testStage = EcsStage::Client;
    std::array<BatchItem, MaxBatchItems> batchItems{};
    std::size_t                          batchCount = 0;
};

struct EcsSnapshot {
    static constexpr std::size_t MaxRows = 1024, MaxVisited = 4096;
    static constexpr std::size_t MaxBatchItems = EcsQuery::MaxBatchItems, MaxBatchBytes = EcsQuery::MaxBatchBytes;
    struct Row {
        std::uint32_t id = 0, stride = 0;
        std::uint64_t index = 0, address = 0, count = 0;
    } component;
    struct BatchResult {
        std::uint32_t                           id = 0, offset = 0, length = 0;
        std::uint64_t                           address = 0;
        bool                                    present = false, readOk = false;
        std::array<std::uint8_t, MaxBatchBytes> bytes{};
    };
    struct RegistryEntry {
        std::uint64_t registry = 0, entity = 0;
        std::size_t   components = 0;
        bool          present    = false;
    } clientRegistry, serverRegistry;
    std::array<Row, MaxRows>               rows{};
    std::array<std::uint32_t, MaxRows>     skipped{};
    std::array<std::uint8_t, 512>          bytes{};
    std::array<BatchResult, MaxBatchItems> batchResults{};
    std::size_t                            batchCount = 0;
    std::uint64_t                          tick       = 0;
    FixedText<256>                         type;
    std::size_t                            count = 0, skippedCount = 0, visited = 0, priorVisited = 0;
    std::uint64_t                          registry = 0, entity = 0, actor = 0, context = 0, playerRuntimeId = 0;
    std::uint32_t                          length = 0;
    bool                                   server = false, truncated = false, ok = false;
    char const*                            error = nullptr;

    bool add(Row const& row) {
        if (count == rows.size()) {
            truncated = true;
            return false;
        }
        rows[count++] = row;
        return true;
    }
    bool skip(std::uint32_t id) {
        if (skippedCount == skipped.size()) {
            truncated = true;
            return false;
        }
        skipped[skippedCount++] = id;
        return true;
    }
};

// Storage iteration is bounded independently of the result count: a registry
// with many absent components must not bypass the work budget. Probe owns the
// per-storage game boundary and reports presence separately from failure.
template <class Range, class Probe>
void collectEcsRows(EcsOperation operation, EcsSnapshot& out, Range&& storages, Probe&& probe) {
    for (auto&& [id, storage] : storages) {
        if (out.visited == EcsSnapshot::MaxVisited) {
            out.truncated = true;
            break;
        }
        ++out.visited;
        if (operation == EcsOperation::Registries) {
            ++out.count;
            continue;
        }
        EcsSnapshot::Row row;
        row.id       = static_cast<std::uint32_t>(id);
        bool present = false, valid = false;
        try {
            valid = probe(storage, row, present);
        } catch (...) {}
        if (!valid) {
            if (!out.skip(row.id)) break;
        } else if (present) {
            if (operation == EcsOperation::Entity) ++out.count;
            else if (!out.add(row)) break;
        }
    }
}

using EcsCapture = StagedCapture<EcsQuery, EcsSnapshot, EcsStage>;

} // namespace probe::detail
