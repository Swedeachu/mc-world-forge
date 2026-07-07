#pragma once

#include <array>

#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"

struct TerrainSample {
    int height = 64;
    int biome = BIOME_PLAINS;
    bool river = false;
    bool lake = false;
    bool lava = false;
    bool beach = false;
    int waterSurface = 0;
    int lavaSurface = 0;
    bool badlands = false;
    bool desert = false;
    bool snowy = false;
    double mountain = 0.0;
    double badlandsStrength = 0.0;
    int topId = 0;
    int subId = 0;
};

// Surface + biome sampling.
TerrainSample sampleTerrain(const Config& cfg, int x, int z);

void sampleTerrainChunk(const Config& cfg, const Blocks& blocks, const BlockIds& ids, int cx, int cz,
                        std::array<std::array<TerrainSample, 16>, 16>& out);

// Noise-field cave test (shared by the deep-dark skin and ore-exposure checks).
bool caveCarves(const Config& cfg, int x, int y, int z, int surfaceY, int floorY);

int netherBiome(const Config& cfg, int x, int z);
