#include "mcwf/terrain.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <string>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"
#include "internal/terrain_biome.hpp"
#include "internal/terrain_climate.hpp"
#include "internal/terrain_grid.hpp"
#include "internal/terrain_height.hpp"
#include "internal/terrain_water.hpp"

using namespace mcwf::terrain;

namespace {

int topBlockFor(const Config& cfg, const Blocks& blocks, const BlockIds& ids, int biome, bool snowy, bool desert, bool badlands, bool beach, double mountain, int height) {
    (void)blocks;
    if (beach) {
        return ids.sand;
    }
    if (desert) {
        return ids.sand;
    }
    if (badlands) {
        return ids.redSand;
    }

    if (biome == BIOME_SNOW) {
        bool alpine = height >= cfg.baseHeight + 31 || mountain > 0.34;
        if (!alpine) {
            return ids.grassSnow;
        }

        // Snowy mountains should read as stone mountains with a snow cap, not
        // giant solid snow hills with tiny stone teeth on top. The old native
        // rule was effectively inverted for the screenshots the web app showed.
        bool cap = height >= cfg.snowlineY - 3 || height >= cfg.baseHeight + 70;

        return cap ? ids.snowBlock : ids.stone;
    }

    if (snowy) {
        return ids.grassSnow;
    }
    if (biome == BIOME_TAIGA) {
        return ids.podzol;
    }
    if (biome == BIOME_SWAMP) {
        return ids.grass;
    }

    return ids.grass;
}

int subBlockFor(const Config& cfg, const BlockIds& ids, int biome, bool desert, bool badlands, bool beach, double mountain, int height) {
    if (beach) {
        return ids.sandstone;
    }
    if (desert) {
        return ids.sandstone;
    }
    if (badlands) {
        return ids.terracotta;
    }
    if (biome == BIOME_SNOW) {
        bool alpine = height >= cfg.baseHeight + 31 || mountain > 0.34;

        return alpine ? ids.stone : ids.dirt;
    }

    return ids.dirt;
}

}  // namespace

