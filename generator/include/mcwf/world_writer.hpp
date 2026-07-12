#pragma once

#include <cstdint>
#include <filesystem>
#include <map>
#include <memory>
#include <mutex>
#include <vector>

#include "mcwf/bedrock.hpp"
#include "mcwf/config.hpp"
#include "mcwf/region.hpp"

namespace fs = std::filesystem;

struct WorldWriteBucket {
    virtual ~WorldWriteBucket() = default;
    virtual void addChunk(const Config& cfg, const Blocks& blocks, int cx, int cz,
                          std::vector<uint8_t>&& javaPayload,
                          const std::vector<uint16_t>& blockIds,
                          const std::vector<uint16_t>& biomeIds) = 0;
};

struct JavaWriteBucket final : WorldWriteBucket {
    RegionWriter writer;

    JavaWriteBucket(int regionX, int regionZ) : writer(regionX, regionZ) {}

    void addChunk(const Config& cfg, const Blocks& blocks, int cx, int cz,
                  std::vector<uint8_t>&& javaPayload,
                  const std::vector<uint16_t>& blockIds,
                  const std::vector<uint16_t>& biomeIds) override;
};

struct BedrockWriteBucket final : WorldWriteBucket {
    std::vector<BedrockKv> entries;

    void addChunk(const Config& cfg, const Blocks& blocks, int cx, int cz,
                  std::vector<uint8_t>&& javaPayload,
                  const std::vector<uint16_t>& blockIds,
                  const std::vector<uint16_t>& biomeIds) override;
};

struct SharedWorldBucket {
    std::unique_ptr<WorldWriteBucket> writer;
    std::mutex mutex;

    explicit SharedWorldBucket(std::unique_ptr<WorldWriteBucket>&& writer_) : writer(std::move(writer_)) {}
    SharedWorldBucket(const SharedWorldBucket&) = delete;
    SharedWorldBucket& operator=(const SharedWorldBucket&) = delete;
};

std::vector<std::unique_ptr<SharedWorldBucket>> createWorldBuckets(const Config& cfg, const std::vector<RegionJob>& regions);

void writeWorldBuckets(const Config& cfg, const fs::path& outDir, const fs::path& regionDir,
                       const std::vector<std::unique_ptr<SharedWorldBucket>>& buckets, const WorldSpawn& spawn);
