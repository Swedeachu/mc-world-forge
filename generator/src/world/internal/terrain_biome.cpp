#include "internal/terrain_biome.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <string>
#include <vector>

#include "mcwf/blocks.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/terrain.hpp"
#include "mcwf/util.hpp"

const std::vector<std::string> BIOMES = {
    "minecraft:plains",
    "minecraft:forest",
    "minecraft:birch_forest",
    "minecraft:desert",
    "minecraft:savanna",
    "minecraft:jungle",
    "minecraft:snowy_plains",
    "minecraft:taiga",
    "minecraft:badlands",
    "minecraft:river",
    "minecraft:swamp",
    "minecraft:windswept_hills",
    "minecraft:deep_dark",
    "minecraft:nether_wastes",
    "minecraft:crimson_forest",
    "minecraft:warped_forest",
    "minecraft:soul_sand_valley",
    "minecraft:basalt_deltas",
};

namespace mcwf::terrain {

namespace {

double badlandsStrength(const Config& cfg, double x, double z, double tempBand, double humBand) {
    double aridCore = fbm2d(x - 5005.0, z + 7077.0, cfg.seed + 77, 4, 1.0 / 620.0);
    double aridWarp = (fbm2d(x + 3030.0, z - 9090.0, cfg.seed + 177, 2, 1.0 / 240.0) - 0.5) * 0.18;
    double dryGate = smoothstep01((cfg.biomeDesertHumCut + 0.100 - humBand) / 0.24);
    double warmGate = smoothstep01((tempBand - (cfg.biomeWarmCut - 0.055)) / 0.24);
    double province = smoothstep01((aridCore + aridWarp - 0.340) / 0.30);

    return clampDouble(province * dryGate * warmGate, 0.0, 1.0);
}

struct ProvinceChoice {
    int biome = BIOME_PLAINS;
    double strength = 0.0;
};

BiomeChoice forcedBiomeChoice(int biome) {
    switch (biome) {
        case BIOME_DESERT:
            return {BIOME_DESERT, 0.88, 0.16, 0.0};
        case BIOME_SAVANNA:
            return {BIOME_SAVANNA, 0.82, 0.34, 0.0};
        case BIOME_JUNGLE:
            return {BIOME_JUNGLE, 0.84, 0.91, 0.0};
        case BIOME_SNOW:
            return {BIOME_SNOW, 0.08, 0.46, 0.0};
        case BIOME_TAIGA:
            return {BIOME_TAIGA, 0.27, 0.58, 0.0};
        case BIOME_BADLANDS:
            return {BIOME_BADLANDS, 0.90, 0.12, 0.88};
        case BIOME_SWAMP:
            return {BIOME_SWAMP, 0.74, 0.92, 0.0};
        case BIOME_FOREST:
            return {BIOME_FOREST, 0.54, 0.76, 0.0};
        case BIOME_BIRCH:
            return {BIOME_BIRCH, 0.56, 0.68, 0.0};
        case BIOME_PLAINS:
        default:
            return {BIOME_PLAINS, 0.58, 0.50, 0.0};
    }
}

double weightScore(double weight, double field) {
    weight = std::max(0.001, weight);

    return std::log(weight) + (field - 0.5) * 1.55;
}

ProvinceChoice balancedProvinceBiome(const Config& cfg, double x, double z) {
    // Smooth weighted biome chooser. The earlier Voronoi province pass created
    // hard polygon wedges and skinny strips. This uses broad warped score fields
    // plus user-facing target weights, so transitions are rounded and the 1k
    // world mix is biased toward the requested biome groups.
    double total = std::max(0.001, cfg.biomePlainsForestWeight + cfg.biomeDesertWeight + cfg.biomeSnowWeight + cfg.biomeRareWeight);
    double plainsW = cfg.biomePlainsForestWeight / total * 100.0;
    double desertW = cfg.biomeDesertWeight / total * 100.0;
    double snowW = cfg.biomeSnowWeight / total * 100.0;
    double rareW = cfg.biomeRareWeight / total * 100.0;

    double warpX = (fbm2d(x + 1717.0, z - 2626.0, cfg.seed + 26101, 4, 1.0 / 720.0) - 0.5) * 145.0;
    double warpZ = (fbm2d(x - 3131.0, z + 1515.0, cfg.seed + 26102, 4, 1.0 / 720.0) - 0.5) * 145.0;
    double px = x + warpX;
    double pz = z + warpZ;

    double plainsField = fbm2d(px + 1000.0, pz - 1000.0, cfg.seed + 26210, 4, 1.0 / 470.0);
    double desertField = fbm2d(px - 3000.0, pz + 2200.0, cfg.seed + 26220, 4, 1.0 / 520.0);
    double snowField = fbm2d(px + 4700.0, pz - 5800.0, cfg.seed + 26230, 4, 1.0 / 610.0);
    double rareField = fbm2d(px - 8200.0, pz + 7300.0, cfg.seed + 26240, 4, 1.0 / 420.0);

    double plainsScore = weightScore(plainsW, plainsField) + 0.30;
    double desertScore = weightScore(desertW, desertField) - 0.08;
    double snowScore = weightScore(snowW, snowField) - 0.14;
    double rareScore = weightScore(rareW, rareField) - 0.32;

    int group = 0;
    double best = plainsScore;
    double second = -1.0e30;
    auto offer = [&](int g, double s) {
        if (s > best) {
            second = best;
            best = s;
            group = g;
        } else if (s > second) {
            second = s;
        }
    };
    offer(1, desertScore);
    offer(2, snowScore);
    offer(3, rareScore);

    double margin = best - second;
    double strength = smoothstep01((margin - 0.035) / 0.42);

    double sub = fbm2d(px + 11000.0, pz - 9000.0, cfg.seed + 26300, 3, 1.0 / 260.0);
    if (group == 1) {
        return {BIOME_DESERT, strength};
    }
    if (group == 2) {
        return {BIOME_SNOW, strength};
    }
    if (group == 3) {
        double rareSub = fbm2d(px - 12700.0, pz + 9100.0, cfg.seed + 26310, 3, 1.0 / 260.0);
        if (rareSub < 0.44) {
            return {BIOME_SWAMP, strength};
        }
        if (rareSub < 0.72) {
            return {BIOME_JUNGLE, strength};
        }

        return {BIOME_BADLANDS, strength};
    }

    if (sub < 0.40) return {BIOME_PLAINS, strength};
    if (sub < 0.58) return {BIOME_FOREST, strength};
    if (sub < 0.74) return {BIOME_BIRCH, strength};

    return {BIOME_TAIGA, strength};
}

}  // namespace

BiomeChoice classifyBiome(const Config& cfg, double x, double z, double h, double mnt, const Climate& c) {
    if (cfg.biomeOverride >= 0) {
        return forcedBiomeChoice(cfg.biomeOverride);
    }

    double tempBand = clampDouble(c.temp + c.edge * 0.018, 0.0, 1.0);
    double humBand = clampDouble(c.hum + (fbm2d(x - 818.0, z + 181.0, cfg.seed + 75, 2, 1.0 / 190.0) - 0.5) * 0.040, 0.0, 1.0);
    double frozenCut = std::max(0.08, cfg.biomeFrozenCut - 0.055);
    double coldCut = std::max(frozenCut + 0.10, cfg.biomeColdCut - 0.025);
    double warmCut = cfg.biomeWarmCut - 0.025;
    double forestStyle = fbm2d(x + 9191.0, z - 3939.0, cfg.seed + 76, 3, 1.0 / 240.0);
    double dryProvince = fbm2d(x - 14200.0, z + 9200.0, cfg.seed + 576, 3, 1.0 / 520.0);
    double jungleProvince = fbm2d(x + 18300.0, z - 11100.0, cfg.seed + 577, 3, 1.0 / 470.0);
    double taigaProvince = fbm2d(x - 7600.0, z - 15100.0, cfg.seed + 578, 3, 1.0 / 500.0);
    double bad = badlandsStrength(cfg, x, z, tempBand, humBand);

    int biome = BIOME_PLAINS;
    bool frozen = tempBand < frozenCut;
    bool cold = tempBand >= frozenCut && tempBand < coldCut;
    bool temperate = tempBand >= coldCut && tempBand < warmCut;
    bool warm = tempBand >= warmCut;

    if (frozen) {
        biome = humBand < 0.48 ? BIOME_SNOW : BIOME_TAIGA;
    } else if (cold) {
        if (humBand >= 0.47 || taigaProvince > 0.50) {
            biome = BIOME_TAIGA;
        } else if (humBand >= 0.38 && c.selector > 0.31) {
            biome = forestStyle < 0.40 ? BIOME_BIRCH : BIOME_FOREST;
        } else {
            biome = BIOME_PLAINS;
        }
    } else if (temperate) {
        bool dryTemperate = tempBand > coldCut + 0.10 && humBand < cfg.biomeDesertHumCut + 0.11 && dryProvince > 0.50;
        bool jungleTemperate = tempBand > coldCut + 0.07 && humBand >= cfg.biomeForestHumCut + 0.08 && jungleProvince > 0.48;
        bool taigaTemperate = tempBand < coldCut + 0.115 && humBand >= 0.46 && taigaProvince > 0.45;
        if (dryTemperate) {
            biome = bad > 0.46 || dryProvince > 0.77 ? BIOME_BADLANDS : BIOME_DESERT;
        } else if (jungleTemperate || (humBand >= cfg.biomeJungleHumCut - 0.05 && c.selector > 0.43)) {
            biome = BIOME_JUNGLE;
        } else if (taigaTemperate) {
            biome = BIOME_TAIGA;
        } else {
            bool forest = humBand >= cfg.biomeForestHumCut - 0.10 && c.selector > 0.22;
            if (forest) {
                biome = forestStyle < 0.43 ? BIOME_BIRCH : BIOME_FOREST;
            }
        }
    } else if (warm) {
        biome = BIOME_PLAINS;
        bool dryHot = tempBand >= warmCut - 0.010 && humBand < cfg.biomeDesertHumCut + 0.075;
        dryHot = dryHot || (tempBand >= warmCut - 0.040 && humBand < cfg.biomeDesertHumCut + 0.14 && dryProvince > 0.53);
        if (dryHot) {
            biome = bad > 0.44 || dryProvince > 0.80 ? BIOME_BADLANDS : BIOME_DESERT;
        } else if (humBand >= cfg.biomeJungleHumCut - 0.10 || (humBand >= 0.66 && jungleProvince > 0.46)) {
            biome = BIOME_JUNGLE;
        } else if (humBand >= std::max(cfg.biomeForestHumCut - 0.07, 0.50) && c.selector > 0.46) {
            biome = forestStyle < 0.30 ? BIOME_BIRCH : BIOME_FOREST;
        }
    }

    ProvinceChoice province = balancedProvinceBiome(cfg, x, z);
    int provinceBiome = province.biome;
    double provinceStrength = smoothstep01((0.72 - mnt) / 0.30) * province.strength;
    if (provinceStrength > 0.16) {
        if (provinceBiome == BIOME_DESERT && tempBand > frozenCut + 0.020) {
            biome = BIOME_DESERT;
        } else if (provinceBiome == BIOME_BADLANDS && tempBand > warmCut - 0.020 && humBand < cfg.biomeDesertHumCut + 0.045 && provinceStrength > 0.66) {
            biome = BIOME_BADLANDS;
            bad = std::max(bad, 0.60);
        } else if (provinceBiome == BIOME_SWAMP && tempBand > frozenCut + 0.12 && humBand > 0.48 && mnt < 0.32) {
            biome = BIOME_SWAMP;
        } else if (provinceBiome == BIOME_JUNGLE && tempBand > frozenCut + 0.120 && humBand > 0.46 && provinceStrength > 0.28) {
            biome = BIOME_JUNGLE;
        } else if (provinceBiome == BIOME_TAIGA && tempBand < warmCut + 0.085) {
            biome = BIOME_TAIGA;
        } else if (provinceBiome == BIOME_BIRCH) {
            biome = BIOME_BIRCH;
        } else if (provinceBiome == BIOME_FOREST) {
            biome = BIOME_FOREST;
        } else if (provinceBiome == BIOME_PLAINS && tempBand > frozenCut + 0.035) {
            biome = BIOME_PLAINS;
        } else if (provinceBiome == BIOME_SNOW && humBand < 0.72) {
            if ((mnt > 0.76 && provinceStrength > 0.48) || tempBand < frozenCut + 0.010) {
                biome = BIOME_SNOW;
            } else if (tempBand < coldCut + 0.060) {
                biome = BIOME_TAIGA;
            }
        }
    }

    bool arid = biome == BIOME_DESERT || biome == BIOME_BADLANDS;
    bool high = mnt > 0.82;
    if (high && ((h >= cfg.snowlineY + 12) || ((h >= cfg.snowlineY - 2) && c.temp < coldCut - 0.08)) && !arid) {
        biome = BIOME_SNOW;
    }

    return {biome, tempBand, humBand, bad};
}

void cleanBiomeGrid(std::vector<int>& biome, int w, int h) {
    auto idx = [&](int z, int x) -> size_t {
        z = clampInt(z, 0, h - 1);
        x = clampInt(x, 0, w - 1);

        return static_cast<size_t>(z * w + x);
    };

    for (int pass = 0; pass < 2; ++pass) {
        std::vector<int> next = biome;

        for (int z = 0; z < h; ++z) {
            for (int x = 0; x < w; ++x) {
                std::array<int, 18> counts{};
                int total = 0;

                for (int dz = -2; dz <= 2; ++dz) {
                    for (int dx = -2; dx <= 2; ++dx) {
                        int b = biome[idx(z + dz, x + dx)];
                        if (b >= 0 && b < static_cast<int>(counts.size())) {
                            counts[static_cast<size_t>(b)] += (std::abs(dx) + std::abs(dz) <= 1) ? 2 : 1;
                            total += (std::abs(dx) + std::abs(dz) <= 1) ? 2 : 1;
                        }
                    }
                }
                int cur = biome[idx(z, x)];
                int bestBiome = cur;
                int bestCount = cur >= 0 && cur < static_cast<int>(counts.size()) ? counts[static_cast<size_t>(cur)] : 0;

                for (int b = 0; b < static_cast<int>(counts.size()); ++b) {
                    if (counts[static_cast<size_t>(b)] > bestCount) {
                        bestCount = counts[static_cast<size_t>(b)];
                        bestBiome = b;
                    }
                }
                int curCount = cur >= 0 && cur < static_cast<int>(counts.size()) ? counts[static_cast<size_t>(cur)] : 0;
                if (bestBiome != cur && bestCount >= total * 0.38 && curCount <= total * 0.28) {
                    next[idx(z, x)] = bestBiome;
                }
            }
        }
        biome.swap(next);
    }
}

void applyBiomeGridSafety(const Config& cfg, int x0, int z0, GridD& hf, const GridD& mnt,
                          GridD& tempBand, GridD& humBand, GridD& bad, std::vector<int>& biome, int w, int h) {
    if (cfg.biomeOverride >= 0) {
        return;
    }

    auto bio = [&](int z, int x) -> int& {
        return biome[static_cast<size_t>(z * w + x)];
    };

    GridD coldMask(w, h);
    GridD snowTempMask(w, h);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            coldMask.at(z, x) = tempBand.at(z, x) < (cfg.biomeColdCut - 0.025) + 0.080 ? 1.0 : 0.0;
            snowTempMask.at(z, x) = tempBand.at(z, x) < (cfg.biomeFrozenCut - 0.055) + 0.070 ? 1.0 : 0.0;
        }
    }
    GridD coldBuffer = softMaskGrid(coldMask, 7);
    GridD snowBuffer = softMaskGrid(snowTempMask, 8);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            int& b = bio(z, x);
            bool arid = b == BIOME_DESERT || b == BIOME_BADLANDS;
            bool aridEdge = arid && ((coldBuffer.at(z, x) > 0.018) || (snowBuffer.at(z, x) > 0.004) ||
                                     (tempBand.at(z, x) < (cfg.biomeWarmCut - 0.025) + 0.105));
            if (aridEdge) {
                b = humBand.at(z, x) >= cfg.biomeForestHumCut ? BIOME_FOREST : BIOME_PLAINS;
                bad.at(z, x) = 0.0;
            }
        }
    }

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            int& b = bio(z, x);
            bool arid = b == BIOME_DESERT || b == BIOME_BADLANDS;
            bool high = mnt.at(z, x) > 0.82;
            if (high && ((hf.at(z, x) >= cfg.snowlineY + 12) ||
                         ((hf.at(z, x) >= cfg.snowlineY - 2) && (tempBand.at(z, x) < (cfg.biomeColdCut - 0.025) - 0.08))) && !arid) {
                b = BIOME_SNOW;
            }
        }
    }

    GridD snowPaint(w, h);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            int b = bio(z, x);
            snowPaint.at(z, x) = (b == BIOME_SNOW) ? 1.0 : 0.0;
        }
    }
    GridD snowNear = softMaskGrid(snowPaint, 7);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            int& b = bio(z, x);
            bool arid = b == BIOME_DESERT || b == BIOME_BADLANDS;
            if (arid && snowNear.at(z, x) > 0.004) {
                b = humBand.at(z, x) >= cfg.biomeForestHumCut ? BIOME_FOREST : BIOME_PLAINS;
                bad.at(z, x) = 0.0;
            }
        }
    }

    (void)x0;
    (void)z0;
}

}  // namespace mcwf::terrain

int netherBiome(const Config& cfg, int x, int z) {
    double sel = fbm2d(static_cast<double>(x) + 7000.0, static_cast<double>(z) - 4000.0, cfg.seed + 501, 3, 1.0 / 210.0);
    double warm = fbm2d(static_cast<double>(x) - 3000.0, static_cast<double>(z) + 9000.0, cfg.seed + 502, 3, 1.0 / 260.0);

    if (sel > 0.55 && warm > 0.50) return BIOME_CRIMSON;
    if (sel > 0.55 && warm <= 0.50) return BIOME_WARPED;
    if (sel <= 0.35 && warm < 0.42) return BIOME_SOUL;
    if (sel <= 0.30 && warm >= 0.62) return BIOME_BASALT;

    return BIOME_NETHER;
}
