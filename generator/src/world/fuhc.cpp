#include "mcwf/fuhc.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <limits>
#include <numeric>
#include <string>
#include <vector>

#include "mcwf/chunk.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"
#include "internal/chunk_common.hpp"

namespace {

constexpr int FUHC_BIOME_GROUP_COUNT = 4;

double fuhcUnitRandom(uint32_t seed, uint32_t salt) {
    return static_cast<double>(mix32(seed ^ salt)) * INV_U32;
}

std::array<double, FUHC_BIOME_GROUP_COUNT> fuhcBiomeWeights(const Config& cfg) {
    return {{
        std::max(0.0, cfg.biomePlainsForestWeight),
        std::max(0.0, cfg.biomeDesertWeight),
        std::max(0.0, cfg.biomeSnowWeight),
        std::max(0.0, cfg.biomeRareWeight),
    }};
}

std::array<int, FUHC_BIOME_GROUP_COUNT> fuhcBiomeQuotas(const Config& cfg, int arenaCount) {
    std::array<int, FUHC_BIOME_GROUP_COUNT> quota{};
    if (arenaCount <= 0 || !cfg.fuhcVariedBiomes) {
        quota[FUHC_BIOME_PLAINS] = std::max(0, arenaCount);
        return quota;
    }

    std::array<double, FUHC_BIOME_GROUP_COUNT> weights = fuhcBiomeWeights(cfg);
    double totalWeight = 0.0;
    int activeGroups = 0;
    for (double weight : weights) {
        totalWeight += weight;
        if (weight > 0.0) {
            ++activeGroups;
        }
    }

    if (totalWeight <= 0.0 || activeGroups <= 0) {
        quota[FUHC_BIOME_PLAINS] = arenaCount;
        return quota;
    }

    int remaining = arenaCount;
    if (arenaCount >= activeGroups) {
        for (int group = 0; group < FUHC_BIOME_GROUP_COUNT; ++group) {
            if (weights[static_cast<size_t>(group)] > 0.0) {
                quota[static_cast<size_t>(group)] = 1;
                --remaining;
            }
        }
    }

    std::array<double, FUHC_BIOME_GROUP_COUNT> fractions{};
    int allocated = 0;
    for (int group = 0; group < FUHC_BIOME_GROUP_COUNT; ++group) {
        double ideal = static_cast<double>(remaining) * weights[static_cast<size_t>(group)] / totalWeight;
        int whole = static_cast<int>(std::floor(ideal));
        quota[static_cast<size_t>(group)] += whole;
        fractions[static_cast<size_t>(group)] = ideal - static_cast<double>(whole);
        allocated += whole;
    }

    int leftovers = remaining - allocated;
    while (leftovers > 0) {
        int bestGroup = -1;
        double bestFraction = -1.0;
        uint32_t bestTie = 0;

        for (int group = 0; group < FUHC_BIOME_GROUP_COUNT; ++group) {
            if (weights[static_cast<size_t>(group)] <= 0.0) {
                continue;
            }

            double fraction = fractions[static_cast<size_t>(group)];
            uint32_t tie = mix32(static_cast<uint32_t>(cfg.seed) ^
                                 (static_cast<uint32_t>(group + 1) * 0x9e3779b9u));
            if (fraction > bestFraction || (fraction == bestFraction && tie > bestTie)) {
                bestGroup = group;
                bestFraction = fraction;
                bestTie = tie;
            }
        }

        if (bestGroup < 0) {
            quota[FUHC_BIOME_PLAINS] += leftovers;
            break;
        }

        ++quota[static_cast<size_t>(bestGroup)];
        fractions[static_cast<size_t>(bestGroup)] = -1.0;
        --leftovers;
    }

    return quota;
}

int fuhcBiomeVariant(const Config& cfg, int group, int occurrence) {
    static const std::array<std::vector<int>, FUHC_BIOME_GROUP_COUNT> variants = {{
        {BIOME_PLAINS, BIOME_FOREST, BIOME_BIRCH, BIOME_TAIGA, BIOME_SAVANNA},
        {BIOME_DESERT},
        {BIOME_SNOW},
        {BIOME_JUNGLE, BIOME_SWAMP, BIOME_BADLANDS},
    }};

    const std::vector<int>& choices = variants[static_cast<size_t>(clampInt(group, 0, FUHC_BIOME_GROUP_COUNT - 1))];
    uint32_t offsetHash = mix32(static_cast<uint32_t>(cfg.seed) ^
                                (static_cast<uint32_t>(group + 1) * 0x85ebca6bu) ^ 0x46554249u);
    int offset = static_cast<int>(offsetHash % static_cast<uint32_t>(choices.size()));

    return choices[static_cast<size_t>((occurrence + offset) % static_cast<int>(choices.size()))];
}

struct FuhcClimateColumn {
    double temperature = 0.0;
    double moisture = 0.5;
    double selector = 0.5;
    double coldSelector = 0.0;
    double warmSelector = 0.0;
    double variantSelectorA = 0.0;
    double variantSelectorB = 0.0;
    double variantSelectorC = 0.0;
    int index = 0;
};

double fuhcSignedFbm(double x, double z, int seed, int octaves, double frequency) {
    return fbm2d(x, z, seed, octaves, frequency) * 2.0 - 1.0;
}

void fuhcClimateFields(const Area& area, int arenaSeed, int localX, int localZ,
                       double& temperature, double& moisture, double& selector,
                       double& coldSelector, double& warmSelector,
                       double& variantSelectorA, double& variantSelectorB,
                       double& variantSelectorC) {
    double width = static_cast<double>(std::max(1, area.xmax - area.xmin - 1));
    double depth = static_cast<double>(std::max(1, area.zmax - area.zmin - 1));
    double shortestSide = std::max(16.0, std::min(width, depth));
    double centeredX = static_cast<double>(localX) - (width - 1.0) * 0.5;
    double centeredZ = static_cast<double>(localZ) - (depth - 1.0) * 0.5;
    uint32_t seed = static_cast<uint32_t>(arenaSeed);

    double angle = fuhcUnitRandom(seed, 0x54454d50u) * PI * 2.0;
    double axisX = std::cos(angle);
    double axisZ = std::sin(angle);
    double crossX = -axisZ;
    double crossZ = axisX;
    double primary = centeredX * axisX + centeredZ * axisZ;
    double across = centeredX * crossX + centeredZ * crossZ;

    double bendWavelength = shortestSide *
                            (0.95 + fuhcUnitRandom(seed, 0x62656e64u) * 0.65);
    double bendPhase = fuhcUnitRandom(seed, 0x70686173u) * PI * 2.0;
    double bend = std::sin(across * PI * 2.0 / bendWavelength + bendPhase) *
                  shortestSide * (0.055 + fuhcUnitRandom(seed, 0x63757276u) * 0.035);

    double broadScale = shortestSide *
                        (0.68 + fuhcUnitRandom(seed, 0x62726f61u) * 0.34);
    double broad = fuhcSignedFbm(
        (static_cast<double>(localX) + 71.0) / broadScale,
        (static_cast<double>(localZ) - 53.0) / broadScale,
        arenaSeed + 0x1f31, 3, 1.0) * shortestSide * 0.075;
    double secondaryScale = shortestSide *
                            (0.42 + fuhcUnitRandom(seed, 0x7365636fu) * 0.18);
    double secondary = fuhcSignedFbm(
        (static_cast<double>(localX) - 113.0) / secondaryScale,
        (static_cast<double>(localZ) + 89.0) / secondaryScale,
        arenaSeed + 0x2a17, 2, 1.0) * shortestSide * 0.028;

    temperature = primary + bend + broad + secondary;

    double moistureScale = shortestSide *
                           (0.56 + fuhcUnitRandom(seed, 0x6d6f6973u) * 0.30);
    double moistureField = fuhcSignedFbm(
        (static_cast<double>(localX) + 211.0) / moistureScale,
        (static_cast<double>(localZ) - 177.0) / moistureScale,
        arenaSeed + 0x3c29, 3, 1.0);
    double moistureWave = std::sin(
        across * PI * 2.0 / (shortestSide * 1.35) +
        fuhcUnitRandom(seed, 0x68756d69u) * PI * 2.0);
    moisture = clampDouble(0.5 + moistureField * 0.34 + moistureWave * 0.12, 0.0, 1.0);

    double selectorScale = shortestSide * 0.48;
    selector = clampDouble(
        0.5 + fuhcSignedFbm(
            (static_cast<double>(localX) - 307.0) / selectorScale,
            (static_cast<double>(localZ) + 263.0) / selectorScale,
            arenaSeed + 0x4d43, 2, 1.0) * 0.5,
        0.0, 1.0);

    double crossNormalized = across / shortestSide;
    double primaryNormalized = primary / shortestSide;
    double coldLobeScale = shortestSide *
                           (0.78 + fuhcUnitRandom(seed, 0x636f6c64u) * 0.30);
    double warmLobeScale = shortestSide *
                           (0.74 + fuhcUnitRandom(seed, 0x7761726du) * 0.34);
    double coldLobe = fuhcSignedFbm(
        (static_cast<double>(localX) + 401.0) / coldLobeScale,
        (static_cast<double>(localZ) - 347.0) / coldLobeScale,
        arenaSeed + 0x5f51, 3, 1.0);
    double warmLobe = fuhcSignedFbm(
        (static_cast<double>(localX) - 439.0) / warmLobeScale,
        (static_cast<double>(localZ) + 379.0) / warmLobeScale,
        arenaSeed + 0x6a63, 3, 1.0);
    double coldCurve = std::sin(
        primary * PI * 2.0 / (shortestSide * 1.55) +
        fuhcUnitRandom(seed, 0x636f6c76u) * PI * 2.0);
    double warmCurve = std::sin(
        primary * PI * 2.0 / (shortestSide * 1.68) +
        fuhcUnitRandom(seed, 0x77617276u) * PI * 2.0);

    // The primary temperature field only chooses the broad cold and warm
    // continents. These perpendicular, low-frequency selectors split those
    // continents into full-sized biome regions instead of turning every
    // intermediate biome into a narrow contour strip.
    coldSelector = crossNormalized * 0.58 + coldLobe * 0.31 +
                   coldCurve * 0.08 - primaryNormalized * 0.03;
    warmSelector = -crossNormalized * 0.49 + warmLobe * 0.28 +
                   warmCurve * 0.08 + primaryNormalized * 0.05 -
                   (moisture - 0.5) * 0.10;

    double variantScaleA = shortestSide *
                           (0.64 + fuhcUnitRandom(seed, 0x76617261u) * 0.28);
    double variantScaleB = shortestSide *
                           (0.58 + fuhcUnitRandom(seed, 0x76617262u) * 0.32);
    double variantScaleC = shortestSide *
                           (0.70 + fuhcUnitRandom(seed, 0x76617263u) * 0.26);
    double variantNoiseA = fuhcSignedFbm(
        (static_cast<double>(localX) + 503.0) / variantScaleA,
        (static_cast<double>(localZ) - 457.0) / variantScaleA,
        arenaSeed + 0x7b71, 3, 1.0);
    double variantNoiseB = fuhcSignedFbm(
        (static_cast<double>(localX) - 547.0) / variantScaleB,
        (static_cast<double>(localZ) + 491.0) / variantScaleB,
        arenaSeed + 0x8c83, 3, 1.0);
    double variantNoiseC = fuhcSignedFbm(
        (static_cast<double>(localX) + 593.0) / variantScaleC,
        (static_cast<double>(localZ) + 557.0) / variantScaleC,
        arenaSeed + 0x9d95, 3, 1.0);

    variantSelectorA = variantNoiseA * 0.58 + crossNormalized * 0.24 +
                       (moisture - 0.5) * 0.18;
    variantSelectorB = variantNoiseB * 0.58 - primaryNormalized * 0.22 +
                       (selector - 0.5) * 0.20;
    variantSelectorC = variantNoiseC * 0.60 +
                       (coldSelector - warmSelector) * 0.22 -
                       (moisture - 0.5) * 0.18;
}

int fuhcBiomeGroupForBiome(int biome) {
    if (biome == BIOME_DESERT) {
        return FUHC_BIOME_DESERT;
    }
    if (biome == BIOME_SNOW) {
        return FUHC_BIOME_SNOW;
    }
    if (biome == BIOME_JUNGLE || biome == BIOME_SWAMP || biome == BIOME_BADLANDS) {
        return FUHC_BIOME_RARE;
    }

    return FUHC_BIOME_PLAINS;
}

int fuhcFloorY(const Config& cfg) {
    return clampInt(cfg.fuhcBaseY - cfg.fuhcDepth + 1, MIN_Y, MIN_Y + WORLD_HEIGHT - 4);
}

int fuhcBedrockWallStartY(const Config& cfg) {
    return clampInt(cfg.fuhcBaseY, MIN_Y, MIN_Y + WORLD_HEIGHT - 3);
}

int fuhcBedrockWallEndY(const Config& cfg) {
    return clampInt(fuhcBedrockWallStartY(cfg) + cfg.fuhcBedrockWallHeight - 1,
                    MIN_Y, MIN_Y + WORLD_HEIGHT - 3);
}

int fuhcGlassWallHeight(const Config& cfg, const Area& area) {
    int height = area.fuhcGlassWallHeight > 0 ? area.fuhcGlassWallHeight : cfg.fuhcGlassWallHeight;

    return clampInt(height, 1, 128);
}

int fuhcGlassWallStartY(const Config& cfg) {
    return std::min(MIN_Y + WORLD_HEIGHT - 2, fuhcBedrockWallEndY(cfg) + 1);
}

int fuhcGlassWallEndY(const Config& cfg, const Area& area) {
    return clampInt(fuhcGlassWallStartY(cfg) + fuhcGlassWallHeight(cfg, area) - 1,
                    MIN_Y, MIN_Y + WORLD_HEIGHT - 2);
}

int fuhcRoofY(const Config& cfg, const Area& area) {
    return std::min(MIN_Y + WORLD_HEIGHT - 1, fuhcGlassWallEndY(cfg, area) + 1);
}

int fuhcVisibleShellTopY(const Config& cfg, const Area& area) {
    return cfg.fuhcGlassRoof ? fuhcRoofY(cfg, area) : fuhcGlassWallEndY(cfg, area);
}

int fuhcBarrierFloorStartY(const Config& cfg) {
    return std::max(MIN_Y, fuhcFloorY(cfg) - FUHC_BARRIER_LAYERS);
}

int fuhcBarrierRoofStartY(const Config& cfg, const Area& area) {
    return std::min(MIN_Y + WORLD_HEIGHT - 1, fuhcVisibleShellTopY(cfg, area) + 1);
}

int fuhcBarrierRoofEndY(const Config& cfg, const Area& area) {
    return std::min(MIN_Y + WORLD_HEIGHT - 1,
                    fuhcBarrierRoofStartY(cfg, area) + FUHC_BARRIER_LAYERS - 1);
}

int fuhcHorizontalShellDistance(const Area& area, int x, int z) {
    int dx = 0;
    int dz = 0;
    if (x < area.xmin) {
        dx = area.xmin - x;
    } else if (x > area.xmax) {
        dx = x - area.xmax;
    }
    if (z < area.zmin) {
        dz = area.zmin - z;
    } else if (z > area.zmax) {
        dz = z - area.zmax;
    }

    return std::max(dx, dz);
}

bool isFuhcVegetation(int id, const BlockIds& ids) {
    return mcwf::chunk::isPlantLike(id, ids) ||
           id == ids.oakLog || id == ids.oakLeaves ||
           id == ids.birchLog || id == ids.birchLeaves ||
           id == ids.spruceLog || id == ids.spruceLeaves ||
           id == ids.jungleLog || id == ids.jungleLeaves ||
           id == ids.darkOakLog || id == ids.darkOakLeaves ||
           id == ids.snowLayer;
}

int findFuhcGround(const std::vector<uint16_t>& blocks, const BlockIds& ids, int lx, int lz, int floorY, int ceilingY) {
    for (int y = std::min(ceilingY - 1, MIN_Y + WORLD_HEIGHT - 1); y >= floorY; --y) {
        int id = mcwf::chunk::getBlock(blocks, lx, y, lz);
        if (id == ids.air || id == ids.glass || id == ids.barrier || isFuhcVegetation(id, ids)) {
            continue;
        }

        return y;
    }

    return floorY;
}

void clearSpawnBody(std::vector<uint16_t>& blocks, const BlockIds& ids, int lx, int lz, int groundY) {
    for (int y = groundY + 1; y <= groundY + 2; ++y) {
        if (yIndex(y) < 0 || yIndex(y) >= WORLD_HEIGHT) {
            continue;
        }

        int id = mcwf::chunk::getBlock(blocks, lx, y, lz);
        if (id == ids.glass || id == ids.barrier || id == ids.bedrock) {
            continue;
        }

        mcwf::chunk::setBlock(blocks, lx, y, lz, ids.air);
    }
}

void worldBounds(const std::vector<Area>& areas, int& xmin, int& xmax, int& zmin, int& zmax) {
    xmin = areas.empty() ? 0 : areas[0].xmin;
    xmax = areas.empty() ? 0 : areas[0].xmax;
    zmin = areas.empty() ? 0 : areas[0].zmin;
    zmax = areas.empty() ? 0 : areas[0].zmax;

    for (const Area& area : areas) {
        xmin = std::min(xmin, area.xmin);
        xmax = std::max(xmax, area.xmax);
        zmin = std::min(zmin, area.zmin);
        zmax = std::max(zmax, area.zmax);
    }
}

}  // namespace