TerrainSample sampleTerrain(const Config& cfg, int x, int z) {
    RawTerrain terrain = terrainHeight(cfg, static_cast<double>(x), static_cast<double>(z));
    double h = terrain.h;
    double mnt = terrain.mountain;

    Climate climate = climateFields(cfg, static_cast<double>(x), static_cast<double>(z), h);
    BiomeChoice choice = classifyBiome(cfg, static_cast<double>(x), static_cast<double>(z), h, mnt, climate);
    int biome = choice.biome;
    bool isDesert = biome == BIOME_DESERT;
    bool isBadlands = biome == BIOME_BADLANDS;
    bool isArid = isDesert || isBadlands;

    double coldRelief = smoothstep01((((cfg.biomeColdCut - 0.025) + 0.14) - choice.tempBand) / 0.34);
    coldRelief *= 1.0 - smoothstep01((choice.humBand - 0.94) / 0.12);
    double frozenRelief = smoothstep01((((cfg.biomeFrozenCut - 0.055) + 0.070) - choice.tempBand) / 0.20);
    frozenRelief *= 1.0 - smoothstep01((choice.humBand - 0.965) / 0.11);
    if (biome == BIOME_SNOW) {
        frozenRelief = std::max(frozenRelief, 0.92);
    }

    double coldLumps = (fbm2d(x + 5100.0, z - 5100.0, cfg.seed + 74, 3, 1.0 / 150.0) - 0.5) * 2.0;
    double coldRidges = 1.0 - std::abs(fbm2d(x - 9700.0, z + 9700.0, cfg.seed + 75, 3, 1.0 / 220.0) - 0.5) * 2.0;
    coldRidges = smoothstep01((coldRidges - 0.24) / 0.66);

    double snowRange = fbm2d(x + 14800.0, z - 11800.0, cfg.seed + 176, 4, 1.0 / 430.0);
    snowRange = smoothstep01((snowRange - 0.31) / 0.45);
    double snowRidges = 1.0 - std::abs(fbm2d(x - 18100.0, z + 12700.0, cfg.seed + 177, 4, 1.0 / 185.0) - 0.5) * 2.0;
    snowRidges = std::pow(smoothstep01((snowRidges - 0.20) / 0.64), 1.14);
    double snowPeaks = 1.0 - std::abs(fbm2d(x + 22100.0, z - 16100.0, cfg.seed + 178, 3, 1.0 / 92.0) - 0.5) * 2.0;
    snowPeaks = std::pow(smoothstep01((snowPeaks - 0.34) / 0.52), 1.28);
    double snowRoll = (fbm2d(x - 23300.0, z + 17500.0, cfg.seed + 179, 3, 1.0 / 125.0) - 0.5) * 2.0;

    double snowAlpine = frozenRelief * (0.52 + 0.48 * snowRange);
    h += coldRelief * (coldLumps * cfg.hillAmp * 0.30 + coldRidges * cfg.mountainAmp * 0.14 * (0.30 + 0.70 * mnt));
    h += snowAlpine * (snowRoll * cfg.hillAmp * 0.58 +
                       snowRange * cfg.mountainAmp * 0.14 +
                       snowRidges * cfg.mountainAmp * 0.46 +
                       snowRidges * snowPeaks * cfg.mountainAmp * 0.46);
    mnt = clampDouble(std::max(mnt, snowAlpine * (snowRidges * 0.70 + snowPeaks * 0.30)), 0.0, 1.0);

    FeatureField river = finiteRiverField(cfg, static_cast<double>(x), static_cast<double>(z), mnt, choice.tempBand, choice.humBand);
    FeatureField lake = finiteLakeField(cfg, static_cast<double>(x), static_cast<double>(z), mnt, choice.tempBand, choice.humBand);
    FeatureField lava = lavaPoolField(cfg, static_cast<double>(x), static_cast<double>(z), mnt, choice.tempBand, choice.humBand);

    int featureLevel = lake.core > river.core ? lake.level : river.level;
    double featureSurface = static_cast<double>(featureLevel);
    double preWater = h;
    double cutToSurface = preWater - featureSurface;
    double mountainWaterGate = 1.0 - smoothstep01((mnt - 0.30) / 0.22);
    double shallowGate = smoothstep01((cutToSurface + 0.10) / 0.45) * (1.0 - smoothstep01((cutToSurface - 2.20) / 0.90));
    double waterGate = clampDouble(mountainWaterGate * shallowGate, 0.0, 1.0);

    river.core *= waterGate;
    river.bank *= 0.16 + 0.84 * waterGate;
    lake.core *= waterGate;
    lake.bank *= 0.18 + 0.82 * waterGate;

    bool riverWater = river.core > 0.185;
    bool lakeWater = lake.core > 0.315;
    if (biome == BIOME_SWAMP && mnt < 0.26) {
        double marsh = fbm2d(static_cast<double>(x) + 19100.0, static_cast<double>(z) - 8700.0, cfg.seed + 5327, 3, 1.0 / 38.0);
        if (marsh > 0.665) {
            lakeWater = true;
            lake.core = std::max(lake.core, 0.42);
            lake.bank = std::max(lake.bank, 0.58);
            featureLevel = static_cast<int>(std::round(h - 0.10));
            featureSurface = static_cast<double>(featureLevel);
        }
    }
    bool waterFeature = riverWater || lakeWater;
    double bankStrength = clampDouble(river.bank * 1.18 + lake.bank * 1.08, 0.0, 1.0);
    double bankNoise = (fbm2d(x + 330.0, z - 660.0, cfg.seed + 73, 2, 1.0 / 44.0) - 0.5) * 0.18;

    double floodplain = bankStrength * (1.0 - smoothstep01((std::abs(preWater - featureSurface) - 3.6) / 3.0));
    floodplain = clampDouble(floodplain, 0.0, 1.0);
    double floodplainTarget = featureSurface + 0.14 + bankNoise;
    h = h * (1.0 - floodplain * 0.46) + floodplainTarget * (floodplain * 0.46);

    double bankTarget = featureSurface + 0.22 + bankNoise;
    double bankDelta = std::max(0.0, h - bankTarget);
    double bankCut = std::min(bankDelta, 1.85) * bankStrength * 0.64;
    h -= bankCut;

    double riverDepth = 0.82 + 0.42 * clampDouble(river.core, 0.0, 1.0);
    double lakeDepth = 0.90 + 0.58 * clampDouble(lake.core, 0.0, 1.0);
    double wantedBed = lake.core > river.core ? featureSurface - lakeDepth : featureSurface - riverDepth;
    double maxCut = 1.72 + 0.32 * clampDouble(lake.core, 0.0, 1.0);
    double bed = std::max(wantedBed, preWater - maxCut);
    bed = std::min(bed, featureSurface - 1.0);
    if (waterFeature) {
        h = std::min(h, bed);
    }

    double lavaSurface = static_cast<double>(lava.level);
    double lavaCut = h - lavaSurface;
    double lavaGate = (1.0 - clampDouble(bankStrength * 1.55, 0.0, 1.0));
    lavaGate *= 1.0 - smoothstep01((std::abs(h - lavaSurface) - 2.4) / 1.2);
    lava.core *= clampDouble(lavaGate, 0.0, 1.0);
    lava.bank *= clampDouble(lavaGate, 0.0, 1.0);
    bool lavaPool = lava.core > 0.425;
    double lavaBankStrength = clampDouble(lava.bank, 0.0, 1.0);
    double lavaTarget = lavaSurface + 0.08;
    h = h * (1.0 - lavaBankStrength * 0.32) + lavaTarget * (lavaBankStrength * 0.32);
    double lavaBed = std::min(lavaSurface - 1.0, h - clampDouble(lavaCut, 0.0, 1.35));
    lavaBed = std::min(lavaBed, lavaSurface - 1.0);
    if (lavaPool) {
        h = std::min(h, lavaBed);
    }

    int height = static_cast<int>(std::round(clampDouble(h, cfg.floorY + 3, MIN_Y + WORLD_HEIGHT - 40)));
    if (waterFeature) {
        height = std::min(height, featureLevel - 1);
    }
    if (lavaPool) {
        height = std::min(height, lava.level - 1);
    }

    bool seaWater = false;
    bool isLava = lavaPool && !waterFeature && !seaWater;
    bool isWater = waterFeature && !isLava;
    int waterSurface = cfg.waterLevel;
    if (waterFeature) {
        waterSurface = featureLevel;
    }
    waterSurface = isWater ? std::max(waterSurface, height + 1) : height;
    int lavaSurf = isLava ? std::max(lava.level, height + 1) : height;

    bool isRiver = riverWater && isWater;
    if (isRiver) {
        biome = BIOME_RIVER;
    }

    bool localShore = river.bank > 0.110 || lake.bank > 0.135;
    bool seaShore = false;
    bool isBeach = !isWater && !isLava && (seaShore || localShore) && biome != BIOME_SNOW && biome != BIOME_TAIGA;
    if (isBeach) {
        biome = BIOME_RIVER;
    }

    bool frozenLand = biome == BIOME_SNOW || (choice.tempBand < (cfg.biomeFrozenCut - 0.055) && !isArid);
    bool alpineSnow = frozenLand && (height >= cfg.baseHeight + 31 || (height >= cfg.baseHeight + 22 && mnt > 0.34));
    if (alpineSnow) {
        biome = BIOME_SNOW;
    }
    bool snowy = !isArid && !isWater && ((height >= cfg.snowlineY + 6) || frozenLand || alpineSnow);

    TerrainSample out;
    out.height = height;
    out.biome = biome;
    out.river = isRiver;
    out.lake = lakeWater && isWater;
    out.lava = isLava;
    out.beach = isBeach;
    out.waterSurface = waterSurface;
    out.lavaSurface = lavaSurf;
    out.badlands = biome == BIOME_BADLANDS;
    out.desert = biome == BIOME_DESERT;
    out.snowy = snowy;
    out.mountain = mnt;
    out.badlandsStrength = choice.badlands;
    out.topId = 0;
    out.subId = 0;

    return out;
}

