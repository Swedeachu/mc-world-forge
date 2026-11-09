#pragma once

#include <array>
#include <string>
#include <unordered_map>
#include <vector>

#include "mcwf/config.hpp"

// Block/biome registry and the per-run block id lookups.

struct Blocks {
    std::vector<std::string> names;
    std::unordered_map<std::string, int> map;

    int add(const std::string& name);
    int id(const std::string& name) const;
};

// Currently bad and hard coded but these are the only blocks we need right now.
struct BlockIds {
    int air = 0;
    int bedrock = 0;
    int glass = 0;
    int barrier = 0;
    // FUHC "pattern" walls: one id per step of the diagonal repeat.
    std::vector<int> fuhcWallPattern;
    int stone = 0;
    int deepslate = 0;
    int tuff = 0;
    int cobbledDeepslate = 0;
    int dirt = 0;
    int coarseDirt = 0;
    int grass = 0;
    int grassSnow = 0;
    int sand = 0;
    int sandstone = 0;
    int redSand = 0;
    int redSandstone = 0;
    int terracotta = 0;
    int orangeTerracotta = 0;
    int yellowTerracotta = 0;
    int brownTerracotta = 0;
    int redTerracotta = 0;
    int whiteTerracotta = 0;
    int gravel = 0;
    int clay = 0;
    int water = 0;
    int lava = 0;
    int magma = 0;
    int netherrack = 0;
    int soulSand = 0;
    int soulSoil = 0;
    int basalt = 0;
    int blackstone = 0;
    int netherGoldOre = 0;
    int netherQuartzOre = 0;
    int ancientDebris = 0;
    int crimsonNylium = 0;
    int warpedNylium = 0;
    int crimsonStem = 0;
    int warpedStem = 0;
    int netherWartBlock = 0;
    int warpedWartBlock = 0;
    int shroomlight = 0;
    int glowstone = 0;
    int boneBlock = 0;
    int sculk = 0;
    int snowLayer = 0;
    int snowBlock = 0;
    int podzol = 0;
    int oakLog = 0;
    int oakLeaves = 0;
    int birchLog = 0;
    int birchLeaves = 0;
    int spruceLog = 0;
    int spruceLeaves = 0;
    int jungleLog = 0;
    int jungleLeaves = 0;
    int darkOakLog = 0;
    int darkOakLeaves = 0;
    int cactus = 0;
    int sugarCane = 0;
    int deadBush = 0;
    int shortGrass = 0;
    int fern = 0;
    int tallGrassLo = 0;
    int tallGrassHi = 0;
    int vineNorth = 0;
    int vineSouth = 0;
    int vineEast = 0;
    int vineWest = 0;
    int mud = 0;
    int lilyPad = 0;
    int blueOrchid = 0;
    int brownMushroom = 0;
    int redMushroom = 0;
    std::array<int, 5> flowers{};
};

extern const std::vector<std::string> BIOMES;

enum BiomeId {
    BIOME_PLAINS = 0,
    BIOME_FOREST = 1,
    BIOME_BIRCH = 2,
    BIOME_DESERT = 3,
    BIOME_SAVANNA = 4,
    BIOME_JUNGLE = 5,
    BIOME_SNOW = 6,
    BIOME_TAIGA = 7,
    BIOME_BADLANDS = 8,
    BIOME_RIVER = 9,
    BIOME_SWAMP = 10,
    BIOME_HILLS = 11,
    BIOME_DEEP_DARK = 12,
    BIOME_NETHER = 13,
    BIOME_CRIMSON = 14,
    BIOME_WARPED = 15,
    BIOME_SOUL = 16,
    BIOME_BASALT = 17,
};

std::string deepDarkOreName(const std::string& name);

Blocks buildBlocks(const Config& cfg, BlockIds& ids);