std::vector<FuhcBiomeAssignment> planFuhcBiomes(const Config& cfg, int arenaCount) {
    arenaCount = std::max(0, arenaCount);
    std::vector<FuhcBiomeAssignment> out;
    out.reserve(static_cast<size_t>(arenaCount));

    if (!cfg.fuhcVariedBiomes) {
        for (int arena = 0; arena < arenaCount; ++arena) {
            out.push_back({FUHC_BIOME_PLAINS, BIOME_PLAINS});
        }

        return out;
    }

    std::array<int, FUHC_BIOME_GROUP_COUNT> quota = fuhcBiomeQuotas(cfg, arenaCount);
    std::array<int, FUHC_BIOME_GROUP_COUNT> remaining = quota;
    std::array<int, FUHC_BIOME_GROUP_COUNT> occurrences{};
    std::array<double, FUHC_BIOME_GROUP_COUNT> score{};

    for (int group = 0; group < FUHC_BIOME_GROUP_COUNT; ++group) {
        uint32_t phase = mix32(static_cast<uint32_t>(cfg.seed) ^
                               (static_cast<uint32_t>(group + 1) * 0xc2b2ae35u) ^ 0x42494f4du);
        score[static_cast<size_t>(group)] = static_cast<double>(phase) * INV_U32 * arenaCount;
    }

    for (int arena = 0; arena < arenaCount; ++arena) {
        int bestGroup = -1;
        double bestScore = -std::numeric_limits<double>::infinity();
        uint32_t bestTie = 0;

        for (int group = 0; group < FUHC_BIOME_GROUP_COUNT; ++group) {
            if (remaining[static_cast<size_t>(group)] <= 0) {
                continue;
            }

            score[static_cast<size_t>(group)] += quota[static_cast<size_t>(group)];
            uint32_t tie = mix32(static_cast<uint32_t>(cfg.seed) ^
                                 (static_cast<uint32_t>(arena + 1) * 0x9e3779b9u) ^
                                 (static_cast<uint32_t>(group + 1) * 0x7f4a7c15u));
            if (score[static_cast<size_t>(group)] > bestScore ||
                (score[static_cast<size_t>(group)] == bestScore && tie > bestTie)) {
                bestGroup = group;
                bestScore = score[static_cast<size_t>(group)];
                bestTie = tie;
            }
        }

        if (bestGroup < 0) {
            bestGroup = FUHC_BIOME_PLAINS;
        }

        score[static_cast<size_t>(bestGroup)] -= arenaCount;
        --remaining[static_cast<size_t>(bestGroup)];
        int occurrence = occurrences[static_cast<size_t>(bestGroup)]++;
        out.push_back({bestGroup, fuhcBiomeVariant(cfg, bestGroup, occurrence)});
    }

    return out;
}