void sampleTerrainChunk(const Config& cfg, const Blocks& blocks, const BlockIds& ids, int cx, int cz,
                        std::array<std::array<TerrainSample, 16>, 16>& out) {
    (void)blocks;

    constexpr int margin = 10;
    constexpr int w = 16 + margin * 2;
    constexpr int h = 16 + margin * 2;
    int x0 = cx * 16 - margin;
    int z0 = cz * 16 - margin;

    GridD hf;
    GridD mnt;
    terrainHeightGrid(cfg, x0, z0, w, h, hf, mnt);

    GridD temp(w, h);
    GridD hum(w, h);
    GridD selector(w, h);
    GridD edge(w, h);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            Climate c = climateFields(cfg, static_cast<double>(x0 + x), static_cast<double>(z0 + z), hf.at(z, x));
            temp.at(z, x) = c.temp;
            hum.at(z, x) = c.hum;
            selector.at(z, x) = c.selector;
            edge.at(z, x) = c.edge;
        }
    }
    smoothClimateGrid(temp, hum, selector, edge);

    GridD tempBand(w, h);
    GridD humBand(w, h);
    GridD bad(w, h);
    std::vector<int> biome(static_cast<size_t>(w * h), BIOME_PLAINS);
    auto bio = [&](int z, int x) -> int& {
        return biome[static_cast<size_t>(z * w + x)];
    };

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            Climate c{temp.at(z, x), hum.at(z, x), selector.at(z, x), edge.at(z, x)};
            BiomeChoice b = classifyBiome(cfg, static_cast<double>(x0 + x), static_cast<double>(z0 + z), hf.at(z, x), mnt.at(z, x), c);
            bio(z, x) = b.biome;
            tempBand.at(z, x) = b.tempBand;
            humBand.at(z, x) = b.humBand;
            bad.at(z, x) = b.badlands;
        }
    }
    applyBiomeGridSafety(cfg, x0, z0, hf, mnt, tempBand, humBand, bad, biome, w, h);
    cleanBiomeGrid(biome, w, h);

    // Make true extreme hills reliably reach the snow-cap band while keeping
    // the later slope limiter responsible for softening the silhouette.
    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            double extremeHill = smoothstep01((mnt.at(z, x) - 0.76) / 0.18);
            if (extremeHill <= 0.0) {
                continue;
            }
            double target = static_cast<double>(cfg.snowlineY + 8) + extremeHill * 10.0;
            double missing = std::max(0.0, target - hf.at(z, x));
            hf.at(z, x) += missing * extremeHill * 0.34;
        }
    }

    GridD frozenPaint(w, h);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            frozenPaint.at(z, x) = bio(z, x) == BIOME_SNOW ? 1.0 : 0.0;
        }
    }
    GridD frozenRegion = softMaskGrid(frozenPaint, 4);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            int wx = x0 + x;
            int wz = z0 + z;
            double coldRelief = smoothstep01((((cfg.biomeColdCut - 0.025) + 0.14) - tempBand.at(z, x)) / 0.34);
            coldRelief *= 1.0 - smoothstep01((humBand.at(z, x) - 0.94) / 0.12);

            double frozenRelief = smoothstep01((((cfg.biomeFrozenCut - 0.055) + 0.070) - tempBand.at(z, x)) / 0.20);
            frozenRelief *= 1.0 - smoothstep01((humBand.at(z, x) - 0.965) / 0.11);
            frozenRelief = std::max(frozenRelief, frozenRegion.at(z, x) * 0.92);

            double coldLumps = (fbm2d(wx + 5100.0, wz - 5100.0, cfg.seed + 74, 3, 1.0 / 150.0) - 0.5) * 2.0;
            double coldRidges = 1.0 - std::abs(fbm2d(wx - 9700.0, wz + 9700.0, cfg.seed + 75, 3, 1.0 / 220.0) - 0.5) * 2.0;
            coldRidges = smoothstep01((coldRidges - 0.24) / 0.66);

            double snowRange = fbm2d(wx + 14800.0, wz - 11800.0, cfg.seed + 176, 4, 1.0 / 430.0);
            snowRange = smoothstep01((snowRange - 0.31) / 0.45);
            double snowRidges = 1.0 - std::abs(fbm2d(wx - 18100.0, wz + 12700.0, cfg.seed + 177, 4, 1.0 / 185.0) - 0.5) * 2.0;
            snowRidges = std::pow(smoothstep01((snowRidges - 0.20) / 0.64), 1.14);
            double snowPeaks = 1.0 - std::abs(fbm2d(wx + 22100.0, wz - 16100.0, cfg.seed + 178, 3, 1.0 / 92.0) - 0.5) * 2.0;
            snowPeaks = std::pow(smoothstep01((snowPeaks - 0.34) / 0.52), 1.28);
            double snowRoll = (fbm2d(wx - 23300.0, wz + 17500.0, cfg.seed + 179, 3, 1.0 / 125.0) - 0.5) * 2.0;

            double snowAlpine = frozenRelief * (0.52 + 0.48 * snowRange);
            hf.at(z, x) += coldRelief * (coldLumps * cfg.hillAmp * 0.30 + coldRidges * cfg.mountainAmp * 0.14 * (0.30 + 0.70 * mnt.at(z, x)));
            hf.at(z, x) += snowAlpine * (snowRoll * cfg.hillAmp * 0.58 +
                                          snowRange * cfg.mountainAmp * 0.14 +
                                          snowRidges * cfg.mountainAmp * 0.46 +
                                          snowRidges * snowPeaks * cfg.mountainAmp * 0.46);
            mnt.at(z, x) = clampDouble(std::max(mnt.at(z, x), snowAlpine * (snowRidges * 0.70 + snowPeaks * 0.30)), 0.0, 1.0);
        }
    }

    GridD lowBlur = blurGrid(hf);
    GridD lowBlur2 = blurGrid(lowBlur);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            double lowlandFlat = 1.0 - smoothstep01((mnt.at(z, x) - 0.24) / 0.34);
            double lowSmoothed = hf.at(z, x) * 0.34 + lowBlur.at(z, x) * 0.34 + lowBlur2.at(z, x) * 0.32;
            hf.at(z, x) = hf.at(z, x) * (1.0 - lowlandFlat * 0.82) + lowSmoothed * (lowlandFlat * 0.82);
        }
    }
    hf = smoothFinalHeightGrid(hf);
    hf = limitMountainSlopeGrid(hf, mnt, 3);

    GridD preWater = hf;
    GridD localBlur = blurGrid(preWater);

    GridD finalHeight(w, h);
    GridD waterSurfaceGrid(w, h);
    GridD lavaSurfaceGrid(w, h);
    std::vector<uint8_t> isRiver(static_cast<size_t>(w * h), 0);
    std::vector<uint8_t> isLake(static_cast<size_t>(w * h), 0);
    std::vector<uint8_t> isLava(static_cast<size_t>(w * h), 0);
    std::vector<uint8_t> isBeach(static_cast<size_t>(w * h), 0);
    std::vector<uint8_t> isSnowy(static_cast<size_t>(w * h), 0);
    auto flag = [&](std::vector<uint8_t>& f, int z, int x) -> uint8_t& {
        return f[static_cast<size_t>(z * w + x)];
    };

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            int wx = x0 + x;
            int wz = z0 + z;
            FeatureField river = finiteRiverField(cfg, static_cast<double>(wx), static_cast<double>(wz), mnt.at(z, x), tempBand.at(z, x), humBand.at(z, x));
            FeatureField lake = finiteLakeField(cfg, static_cast<double>(wx), static_cast<double>(wz), mnt.at(z, x), tempBand.at(z, x), humBand.at(z, x));
            FeatureField lava = lavaPoolField(cfg, static_cast<double>(wx), static_cast<double>(wz), mnt.at(z, x), tempBand.at(z, x), humBand.at(z, x));

            int featureLevel = lake.core > river.core ? lake.level : river.level;
            double featureSurface = static_cast<double>(featureLevel);
            double cutToSurface = preWater.at(z, x) - featureSurface;
            double localSlope = std::abs(preWater.at(z, x) - localBlur.at(z, x));
            double mountainWaterGate = 1.0 - smoothstep01((mnt.at(z, x) - 0.30) / 0.22);
            double slopeWaterGate = 1.0 - smoothstep01((localSlope - 4.6) / 5.8);
            double shallowGate = smoothstep01((cutToSurface + 0.10) / 0.45) * (1.0 - smoothstep01((cutToSurface - 2.20) / 0.90));
            double waterGate = clampDouble(mountainWaterGate * slopeWaterGate * shallowGate, 0.0, 1.0);

            river.core *= waterGate;
            river.bank *= 0.16 + 0.84 * waterGate;
            lake.core *= waterGate;
            lake.bank *= 0.18 + 0.82 * waterGate;

            bool riverWater = river.core > 0.185;
            bool lakeWater = lake.core > 0.315;
            if (bio(z, x) == BIOME_SWAMP && mnt.at(z, x) < 0.28) {
                double marsh = fbm2d(static_cast<double>(wx) + 19100.0, static_cast<double>(wz) - 8700.0, cfg.seed + 5327, 3, 1.0 / 42.0);
                double puddle = fbm2d(static_cast<double>(wx) - 7100.0, static_cast<double>(wz) + 9900.0, cfg.seed + 5329, 2, 1.0 / 18.0);
                if (marsh > 0.61 && puddle > 0.34) {
                    lakeWater = true;
                    lake.core = std::max(lake.core, 0.46);
                    lake.bank = std::max(lake.bank, 0.62);
                    featureLevel = static_cast<int>(std::round(cfg.baseHeight - 2 + (marsh - 0.61) * 2.2));
                    featureSurface = static_cast<double>(featureLevel);
                }
            }
            bool waterFeature = riverWater || lakeWater;
            double bankStrength = clampDouble(river.bank * 1.18 + lake.bank * 1.08, 0.0, 1.0);
            double bankNoise = (fbm2d(wx + 330.0, wz - 660.0, cfg.seed + 73, 2, 1.0 / 44.0) - 0.5) * 0.18;

            double hval = hf.at(z, x);
            if (bio(z, x) == BIOME_SWAMP) {
                double swampNoise = (fbm2d(static_cast<double>(wx) - 4400.0, static_cast<double>(wz) + 3700.0, cfg.seed + 5328, 2, 1.0 / 60.0) - 0.5) * 1.25;
                double swampTarget = static_cast<double>(cfg.baseHeight - 2) + swampNoise;
                hval = hval * 0.18 + swampTarget * 0.82;
            }
            double floodplain = bankStrength * (1.0 - smoothstep01((std::abs(preWater.at(z, x) - featureSurface) - 3.6) / 3.0));
            floodplain = clampDouble(floodplain, 0.0, 1.0);
            double floodplainTarget = featureSurface + 0.14 + bankNoise;
            hval = hval * (1.0 - floodplain * 0.46) + floodplainTarget * (floodplain * 0.46);

            double bankTarget = featureSurface + 0.22 + bankNoise;
            double bankDelta = std::max(0.0, hval - bankTarget);
            double bankCut = std::min(bankDelta, 1.85) * bankStrength * 0.64;
            hval -= bankCut;

            double riverDepth = 0.82 + 0.42 * clampDouble(river.core, 0.0, 1.0);
            double lakeDepth = 0.90 + 0.58 * clampDouble(lake.core, 0.0, 1.0);
            double wantedBed = lake.core > river.core ? featureSurface - lakeDepth : featureSurface - riverDepth;
            double maxCut = 1.72 + 0.32 * clampDouble(lake.core, 0.0, 1.0);
            double bed = std::max(wantedBed, preWater.at(z, x) - maxCut);
            bed = std::min(bed, featureSurface - 1.0);
            if (waterFeature) {
                hval = std::min(hval, bed);
            }

            double lavaSurface = static_cast<double>(lava.level);
            double lavaCut = hval - lavaSurface;
            double lavaGate = 1.0 - clampDouble(bankStrength * 1.55, 0.0, 1.0);
            lavaGate *= 1.0 - smoothstep01((std::abs(hval - lavaSurface) - 2.4) / 1.2);
            lava.core *= clampDouble(lavaGate, 0.0, 1.0);
            lava.bank *= clampDouble(lavaGate, 0.0, 1.0);
            bool lavaPool = lava.core > 0.425;
            double lavaBankStrength = clampDouble(lava.bank, 0.0, 1.0);
            double lavaTarget = lavaSurface + 0.08;
            hval = hval * (1.0 - lavaBankStrength * 0.32) + lavaTarget * (lavaBankStrength * 0.32);
            double lavaBed = std::min(lavaSurface - 1.0, hval - clampDouble(lavaCut, 0.0, 1.35));
            lavaBed = std::min(lavaBed, lavaSurface - 1.0);
            if (lavaPool) {
                hval = std::min(hval, lavaBed);
            }

            int height = static_cast<int>(std::round(clampDouble(hval, cfg.floorY + 3, MIN_Y + WORLD_HEIGHT - 40)));
            if (waterFeature) {
                height = std::min(height, featureLevel - 1);
            }
            if (lavaPool) {
                height = std::min(height, lava.level - 1);
            }

            bool seaWater = false;
            bool water = waterFeature && !(lavaPool && !waterFeature && !seaWater);
            bool lavaFinal = lavaPool && !waterFeature && !seaWater;
            int waterSurface = waterFeature ? featureLevel : cfg.waterLevel;
            waterSurface = water ? std::max(waterSurface, height + 1) : height;
            int lavaSurf = lavaFinal ? std::max(lava.level, height + 1) : height;

            bool riverFinal = riverWater && water;
            if (riverFinal) {
                bio(z, x) = BIOME_RIVER;
            }

            bool localShore = river.bank > 0.110 || lake.bank > 0.135;
            bool seaShore = false;
            bool arid = bio(z, x) == BIOME_DESERT || bio(z, x) == BIOME_BADLANDS;
            bool beach = !water && !lavaFinal && (seaShore || localShore) && bio(z, x) != BIOME_SNOW && bio(z, x) != BIOME_TAIGA;
            if (beach) {
                bio(z, x) = BIOME_RIVER;
            }

            bool frozenLand = bio(z, x) == BIOME_SNOW || (tempBand.at(z, x) < (cfg.biomeFrozenCut - 0.055) && !arid);
            bool alpineSnow = frozenLand && (height >= cfg.baseHeight + 31 || (height >= cfg.baseHeight + 22 && mnt.at(z, x) > 0.34));
            if (alpineSnow) {
                bio(z, x) = BIOME_SNOW;
            }
            bool snowy = !arid && !water && ((height >= cfg.snowlineY + 6) || frozenLand || alpineSnow);

            finalHeight.at(z, x) = static_cast<double>(height);
            waterSurfaceGrid.at(z, x) = static_cast<double>(waterSurface);
            lavaSurfaceGrid.at(z, x) = static_cast<double>(lavaSurf);
            flag(isRiver, z, x) = riverFinal ? 1 : 0;
            flag(isLake, z, x) = lakeWater && water ? 1 : 0;
            flag(isLava, z, x) = lavaFinal ? 1 : 0;
            flag(isBeach, z, x) = beach ? 1 : 0;
            flag(isSnowy, z, x) = snowy ? 1 : 0;
        }
    }

    for (int lz = 0; lz < 16; ++lz) {
        for (int lx = 0; lx < 16; ++lx) {
            int gz = lz + margin;
            int gx = lx + margin;
            TerrainSample c;
            c.height = static_cast<int>(finalHeight.at(gz, gx));
            c.biome = bio(gz, gx);
            c.river = flag(isRiver, gz, gx) != 0;
            c.lake = flag(isLake, gz, gx) != 0;
            c.lava = flag(isLava, gz, gx) != 0;
            c.beach = flag(isBeach, gz, gx) != 0;
            c.waterSurface = static_cast<int>(waterSurfaceGrid.at(gz, gx));
            c.lavaSurface = static_cast<int>(lavaSurfaceGrid.at(gz, gx));
            c.badlands = c.biome == BIOME_BADLANDS;
            c.desert = c.biome == BIOME_DESERT;
            c.snowy = flag(isSnowy, gz, gx) != 0;
            c.mountain = mnt.at(gz, gx);
            c.badlandsStrength = bad.at(gz, gx);

            double neighborAvg = 0.0;
            int count = 0;

            for (int dz = -1; dz <= 1; ++dz) {
                for (int dx = -1; dx <= 1; ++dx) {
                    if (dx == 0 && dz == 0) {
                        continue;
                    }
                    neighborAvg += finalHeight.clamped(gz + dz, gx + dx);
                    ++count;
                }
            }
            neighborAvg /= static_cast<double>(std::max(1, count));
            double localRelief = std::abs(static_cast<double>(c.height) - neighborAvg);
            bool snowMountain = c.snowy && c.biome == BIOME_SNOW &&
                (c.height >= cfg.baseHeight + 31 || c.mountain > 0.34 || localRelief > 2.2);
            bool snowCap = snowMountain &&
                (c.height >= cfg.snowlineY - 3 || c.height >= cfg.baseHeight + 70 ||
                 (c.height >= cfg.baseHeight + 58 && localRelief < 3.2));
            c.topId = topBlockFor(cfg, blocks, ids, c.biome, c.snowy, c.desert, c.badlands, c.beach, c.mountain, c.height);
            c.subId = subBlockFor(cfg, ids, c.biome, c.desert, c.badlands, c.beach, c.mountain, c.height);
            if (snowMountain && !snowCap) {
                c.topId = ids.stone;
                c.subId = ids.stone;
            } else if (snowCap) {
                c.topId = ids.snowBlock;
                c.subId = ids.stone;
            }
            out[static_cast<size_t>(lz)][static_cast<size_t>(lx)] = c;
        }
    }
}

