#pragma once

#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"

namespace fs = std::filesystem;

struct RegionWriter {
    int rx = 0;
    int rz = 0;
    std::map<int, std::vector<uint8_t>> chunks;

    RegionWriter(int regionX, int regionZ);
    void addChunk(int cx, int cz, std::vector<uint8_t>&& nbtPayload);
    void write(const fs::path& dir) const;
};

struct RegionBucket {
    RegionWriter writer;
    std::mutex mutex;

    RegionBucket(int regionX, int regionZ) : writer(regionX, regionZ) {}
    RegionBucket(const RegionBucket&) = delete;
    RegionBucket& operator=(const RegionBucket&) = delete;
};

bool regionChunkInAnyArea(int cx, int cz, const std::vector<Area>& areas, const Area** outArea);

std::vector<uint8_t> generateChunkPayload(const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                                          const Area& area, int cx, int cz,
                                          std::vector<uint16_t>& blockIds, std::vector<uint16_t>& biomeIds);

int generateRegion(const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                   const std::vector<Area>& areas, const RegionJob& job, const fs::path& regionDir,
                   const std::function<void(int, int)>& onChunkDone = {});

void reportProgress(double p, const std::string& msg);

void reportPreviewReady();

int spawnY(const Config& cfg);

void writeResultJson(const Config& cfg, const fs::path& outDir, int chunks, int regions, int workers, double seconds);