const char* fuhcBiomeGroupName(int group) {
    switch (group) {
        case FUHC_BIOME_DESERT:
            return "desert";
        case FUHC_BIOME_SNOW:
            return "snow";
        case FUHC_BIOME_RARE:
            return "rare";
        case FUHC_BIOME_PLAINS:
        default:
            return "plains_forest_taiga";
    }
}

const char* fuhcBiomeName(int biome) {
    if (biome >= 0 && biome < static_cast<int>(BIOMES.size())) {
        return BIOMES[static_cast<size_t>(biome)].c_str();
    }

    return "minecraft:plains";
}

int32_t fuhcArenaSeed(const Config& cfg, const Area& area) {
    uint32_t rootSeed = static_cast<uint32_t>(cfg.seed);
    uint32_t arenaNumber = static_cast<uint32_t>(std::max(0, area.index) + 1);
    uint32_t sequence = mix32(arenaNumber * 0x9e3779b9u + 0x46554843u);
    uint32_t mixed = mix32(rootSeed ^ sequence ^ 0xa511e9b3u);
    int64_t signedSeed = mixed <= 0x7fffffffu
        ? static_cast<int64_t>(mixed)
        : static_cast<int64_t>(mixed) - 0x100000000ll;

    return static_cast<int32_t>(signedSeed);
}

