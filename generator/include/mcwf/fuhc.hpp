#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"

namespace fs = std::filesystem;

constexpr int FUHC_BARRIER_LAYERS = 2;

struct FuhcSpawnPoint {
    int x = 0;
    int y = 64;
    int z = 0;
};

enum FuhcBiomeGroup {
    FUHC_BIOME_PLAINS = 0,
    FUHC_BIOME_DESERT = 1,
    FUHC_BIOME_SNOW = 2,
    FUHC_BIOME_RARE = 3,
};

struct FuhcBiomeAssignment {
    int group = FUHC_BIOME_PLAINS;
    int biome = 0;
};

std::vector<FuhcBiomeAssignment> planFuhcBiomes(const Config& cfg, int arenaCount);

const char* fuhcBiomeGroupName(int group);

const char* fuhcBiomeName(int biome);

int32_t fuhcArenaSeed(const Config& cfg, const Area& area);

Config fuhcTerrainConfig(const Config& cfg, const Area& area);

bool fuhcArenaUsesBiomeBlend(const Area& area);

void prepareFuhcBiomeBlend(const Config& cfg, Area& area);

int fuhcBiomeAt(const Config& cfg, const Area& area, int x, int z);

int fuhcSurfaceY(const Config& cfg, const Area& area, int x, int z);

std::array<FuhcSpawnPoint, 2> fuhcSpawnPoints(const Config& cfg, const Area& area,
                                              const std::array<int, 2>* spawnYs = nullptr);

bool withinFuhcSpawnRadius(const Config& cfg, const Area& area, int x, int z, int radius);

void generateFuhcChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                       int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes,
                       std::array<int, 2>& spawnYs);

WorldSpawn fuhcWorldSpawn(const Config& cfg, const std::vector<Area>& areas,
                          const std::vector<std::array<int, 2>>& spawnYs);

void writeFuhcPositionsJson(const Config& cfg, const fs::path& outDir, const std::vector<Area>& areas,
                            const std::vector<std::array<int, 2>>& spawnYs);

void writeFuhcResultJson(const Config& cfg, const fs::path& outDir, const std::vector<Area>& areas,
                         const std::vector<std::array<int, 2>>& spawnYs, const WorldSpawn& spawn,
                         int chunks, int regions, int workers, double seconds);