bool caveCarves(const Config& cfg, int x, int y, int z, int surfaceY, int floorY) {
    if (!cfg.caves) {
        return false;
    }
    if (y < floorY + 4 || y > surfaceY - 6) {
        return false;
    }

    double intensity = std::max(0.2, cfg.caveIntensity);
    int targetY = -12;

    for (const OreCfg& ore : cfg.ores) {
        if (ore.name.find("diamond") != std::string::npos) {
            targetY = std::min(ore.yMax - 2, ore.yMin + 4);
            break;
        }
    }
    targetY = std::max(floorY + 5, std::min(8, targetY));
    int lowerGate = std::max(floorY + 4, targetY - 10);
    if (cfg.deepDark && floorY <= MIN_Y + 1 && surfaceY <= cfg.floorY) {
        lowerGate = floorY + 4;
        targetY = std::max(floorY + 18, std::min(targetY, cfg.floorY - 16));
    }
    if (y > std::min(surfaceY - 7, cfg.waterLevel + 46) || y < lowerGate) {
        return false;
    }

    double xd = static_cast<double>(x);
    double yd = static_cast<double>(y);
    double zd = static_cast<double>(z);

    // Fast chunk-friendly form of the Python legacy cave pass. The exact Python
    // formula is still the reference, but executing all of its vectorized fBm
    // terms as scalar calls per block made native generation slower than NumPy.
    // This keeps the same seed space, vertical gates, and province/deep bias,
    // while cutting the per-block cave decision down to two fBm fields.
    double province = fbm2d(xd + 5000.0, zd - 5000.0, cfg.seed + 9306, 3, 1.0 / 165.0);
    double provinceGate = clampDouble((province - 0.33) / 0.27, 0.0, 1.0);
    if (provinceGate <= 0.02) {
        return false;
    }

    double belowSurface = clampDouble((static_cast<double>(surfaceY) - yd - 5.0) / 22.0, 0.0, 1.0);
    if (belowSurface <= 0.0) {
        return false;
    }
    double lowBias = clampDouble((static_cast<double>(cfg.waterLevel) + 18.0 - yd) / 70.0, 0.0, 1.0);
    double deepBias = clampDouble((static_cast<double>(targetY) + 34.0 - yd) / 42.0, 0.0, 1.0);
    double width = (0.032 + 0.018 * lowBias + 0.010 * deepBias) * intensity;
    width *= 0.54 + 0.78 * provinceGate;

    double n1 = fbm3d(xd, yd * 0.92, zd, cfg.seed + 9303, 2, 1.0 / 35.0);
    double n2 = fbm3d(xd - 109.0, yd + 47.0, zd + 83.0, cfg.seed + 9304, 1, 1.0 / 57.0);
    bool legacyTube = (std::abs(n1 - 0.5) < width) && (std::abs(n2 - 0.5) < width * 1.55);
    if (legacyTube) {
        return true;
    }

    // Ravines are handled as finite chunk feature segments in chunk.cpp.
    // Keeping the old scalar contour here creates world-spanning laser canyons.
    (void)xd;
    (void)yd;
    (void)zd;

    return false;
}