Config fuhcTerrainConfig(const Config& cfg, const Area& area) {
    Config terrain = cfg;
    terrain.generator = "fuhc";
    terrain.seed = fuhcArenaSeed(cfg, area);
    terrain.floorY = fuhcFloorY(cfg);
    terrain.baseHeight = cfg.fuhcBaseY;
    terrain.waterLevel = terrain.floorY - 1;
    terrain.hillAmp = 0.0;
    terrain.mountainAmp = 0.0;
    terrain.mountainAmount = 0.0;
    terrain.extremeMountainAmount = 0.0;
    terrain.snowlineY = fuhcRoofY(cfg, area) + 32;
    terrain.fuhcGlassWallHeight = fuhcGlassWallHeight(cfg, area);
    terrain.surfaceLavaPools = false;
    terrain.caves = false;
    terrain.ravines = false;
    terrain.fluids = false;
    terrain.rivers = false;
    terrain.lakes = false;
    terrain.wall = false;
    terrain.deepDark = false;
    terrain.nether = false;
    terrain.structures = false;
    terrain.biomeOverride = area.biome >= 0 ? area.biome : BIOME_PLAINS;
    terrain.treeDensity = cfg.treeDensity / 3.0;

    if (!cfg.fuhcVariedBiomes) {
        terrain.biomePlainsForestWeight = 100.0;
        terrain.biomeDesertWeight = 0.0;
        terrain.biomeSnowWeight = 0.0;
        terrain.biomeRareWeight = 0.0;
    }

    return terrain;
}

bool fuhcArenaUsesBiomeBlend(const Area& area) {
    return area.size > 50;
}

void prepareFuhcBiomeBlend(const Config& cfg, Area& area) {
    area.fuhcBiomeMapWidth = 0;
    area.fuhcBiomeMapDepth = 0;
    area.fuhcBiomeMap.reset();

    if (!cfg.fuhcVariedBiomes || !fuhcArenaUsesBiomeBlend(area)) {
        return;
    }

    int width = std::max(1, area.xmax - area.xmin - 1);
    int depth = std::max(1, area.zmax - area.zmin - 1);
    int columnCount = width * depth;
    int arenaSeed = fuhcArenaSeed(cfg, area);
    std::vector<FuhcClimateColumn> climate(static_cast<size_t>(columnCount));
    std::vector<int> order(static_cast<size_t>(columnCount));

    for (int localZ = 0; localZ < depth; ++localZ) {
        for (int localX = 0; localX < width; ++localX) {
            int index = localZ * width + localX;
            FuhcClimateColumn& column = climate[static_cast<size_t>(index)];
            fuhcClimateFields(area, arenaSeed, localX, localZ,
                              column.temperature, column.moisture, column.selector,
                              column.coldSelector, column.warmSelector,
                              column.variantSelectorA, column.variantSelectorB,
                              column.variantSelectorC);
            column.index = index;
            order[static_cast<size_t>(index)] = index;
        }
    }

    std::stable_sort(order.begin(), order.end(), [&](int left, int right) {
        const FuhcClimateColumn& a = climate[static_cast<size_t>(left)];
        const FuhcClimateColumn& b = climate[static_cast<size_t>(right)];
        if (a.temperature != b.temperature) {
            return a.temperature < b.temperature;
        }

        return a.index < b.index;
    });

    std::array<int, FUHC_BIOME_GROUP_COUNT> quota = fuhcBiomeQuotas(cfg, columnCount);
    std::vector<int> groupMap(static_cast<size_t>(columnCount), FUHC_BIOME_PLAINS);
    int coldCount = quota[FUHC_BIOME_SNOW] + quota[FUHC_BIOME_PLAINS];
    coldCount = clampInt(coldCount, 0, columnCount);
    std::vector<int> coldColumns(order.begin(), order.begin() + coldCount);
    std::vector<int> warmColumns(order.begin() + coldCount, order.end());

    auto splitContinent = [&](std::vector<int>& columns, int firstGroup, int firstCount,
                              int secondGroup, auto score) {
        firstCount = clampInt(firstCount, 0, static_cast<int>(columns.size()));
        std::stable_sort(columns.begin(), columns.end(), [&](int left, int right) {
            const FuhcClimateColumn& a = climate[static_cast<size_t>(left)];
            const FuhcClimateColumn& b = climate[static_cast<size_t>(right)];
            double leftScore = score(a);
            double rightScore = score(b);
            if (leftScore != rightScore) {
                return leftScore > rightScore;
            }

            return a.index < b.index;
        });

        for (int offset = 0; offset < static_cast<int>(columns.size()); ++offset) {
            int group = offset < firstCount ? firstGroup : secondGroup;
            groupMap[static_cast<size_t>(columns[static_cast<size_t>(offset)])] = group;
        }
    };

    splitContinent(coldColumns, FUHC_BIOME_SNOW, quota[FUHC_BIOME_SNOW],
                   FUHC_BIOME_PLAINS,
                   [](const FuhcClimateColumn& column) {
                       return column.coldSelector;
                   });
    splitContinent(warmColumns, FUHC_BIOME_DESERT, quota[FUHC_BIOME_DESERT],
                   FUHC_BIOME_RARE,
                   [](const FuhcClimateColumn& column) {
                       return column.warmSelector;
                   });

    auto biomeMap = std::make_shared<std::vector<uint8_t>>(
        static_cast<size_t>(columnCount), static_cast<uint8_t>(BIOME_PLAINS));

    struct BiomeVariantWeight {
        int biome = BIOME_PLAINS;
        double weight = 1.0;
    };

    auto selectorForDepth = [](const FuhcClimateColumn& column, int depth) {
        switch (depth % 3) {
            case 1:
                return column.variantSelectorB;
            case 2:
                return column.variantSelectorC;
            case 0:
            default:
                return column.variantSelectorA;
        }
    };

    auto assignVariants = [&](auto&& self, std::vector<int> columns,
                              std::vector<BiomeVariantWeight> variants, int depth) -> void {
        if (columns.empty() || variants.empty()) {
            return;
        }
        if (variants.size() == 1) {
            uint8_t biome = static_cast<uint8_t>(variants[0].biome);
            for (int index : columns) {
                (*biomeMap)[static_cast<size_t>(index)] = biome;
            }

            return;
        }
        if (columns.size() == 1) {
            auto best = std::max_element(variants.begin(), variants.end(),
                                         [](const BiomeVariantWeight& left,
                                            const BiomeVariantWeight& right) {
                                             return left.weight < right.weight;
                                         });
            (*biomeMap)[static_cast<size_t>(columns[0])] =
                static_cast<uint8_t>(best->biome);

            return;
        }

        size_t splitVariant = variants.size() / 2;
        double leftWeight = 0.0;
        double totalWeight = 0.0;
        for (size_t i = 0; i < variants.size(); ++i) {
            double weight = std::max(0.0, variants[i].weight);
            totalWeight += weight;
            if (i < splitVariant) {
                leftWeight += weight;
            }
        }
        if (totalWeight <= 0.0) {
            totalWeight = static_cast<double>(variants.size());
            leftWeight = static_cast<double>(splitVariant);
        }

        int leftCount = static_cast<int>(std::llround(
            static_cast<double>(columns.size()) * leftWeight / totalWeight));
        leftCount = clampInt(leftCount, 1, static_cast<int>(columns.size()) - 1);
        std::stable_sort(columns.begin(), columns.end(), [&](int left, int right) {
            const FuhcClimateColumn& a = climate[static_cast<size_t>(left)];
            const FuhcClimateColumn& b = climate[static_cast<size_t>(right)];
            double leftScore = selectorForDepth(a, depth);
            double rightScore = selectorForDepth(b, depth);
            if (leftScore != rightScore) {
                return leftScore > rightScore;
            }

            return a.index < b.index;
        });

        std::vector<int> leftColumns(columns.begin(), columns.begin() + leftCount);
        std::vector<int> rightColumns(columns.begin() + leftCount, columns.end());
        std::vector<BiomeVariantWeight> leftVariants(
            variants.begin(), variants.begin() + static_cast<std::ptrdiff_t>(splitVariant));
        std::vector<BiomeVariantWeight> rightVariants(
            variants.begin() + static_cast<std::ptrdiff_t>(splitVariant), variants.end());
        self(self, std::move(leftColumns), std::move(leftVariants), depth + 1);
        self(self, std::move(rightColumns), std::move(rightVariants), depth + 1);
    };

    std::array<std::vector<int>, FUHC_BIOME_GROUP_COUNT> groupColumns;
    for (int index = 0; index < columnCount; ++index) {
        int group = clampInt(groupMap[static_cast<size_t>(index)], 0,
                             FUHC_BIOME_GROUP_COUNT - 1);
        groupColumns[static_cast<size_t>(group)].push_back(index);
    }

    assignVariants(assignVariants, std::move(groupColumns[FUHC_BIOME_PLAINS]), {
        {BIOME_TAIGA, 1.25},
        {BIOME_BIRCH, 0.90},
        {BIOME_FOREST, 1.25},
        {BIOME_PLAINS, 2.00},
        {BIOME_SAVANNA, 0.90},
    }, 0);
    assignVariants(assignVariants, std::move(groupColumns[FUHC_BIOME_DESERT]), {
        {BIOME_DESERT, 1.0},
    }, 0);
    assignVariants(assignVariants, std::move(groupColumns[FUHC_BIOME_SNOW]), {
        {BIOME_SNOW, 1.0},
    }, 0);
    assignVariants(assignVariants, std::move(groupColumns[FUHC_BIOME_RARE]), {
        {BIOME_SWAMP, 1.0},
        {BIOME_JUNGLE, 1.0},
        {BIOME_BADLANDS, 1.0},
    }, 0);

    area.fuhcBiomeMapWidth = width;
    area.fuhcBiomeMapDepth = depth;
    area.fuhcBiomeMap = std::move(biomeMap);
}

