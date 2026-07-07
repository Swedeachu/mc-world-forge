#include "mcwf/world_writer.hpp"

#include <stdexcept>

void JavaWriteBucket::addChunk(const Config& cfg, const Blocks& blocks, int cx, int cz,
                               std::vector<uint8_t>&& javaPayload,
                               const std::vector<uint16_t>& blockIds,
                               const std::vector<uint16_t>& biomeIds) {
    (void)cfg;
    (void)blocks;
    (void)blockIds;
    (void)biomeIds;
    writer.addChunk(cx, cz, std::move(javaPayload));
}

void BedrockWriteBucket::addChunk(const Config& cfg, const Blocks& blocks, int cx, int cz,
                                  std::vector<uint8_t>&& javaPayload,
                                  const std::vector<uint16_t>& blockIds,
                                  const std::vector<uint16_t>& biomeIds) {
    (void)javaPayload;
    appendBedrockChunk(cfg, blocks, cx, cz, blockIds, biomeIds, entries);
}

std::vector<std::unique_ptr<SharedWorldBucket>> createWorldBuckets(const Config& cfg, const std::vector<RegionJob>& regions) {
    std::vector<std::unique_ptr<SharedWorldBucket>> buckets;
    buckets.reserve(regions.size());

    for (const RegionJob& r : regions) {
        if (cfg.outputFormat == "bedrock") {
            buckets.push_back(std::make_unique<SharedWorldBucket>(std::make_unique<BedrockWriteBucket>()));
        } else {
            buckets.push_back(std::make_unique<SharedWorldBucket>(std::make_unique<JavaWriteBucket>(r.rx, r.rz)));
        }
    }

    return buckets;
}

void writeWorldBuckets(const Config& cfg, const fs::path& outDir, const fs::path& regionDir,
                       const std::vector<std::unique_ptr<SharedWorldBucket>>& buckets) {
    if (cfg.outputFormat == "bedrock") {
        std::vector<BedrockKv> entries;
        size_t total = 0;

        for (const auto& bucket : buckets) {
            auto* bedrock = dynamic_cast<BedrockWriteBucket*>(bucket->writer.get());
            if (bedrock == nullptr) {
                throw std::runtime_error("mixed world writer bucket types");
            }
            total += bedrock->entries.size();
        }

        entries.reserve(total);
        for (const auto& bucket : buckets) {
            auto* bedrock = static_cast<BedrockWriteBucket*>(bucket->writer.get());
            for (BedrockKv& entry : bedrock->entries) {
                entries.push_back(std::move(entry));
            }
        }

        writeBedrockWorld(cfg, outDir, std::move(entries));
        return;
    }

    for (const auto& bucket : buckets) {
        auto* java = dynamic_cast<JavaWriteBucket*>(bucket->writer.get());
        if (java == nullptr) {
            throw std::runtime_error("mixed world writer bucket types");
        }
        java->writer.write(regionDir);
    }
}