int fuhcBiomeAt(const Config& cfg, const Area& area, int x, int z) {
    int fallback = area.biome >= 0 ? area.biome : BIOME_PLAINS;
    if (!cfg.fuhcVariedBiomes || !fuhcArenaUsesBiomeBlend(area) ||
        atAreaWall(x, z, area) || !area.fuhcBiomeMap) {
        return fallback;
    }

    int localX = x - (area.xmin + 1);
    int localZ = z - (area.zmin + 1);
    if (localX < 0 || localX >= area.fuhcBiomeMapWidth ||
        localZ < 0 || localZ >= area.fuhcBiomeMapDepth) {
        return fallback;
    }

    size_t index = static_cast<size_t>(localZ * area.fuhcBiomeMapWidth + localX);

    return static_cast<int>((*area.fuhcBiomeMap)[index]);
}

namespace {

int fuhcSurfaceProfileIndex(int seed) {
    return static_cast<int>(mix32(static_cast<uint32_t>(seed) ^ 0x54455252u) % 5u);
}

const char* fuhcSurfaceProfileName(int profile) {
    switch (profile) {
        case 1:
            return "long_ridges";
        case 2:
            return "crossed_swells";
        case 3:
            return "tilted_terraces";
        case 4:
            return "meandering_ridges";
        case 0:
        default:
            return "rolling_terraces";
    }
}

double fuhcSignedValueNoise(double x, double z, int seed, uint32_t salt) {
    return value2d(x, z, seed, salt) * 2.0 - 1.0;
}

double fuhcSurfaceField(const Config& cfg, const Area& area, int x, int z) {
    double interiorWidth = static_cast<double>(std::max(1, area.xmax - area.xmin - 1));
    double interiorDepth = static_cast<double>(std::max(1, area.zmax - area.zmin - 1));
    double shortestSide = std::max(8.0, std::min(interiorWidth, interiorDepth));
    double localX = static_cast<double>(x - (area.xmin + 1));
    double localZ = static_cast<double>(z - (area.zmin + 1));
    double centeredX = localX - (interiorWidth - 1.0) * 0.5;
    double centeredZ = localZ - (interiorDepth - 1.0) * 0.5;
    uint32_t seed = static_cast<uint32_t>(cfg.seed);

    double primaryAngle = fuhcUnitRandom(seed, 0x31a53f85u) * PI * 2.0;
    double secondaryAngle = primaryAngle +
                            PI * (0.32 + fuhcUnitRandom(seed, 0x27d4eb2du) * 0.36);
    double primaryCos = std::cos(primaryAngle);
    double primarySin = std::sin(primaryAngle);
    double secondaryCos = std::cos(secondaryAngle);
    double secondarySin = std::sin(secondaryAngle);
    double primaryAxis = centeredX * primaryCos + centeredZ * primarySin;
    double secondaryAxis = centeredX * secondaryCos + centeredZ * secondarySin;

    double warpScale = shortestSide *
                       (0.50 + fuhcUnitRandom(seed, 0x85ebca6bu) * 0.24);
    double warpStrength = shortestSide *
                          (0.08 + fuhcUnitRandom(seed, 0xc2b2ae35u) * 0.10);
    double warpX = fuhcSignedValueNoise(
        (localX + 17.0) / warpScale, (localZ - 23.0) / warpScale,
        cfg.seed, 0x91e10da5u) * warpStrength;
    double warpZ = fuhcSignedValueNoise(
        (localX - 31.0) / warpScale, (localZ + 29.0) / warpScale,
        cfg.seed, 0x7f4a7c15u) * warpStrength;

    double broadScale = shortestSide *
                        (0.46 + fuhcUnitRandom(seed, 0x165667b1u) * 0.28);
    double detailScale = shortestSide *
                         (0.27 + fuhcUnitRandom(seed, 0x6d2b79f5u) * 0.16);
    double broad = fuhcSignedValueNoise(
        (localX + warpX) / broadScale, (localZ + warpZ) / broadScale,
        cfg.seed, 0x53555246u);
    double detail = fuhcSignedValueNoise(
        (localX - warpZ * 0.5) / detailScale,
        (localZ + warpX * 0.5) / detailScale,
        cfg.seed, 0xa511e9b3u);

    double primaryWavelength = shortestSide *
                               (0.72 + fuhcUnitRandom(seed, 0x9e3779b9u) * 0.48);
    double secondaryWavelength = shortestSide *
                                 (0.50 + fuhcUnitRandom(seed, 0x243f6a88u) * 0.38);
    double primaryPhase = fuhcUnitRandom(seed, 0xb7e15162u) * PI * 2.0;
    double secondaryPhase = fuhcUnitRandom(seed, 0xdeadbeefu) * PI * 2.0;
    double primaryWave = std::sin(
        (primaryAxis + warpX * 0.70) * PI * 2.0 / primaryWavelength +
        primaryPhase + broad * 0.55);
    double secondaryWave = std::sin(
        (secondaryAxis + warpZ * 0.50) * PI * 2.0 / secondaryWavelength +
        secondaryPhase + broad * 0.30);
    double trend = clampDouble(primaryAxis / std::max(interiorWidth, interiorDepth), -0.5, 0.5) * 2.0;

    int profile = fuhcSurfaceProfileIndex(cfg.seed);
    double field = 0.0;
    switch (profile) {
        case 1:
            field = broad * 0.35 + detail * 0.10 + primaryWave * 0.45 + secondaryWave * 0.10;
            break;
        case 2:
            field = broad * 0.38 + detail * 0.12 + primaryWave * 0.25 + secondaryWave * 0.25;
            break;
        case 3:
            field = broad * 0.42 + detail * 0.14 + primaryWave * 0.26 +
                    secondaryWave * 0.10 + trend * 0.08;
            break;
        case 4: {
            double meander = std::sin(
                (primaryAxis + warpX) * PI * 2.0 / primaryWavelength +
                primaryPhase + broad * 1.15);
            field = broad * 0.38 + detail * 0.12 + meander * 0.42 + secondaryWave * 0.08;
            break;
        }
        case 0:
        default:
            field = broad * 0.45 + detail * 0.15 + primaryWave * 0.30 + secondaryWave * 0.10;
            break;
    }

    return field * 1.45;
}

}  // namespace

int fuhcSurfaceY(const Config& cfg, const Area& area, int x, int z) {
    int baseY = cfg.fuhcBaseY;
    if (cfg.fuhcSurfaceRelief <= 0.0 || atAreaWall(x, z, area)) {
        return baseY;
    }

    double reliefStrength = clampDouble(cfg.fuhcSurfaceRelief / 2.0, 0.0, 1.0);
    uint32_t seed = static_cast<uint32_t>(cfg.seed);
    double threshold = lerp(0.34, -0.02, reliefStrength) +
                       (fuhcUnitRandom(seed, 0xdecafbadu) - 0.5) * 0.06;
    double field = fuhcSurfaceField(cfg, area, x, z);

    return field > threshold ? baseY + 1 : baseY;
}

std::array<FuhcSpawnPoint, 2> fuhcSpawnPoints(const Config& cfg, const Area& area,
                                              const std::array<int, 2>* spawnYs) {
    int interiorMinZ = area.zmin + 1;
    int interiorMaxZ = area.zmax - 1;
    int maxInset = std::max(1, (interiorMaxZ - interiorMinZ - 2) / 2);
    int inset = clampInt(cfg.fuhcSpawnInset, 1, maxInset);
    int x = area.xmin + area.size / 2;
    int z1 = interiorMinZ + inset;
    int z2 = interiorMaxZ - inset;
    int defaultY = cfg.fuhcBaseY + 1;

    if (z1 >= z2) {
        int span = std::max(2, interiorMaxZ - interiorMinZ);
        z1 = interiorMinZ + span / 3;
        z2 = interiorMaxZ - span / 3;
    }

    return {{
        {x, spawnYs && (*spawnYs)[0] > MIN_Y ? (*spawnYs)[0] : defaultY, z1},
        {x, spawnYs && (*spawnYs)[1] > MIN_Y ? (*spawnYs)[1] : defaultY, z2},
    }};
}

bool withinFuhcSpawnRadius(const Config& cfg, const Area& area, int x, int z, int radius) {
    radius = std::max(0, radius);
    std::array<FuhcSpawnPoint, 2> spawns = fuhcSpawnPoints(cfg, area);

    for (const FuhcSpawnPoint& spawn : spawns) {
        if (std::abs(x - spawn.x) <= radius && std::abs(z - spawn.z) <= radius) {
            return true;
        }
    }

    return false;
}

void generateFuhcChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                       int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes,
                       std::array<int, 2>& spawnYs) {
    Config terrain = fuhcTerrainConfig(cfg, area);
    generateOverworldChunk(terrain, area, blocks, ids, cx, cz, outBlocks, outBiomes);

    int floorY = fuhcFloorY(cfg);
    int bedrockEndY = fuhcBedrockWallEndY(cfg);
    int glassStartY = fuhcGlassWallStartY(cfg);
    int glassEndY = fuhcGlassWallEndY(cfg, area);
    int roofY = fuhcRoofY(cfg, area);
    int barrierFloorStartY = fuhcBarrierFloorStartY(cfg);
    int barrierRoofStartY = fuhcBarrierRoofStartY(cfg, area);
    int barrierRoofEndY = fuhcBarrierRoofEndY(cfg, area);
    int openTopY = barrierRoofStartY;
    int roofId = cfg.fuhcRoofBlock == "barrier" ? ids.barrier : ids.glass;
    int patternLength = static_cast<int>(ids.fuhcWallPattern.size());
    bool patternWalls = cfg.fuhcWallStyle == "pattern" && patternLength > 0;

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            int shellDistance = fuhcHorizontalShellDistance(area, x, z);
            bool insideArena = shellDistance == 0;
            bool barrierSide = shellDistance > 0 && shellDistance <= FUHC_BARRIER_LAYERS;

            if (!insideArena && !barrierSide) {
                for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                    mcwf::chunk::setBlock(outBlocks, lx, y, lz, ids.air);
                }
                outBiomes[static_cast<size_t>(lz * 16 + lx)] = static_cast<uint16_t>(BIOME_PLAINS);
                continue;
            }

            if (barrierSide) {
                for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                    int id = y >= barrierFloorStartY && y <= barrierRoofEndY ? ids.barrier : ids.air;
                    mcwf::chunk::setBlock(outBlocks, lx, y, lz, id);
                }
                outBiomes[static_cast<size_t>(lz * 16 + lx)] = static_cast<uint16_t>(BIOME_PLAINS);
                continue;
            }

            for (int y = barrierRoofEndY + 1; y < MIN_Y + WORLD_HEIGHT; ++y) {
                mcwf::chunk::setBlock(outBlocks, lx, y, lz, ids.air);
            }

            bool wall = atAreaWall(x, z, area);
            if (wall && patternWalls) {
                // Stripes run along the diagonal: the step advances one per block
                // along the wall and one per block up, so the repeat climbs at 45°.
                int along = (z == area.zmin || z == area.zmax) ? x : z;
                for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                    int id = ids.air;
                    if (y >= floorY && y <= glassEndY) {
                        int step = ((along + y) % patternLength + patternLength) % patternLength;
                        id = ids.fuhcWallPattern[static_cast<size_t>(step)];
                    }
                    mcwf::chunk::setBlock(outBlocks, lx, y, lz, id);
                }
            } else if (wall) {
                for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                    int id = ids.air;
                    if (y >= floorY && y <= bedrockEndY) {
                        id = ids.bedrock;
                    } else if (y >= glassStartY && y <= glassEndY) {
                        id = ids.glass;
                    }
                    mcwf::chunk::setBlock(outBlocks, lx, y, lz, id);
                }
            } else {
                mcwf::chunk::setBlock(outBlocks, lx, floorY, lz, ids.bedrock);
            }

            if (cfg.fuhcGlassRoof && yIndex(roofY) >= 0 && yIndex(roofY) < WORLD_HEIGHT) {
                mcwf::chunk::setBlock(outBlocks, lx, roofY, lz, roofId);
            }

            for (int y = barrierFloorStartY; y < floorY; ++y) {
                mcwf::chunk::setBlock(outBlocks, lx, y, lz, ids.barrier);
            }
            for (int y = barrierRoofStartY; y <= barrierRoofEndY; ++y) {
                mcwf::chunk::setBlock(outBlocks, lx, y, lz, ids.barrier);
            }
        }
    }

    std::array<FuhcSpawnPoint, 2> spawns = fuhcSpawnPoints(cfg, area);
    int chunkMinX = cx * 16;
    int chunkMaxX = chunkMinX + 15;
    int chunkMinZ = cz * 16;
    int chunkMaxZ = chunkMinZ + 15;

    for (int i = 0; i < 2; ++i) {
        const FuhcSpawnPoint& spawn = spawns[static_cast<size_t>(i)];
        if (spawn.x < chunkMinX || spawn.x > chunkMaxX ||
            spawn.z < chunkMinZ || spawn.z > chunkMaxZ) {
            continue;
        }

        int lx = spawn.x - chunkMinX;
        int lz = spawn.z - chunkMinZ;
        int groundY = findFuhcGround(outBlocks, ids, lx, lz, floorY, openTopY);
        clearSpawnBody(outBlocks, ids, lx, lz, groundY);
        spawnYs[static_cast<size_t>(i)] = groundY + 1;
    }
}

WorldSpawn fuhcWorldSpawn(const Config& cfg, const std::vector<Area>& areas,
                          const std::vector<std::array<int, 2>>& spawnYs) {
    if (areas.empty()) {
        return {0, cfg.fuhcBaseY + 1, 0};
    }

    const std::array<int, 2>* ys = spawnYs.empty() ? nullptr : &spawnYs[0];
    FuhcSpawnPoint first = fuhcSpawnPoints(cfg, areas[0], ys)[0];
    return {first.x, first.y, first.z};
}

void writeFuhcPositionsJson(const Config& cfg, const fs::path& outDir, const std::vector<Area>& areas,
                            const std::vector<std::array<int, 2>>& spawnYs) {
    std::ofstream out(outDir / "fuhc_arenas.json");
    out << "{\n";

    for (size_t i = 0; i < areas.size(); ++i) {
        const std::array<int, 2>* ys = i < spawnYs.size() ? &spawnYs[i] : nullptr;
        std::array<FuhcSpawnPoint, 2> spawns = fuhcSpawnPoints(cfg, areas[i], ys);
        out << "  \"fuhc" << (i + 1) << "\": {\n";
        out << "    \"mapSize\": " << areas[i].size << ",\n";
        out << "    \"spawnPoint1\": {\n";
        out << "      \"x\": " << spawns[0].x << ",\n";
        out << "      \"y\": " << spawns[0].y << ",\n";
        out << "      \"z\": " << spawns[0].z << "\n";
        out << "    },\n";
        out << "    \"spawnPoint2\": {\n";
        out << "      \"x\": " << spawns[1].x << ",\n";
        out << "      \"y\": " << spawns[1].y << ",\n";
        out << "      \"z\": " << spawns[1].z << "\n";
        out << "    }\n";
        out << "  }" << (i + 1 < areas.size() ? "," : "") << "\n";
    }

    out << "}\n";
}

void writeFuhcResultJson(const Config& cfg, const fs::path& outDir, const std::vector<Area>& areas,
                         const std::vector<std::array<int, 2>>& spawnYs, const WorldSpawn& spawn,
                         int chunks, int regions, int workers, double seconds) {
    (void)spawnYs;
    int xmin, xmax, zmin, zmax;
    worldBounds(areas, xmin, xmax, zmin, zmax);
    int64_t totalArenaBlocks = 0;
    for (const Area& area : areas) {
        totalArenaBlocks += static_cast<int64_t>(area.size) * area.size;
    }

    std::ofstream out(outDir / "generator_result.json");
    out << "{\n";
    out << "  \"generator\": \"fuhc\",\n";
    out << "  \"world_generator_type\": \"void\",\n";
    out << "  \"root_seed\": " << cfg.seed << ",\n";
    out << "  \"arena_seed_mode\": \"derived_per_arena\",\n";
    out << "  \"biome_assignment_mode\": \"weighted_exact_quota_spread_with_balanced_climate_continents\",\n";
    out << "  \"arena_seeds\": [";
    for (size_t i = 0; i < areas.size(); ++i) {
        out << fuhcArenaSeed(cfg, areas[i]);
        if (i + 1 < areas.size()) {
            out << ", ";
        }
    }
    out << "],\n";
    out << "  \"arena_biomes\": [";
    for (size_t i = 0; i < areas.size(); ++i) {
        out << "\"" << fuhcBiomeName(areas[i].biome) << "\"";
        if (i + 1 < areas.size()) {
            out << ", ";
        }
    }
    out << "],\n";
    out << "  \"arena_map_sizes\": [";
    for (size_t i = 0; i < areas.size(); ++i) {
        out << areas[i].size;
        if (i + 1 < areas.size()) {
            out << ", ";
        }
    }
    out << "],\n";
    out << "  \"arena_glass_wall_heights\": [";
    for (size_t i = 0; i < areas.size(); ++i) {
        out << fuhcGlassWallHeight(cfg, areas[i]);
        if (i + 1 < areas.size()) {
            out << ", ";
        }
    }
    out << "],\n";
    out << "  \"large_arena_biome_threshold\": 50,\n";
    out << "  \"arena_biome_modes\": [";
    for (size_t i = 0; i < areas.size(); ++i) {
        out << "\"" << (fuhcArenaUsesBiomeBlend(areas[i]) ? "exact_quota_balanced_climate_continents" : "single_biome") << "\"";
        if (i + 1 < areas.size()) {
            out << ", ";
        }
    }
    out << "],\n";
    out << "  \"large_arena_biome_temperature_order\": [\"cold_continent:snow+plains_forest_taiga\", \"warm_continent:rare+desert\"],\n";
    out << "  \"large_arena_biome_blend_scale_blocks\": 50,\n";
    out << "  \"arena_biome_column_distribution\": [\n";
    for (size_t i = 0; i < areas.size(); ++i) {
        const Area& area = areas[i];
        std::array<int, FUHC_BIOME_GROUP_COUNT> arenaCounts{};
        if (area.fuhcBiomeMap) {
            for (uint8_t biome : *area.fuhcBiomeMap) {
                int group = fuhcBiomeGroupForBiome(static_cast<int>(biome));
                ++arenaCounts[static_cast<size_t>(group)];
            }
        } else {
            int interiorWidth = std::max(0, area.xmax - area.xmin - 1);
            int interiorDepth = std::max(0, area.zmax - area.zmin - 1);
            int group = clampInt(area.biomeGroup, 0, FUHC_BIOME_GROUP_COUNT - 1);
            arenaCounts[static_cast<size_t>(group)] = interiorWidth * interiorDepth;
        }
        out << "    {";
        for (int group = 0; group < FUHC_BIOME_GROUP_COUNT; ++group) {
            out << "\"" << fuhcBiomeGroupName(group) << "\": "
                << arenaCounts[static_cast<size_t>(group)];
            if (group + 1 < FUHC_BIOME_GROUP_COUNT) {
                out << ", ";
            }
        }
        out << "}" << (i + 1 < areas.size() ? "," : "") << "\n";
    }
    out << "  ],\n";
    out << "  \"arena_biome_variant_distribution\": [\n";
    for (size_t i = 0; i < areas.size(); ++i) {
        const Area& area = areas[i];
        std::vector<int> variantCounts(BIOMES.size(), 0);
        if (area.fuhcBiomeMap) {
            for (uint8_t biome : *area.fuhcBiomeMap) {
                if (biome < variantCounts.size()) {
                    ++variantCounts[static_cast<size_t>(biome)];
                }
            }
        } else {
            int interiorWidth = std::max(0, area.xmax - area.xmin - 1);
            int interiorDepth = std::max(0, area.zmax - area.zmin - 1);
            int biome = clampInt(area.biome, 0, static_cast<int>(BIOMES.size()) - 1);
            variantCounts[static_cast<size_t>(biome)] = interiorWidth * interiorDepth;
        }

        out << "    {";
        bool firstVariant = true;
        for (size_t biome = 0; biome < variantCounts.size(); ++biome) {
            if (variantCounts[biome] <= 0) {
                continue;
            }
            if (!firstVariant) {
                out << ", ";
            }
            firstVariant = false;
            out << "\"" << fuhcBiomeName(static_cast<int>(biome)) << "\": "
                << variantCounts[biome];
        }
        out << "}" << (i + 1 < areas.size() ? "," : "") << "\n";
    }
    out << "  ],\n";
    std::array<int, FUHC_BIOME_GROUP_COUNT> biomeCounts{};
    for (const Area& area : areas) {
        int group = clampInt(area.biomeGroup, 0, FUHC_BIOME_GROUP_COUNT - 1);
        ++biomeCounts[static_cast<size_t>(group)];
    }
    out << "  \"biome_distribution\": {\n";
    for (int group = 0; group < FUHC_BIOME_GROUP_COUNT; ++group) {
        out << "    \"" << fuhcBiomeGroupName(group) << "\": "
            << biomeCounts[static_cast<size_t>(group)]
            << (group + 1 < FUHC_BIOME_GROUP_COUNT ? "," : "") << "\n";
    }
    out << "  },\n";
    out << "  \"arenas\": " << areas.size() << ",\n";
    out << "  \"arena_gap\": " << cfg.fuhcArenaGap << ",\n";
    out << "  \"barrier_layers\": " << FUHC_BARRIER_LAYERS << ",\n";
    out << "  \"barrier_shell\": \"full_double_wrap\",\n";
    out << "  \"effective_tree_density\": " << (cfg.treeDensity / 3.0) << ",\n";
    out << "  \"surface_profile\": \"seeded_warped_pvp_terraces\",\n";
    out << "  \"surface_profile_variants\": [";
    for (size_t i = 0; i < areas.size(); ++i) {
        int arenaSeed = fuhcArenaSeed(cfg, areas[i]);
        int profile = fuhcSurfaceProfileIndex(arenaSeed);
        out << "\"" << fuhcSurfaceProfileName(profile) << "\"";
        if (i + 1 < areas.size()) {
            out << ", ";
        }
    }
    out << "],\n";
    out << "  \"surface_min_y\": " << cfg.fuhcBaseY << ",\n";
    out << "  \"surface_max_y\": "
        << (cfg.fuhcBaseY + (cfg.fuhcSurfaceRelief > 0.0 ? 1 : 0)) << ",\n";
    out << "  \"surface_fluid_ditches\": false,\n";
    out << "  \"arena_blocks_total\": " << totalArenaBlocks << ",\n";
    out << "  \"chunks\": " << chunks << ",\n";
    out << "  \"regions\": " << regions << ",\n";
    out << "  \"workers\": " << workers << ",\n";
    out << "  \"job_mode\": \"native_fuhc_chunk_grid\",\n";
    out << "  \"output_format\": \"" << cfg.outputFormat << "\",\n";
    out << "  \"spawn\": [" << spawn.x << ", " << spawn.y << ", " << spawn.z << "],\n";
    out << "  \"bounds\": [" << xmin << ", " << xmax << "],\n";
    out << "  \"world_bounds\": [" << xmin << ", " << xmax << ", " << zmin << ", " << zmax << "],\n";
    out << "  \"positions_file\": \"fuhc_arenas.json\",\n";
    out << "  \"seconds\": " << seconds << "\n";
    out << "}\n";
}
