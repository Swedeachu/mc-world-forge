#include "mcwf/common.hpp"

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

namespace {

constexpr uint32_t U32 = 0xffffffffu;
constexpr double PI = 3.14159265358979323846264338327950288;

uint32_t hash2i(int64_t x, int64_t z, int seed, uint32_t salt = 0) {
    uint32_t n = (static_cast<uint32_t>(x) * 0x632be59bu) ^
                 (static_cast<uint32_t>(z) * 0x85157af5u) ^
                 (static_cast<uint32_t>(seed) * 0x9e3779b9u) ^
                 (salt * 0x165667b1u);
    return mix32(n) & U32;
}

double rand2(int64_t x, int64_t z, const Config& cfg, uint32_t salt = 0) {
    return static_cast<double>(hash2i(x, z, cfg.seed, salt)) / 4294967296.0;
}

double randRange(int64_t x, int64_t z, const Config& cfg, uint32_t salt, double lo, double hi) {
    return lo + (hi - lo) * rand2(x, z, cfg, salt);
}

double temperatureHeight(const Config& cfg, double h) {
    return clampDouble((h - static_cast<double>(cfg.waterLevel)) / 640.0, 0.0, 0.16);
}

using Grid9 = std::array<std::array<double, 9>, 9>;

Grid9 blur9(const Grid9& g) {
    Grid9 out{};
    auto at = [&](int z, int x) -> double {
        z = clampInt(z, 0, 8);
        x = clampInt(x, 0, 8);
        return g[static_cast<size_t>(z)][static_cast<size_t>(x)];
    };
    for (int z = 0; z < 9; ++z) {
        for (int x = 0; x < 9; ++x) {
            out[static_cast<size_t>(z)][static_cast<size_t>(x)] =
                (at(z, x) * 0.28 +
                 (at(z - 1, x) + at(z + 1, x) + at(z, x - 1) + at(z, x + 1)) * 0.13 +
                 (at(z - 1, x - 1) + at(z - 1, x + 1) + at(z + 1, x - 1) + at(z + 1, x + 1)) * 0.045 +
                 (at(z - 2, x) + at(z + 2, x) + at(z, x - 2) + at(z, x + 2)) * 0.025) / 1.08;
        }
    }
    return out;
}

Grid9 relax9(const Grid9& in, double maxStep) {
    Grid9 out{};
    auto at = [&](int z, int x) -> double {
        z = clampInt(z, 0, 8);
        x = clampInt(x, 0, 8);
        return in[static_cast<size_t>(z)][static_cast<size_t>(x)];
    };
    for (int z = 0; z < 9; ++z) {
        for (int x = 0; x < 9; ++x) {
            std::array<double, 8> n = {
                at(z - 1, x), at(z + 1, x), at(z, x - 1), at(z, x + 1),
                at(z - 1, x - 1), at(z - 1, x + 1), at(z + 1, x - 1), at(z + 1, x + 1)
            };
            std::sort(n.begin(), n.end());
            double med = (n[3] + n[4]) * 0.5;
            double avg = (at(z - 1, x) + at(z + 1, x) + at(z, x - 1) + at(z, x + 1)) * 0.25;
            double v = in[static_cast<size_t>(z)][static_cast<size_t>(x)];
            v = std::min(v, med + maxStep);
            v = std::max(v, med - maxStep);
            out[static_cast<size_t>(z)][static_cast<size_t>(x)] = v * 0.82 + avg * 0.18;
        }
    }
    return out;
}

struct RawTerrain {
    double h = 0.0;
    double mountain = 0.0;
};

RawTerrain rawTerrain(const Config& cfg, double x, double z) {
    double hills = (fbm2d(x, z, cfg.seed + 1, 4, 1.0 / 112.0) - 0.5) * 2.0;
    double detail = (fbm2d(x, z, cfg.seed + 11, 3, 1.0 / 74.0) - 0.5) * 2.0;

    double amount = clampDouble(cfg.mountainAmount, 0.0, 1.0);
    double extremeAmount = clampDouble(cfg.extremeMountainAmount, 0.0, 1.0);

    double mntMask = fbm2d(x, z, cfg.seed + 2, 4, 1.0 / 610.0);
    double province = fbm2d(x - 2900.0, z + 1700.0, cfg.seed + 16, 3, 1.0 / 760.0);
    double provinceGate = smoothstep01((province - (0.39 - amount * 0.050)) / 0.34);
    double lo = 0.530 - 0.115 * amount;
    double mnt = std::pow(smoothstep01((mntMask - lo) / std::max(0.001, 0.90 - lo)), 1.32);
    mnt *= 0.58 + provinceGate * 0.42;

    double ridgeRaw = 1.0 - std::abs(fbm2d(x, z, cfg.seed + 12, 3, 1.0 / 250.0) - 0.5) * 2.0;
    double ridge = std::pow(smoothstep01((ridgeRaw - 0.30) / 0.62), 1.18);

    double rangeNoise = fbm2d(x - 1700.0, z + 2600.0, cfg.seed + 14, 4, 1.0 / 980.0);
    double rangeGate = smoothstep01((rangeNoise - (0.625 - extremeAmount * 0.070)) / 0.30);
    double rangeRidge = 1.0 - std::abs(fbm2d(x + 6400.0, z - 3100.0, cfg.seed + 15, 4, 1.0 / 300.0) - 0.5) * 2.0;
    rangeRidge = std::pow(smoothstep01((rangeRidge - 0.25) / 0.66), 1.48);
    double extreme = rangeGate * rangeRidge;

    double peakNoise = fbm2d(x + 11200.0, z - 8800.0, cfg.seed + 18, 4, 1.0 / 540.0);
    double peakGate = smoothstep01((peakNoise - 0.655) / 0.245);
    double peakRidge = 1.0 - std::abs(fbm2d(x - 14400.0, z + 5300.0, cfg.seed + 19, 4, 1.0 / 190.0) - 0.5) * 2.0;
    peakRidge = std::pow(smoothstep01((peakRidge - 0.22) / 0.60), 1.34);
    double alpinePeak = peakGate * peakRidge * (0.45 + 0.55 * provinceGate);

    double mntRough = (fbm2d(x + 4100.0, z - 2700.0, cfg.seed + 21, 3, 1.0 / 92.0) - 0.5) * 2.0;
    double mntMedium = (fbm2d(x - 6200.0, z + 1500.0, cfg.seed + 22, 3, 1.0 / 150.0) - 0.5) * 2.0;
    double mntShape = smoothstep01(std::max({mnt, extreme * 0.78, alpinePeak * 0.88}));

    double h = cfg.baseHeight +
               hills * cfg.hillAmp * 0.44 +
               detail * 0.22 +
               mnt * cfg.mountainAmp * 0.62 +
               std::pow(mnt, 1.18) * ridge * cfg.mountainAmp * 0.26 +
               extreme * cfg.mountainAmp * 0.50 +
               alpinePeak * cfg.mountainAmp * 1.26 +
               mntShape * (mntMedium * cfg.mountainAmp * 0.055 +
                           mntRough * cfg.mountainAmp * 0.028);
    return {h, mntShape};
}

RawTerrain terrainHeight(const Config& cfg, double x, double z) {
    Grid9 h{};
    Grid9 m{};
    for (int dz = -4; dz <= 4; ++dz) {
        for (int dx = -4; dx <= 4; ++dx) {
            RawTerrain r = rawTerrain(cfg, x + dx, z + dz);
            h[static_cast<size_t>(dz + 4)][static_cast<size_t>(dx + 4)] = r.h;
            m[static_cast<size_t>(dz + 4)][static_cast<size_t>(dx + 4)] = r.mountain;
        }
    }
    Grid9 b1 = blur9(h);
    Grid9 b2 = blur9(b1);
    Grid9 mixed{};
    for (int zi = 0; zi < 9; ++zi) {
        for (int xi = 0; xi < 9; ++xi) {
            mixed[static_cast<size_t>(zi)][static_cast<size_t>(xi)] =
                h[static_cast<size_t>(zi)][static_cast<size_t>(xi)] * 0.68 +
                b1[static_cast<size_t>(zi)][static_cast<size_t>(xi)] * 0.24 +
                b2[static_cast<size_t>(zi)][static_cast<size_t>(xi)] * 0.08;
        }
    }
    Grid9 relaxed = relax9(mixed, 8.0);
    Grid9 mb = blur9(m);
    return {relaxed[4][4], mb[4][4]};
}

struct Climate {
    double temp = 0.5;
    double hum = 0.5;
    double selector = 0.5;
    double edge = 0.0;
};

Climate climateFields(const Config& cfg, double x, double z, double h) {
    double sizeScale = std::max(650.0, std::min(1450.0, cfg.size * 0.92));
    double warpScale = std::max(520.0, sizeScale * 0.78);

    double warpX = (fbm2d(x + 6111.0, z - 4222.0, cfg.seed + 33, 4, 1.0 / warpScale) - 0.5) * 330.0;
    double warpZ = (fbm2d(x - 1777.0, z + 9333.0, cfg.seed + 34, 4, 1.0 / warpScale) - 0.5) * 330.0;
    warpX += (fbm2d(x + 313.0, z + 991.0, cfg.seed + 35, 2, 1.0 / 185.0) - 0.5) * 72.0;
    warpZ += (fbm2d(x - 919.0, z - 707.0, cfg.seed + 36, 2, 1.0 / 185.0) - 0.5) * 72.0;
    double cx = x + warpX;
    double cz = z + warpZ;

    double phaseA = randRange(0, 0, cfg, 41001, 0.0, PI * 2.0);
    double phaseB = randRange(0, 0, cfg, 41002, 0.0, PI * 2.0);
    double angle = randRange(0, 0, cfg, 41003, 0.0, PI * 2.0);
    double ca = std::cos(angle);
    double sa = std::sin(angle);
    double rx = cx * ca - cz * sa;
    double rz = cx * sa + cz * ca;

    double waveTemp = std::sin(rx / sizeScale * PI * 2.15 + phaseA) * 0.28 +
                      std::sin((rx * 0.55 + rz * 0.78) / sizeScale * PI * 1.72 + phaseB) * 0.18;
    double tw = fbm2d(cx + 1000.0, cz - 1000.0, cfg.seed + 3, 5, 1.0 / (sizeScale * 0.92));
    double tempDetail = (fbm2d(cx + 333.0, cz - 777.0, cfg.seed + 31, 3, 1.0 / 260.0) - 0.5) * 0.085;
    double edgeWobble = fbm2d(x + 7077.0, z - 1212.0, cfg.seed + 74, 3, 1.0 / 135.0) - 0.5;
    double temp = 0.5 + waveTemp + (tw - 0.5) * 0.70 + tempDetail + edgeWobble * 0.025;
    temp -= temperatureHeight(cfg, h);

    double mw = fbm2d(cx - 2000.0, cz + 2000.0, cfg.seed + 4, 5, 1.0 / (sizeScale * 0.82));
    double humWave = std::sin((rz * 0.95 - rx * 0.28) / sizeScale * PI * 2.05 + phaseB * 0.7) * 0.16;
    double humDetail = (fbm2d(cx - 444.0, cz + 666.0, cfg.seed + 41, 3, 1.0 / 245.0) - 0.5) * 0.105;
    double hum = 0.5 + (mw - 0.5) * 0.96 + humWave + humDetail - edgeWobble * 0.026;
    double selector = fbm2d(cx + 8811.0, cz - 4417.0, cfg.seed + 73, 3, 1.0 / 255.0);
    return {clampDouble(temp, 0.0, 1.0), clampDouble(hum, 0.0, 1.0), selector, edgeWobble};
}

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

struct BiomeChoice {
    int biome = BIOME_PLAINS;
    double tempBand = 0.5;
    double humBand = 0.5;
    double badlands = 0.0;
};

BiomeChoice classifyBiome(const Config& cfg, double x, double z, double h, double mnt, const Climate& c) {
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

struct RiverNodes {
    std::vector<std::pair<double, double>> nodes;
};

RiverNodes buildRiverNodes(const Config& cfg, int cellX, int cellZ, double spacing) {
    double margin = std::max(18.0, spacing * 0.12);
    double ox = cellX * spacing + randRange(cellX, cellZ, cfg, 5102, margin, spacing - margin);
    double oz = cellZ * spacing + randRange(cellX, cellZ, cfg, 5103, margin, spacing - margin);
    double angle = randRange(cellX, cellZ, cfg, 5104, 0.0, PI * 2.0);
    double bias = randRange(cellX, cellZ, cfg, 5105, -0.105, 0.105);
    int segs = 2 + static_cast<int>(rand2(cellX, cellZ, cfg, 5106) * 3.0);
    RiverNodes out;
    out.nodes.push_back({ox, oz});
    double x = ox;
    double z = oz;
    for (int i = 1; i <= segs; ++i) {
        double turn = randRange(cellX, cellZ, cfg, 5110 + i, -0.52, 0.52);
        turn += std::sin(i * 1.81 + randRange(cellX, cellZ, cfg, 5111, 0.0, PI * 2.0)) * 0.12;
        angle += turn + bias;
        double step = randRange(cellX, cellZ, cfg, 5120 + i, 16.0, 34.0);
        if (i == segs) {
            step *= randRange(cellX, cellZ, cfg, 5125 + i, 0.55, 0.88);
        }
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        out.nodes.push_back({x, z});
    }
    return out;
}

double segmentDistance(double x, double z, double ax, double az, double bx, double bz) {
    double vx = bx - ax;
    double vz = bz - az;
    double length2 = vx * vx + vz * vz;
    if (length2 <= 0.001) {
        double dx = x - ax;
        double dz = z - az;
        return std::sqrt(dx * dx + dz * dz);
    }
    double t = ((x - ax) * vx + (z - az) * vz) / length2;
    t = clampDouble(t, 0.0, 1.0);
    double px = ax + vx * t;
    double pz = az + vz * t;
    double dx = x - px;
    double dz = z - pz;
    return std::sqrt(dx * dx + dz * dz);
}

double fastSurfaceHeight(const Config& cfg, double x, double z) {
    // Python caches _surface_height_at heavily because river/lake/cave feature
    // cells ask for the same surface probes from many neighboring columns. The
    // native path needs the same cache, otherwise the local water-body planner
    // spends most of its time recomputing 9x9 smoothed terrain probes.
    struct CacheEntry {
        int seed = 0;
        int floorY = 0;
        int baseHeight16 = 0;
        int hillAmp16 = 0;
        int mountainAmp16 = 0;
        int mountainAmount4096 = 0;
        int x16 = 0;
        int z16 = 0;
        bool operator==(const CacheEntry& o) const {
            return seed == o.seed && floorY == o.floorY && baseHeight16 == o.baseHeight16 &&
                   hillAmp16 == o.hillAmp16 && mountainAmp16 == o.mountainAmp16 &&
                   mountainAmount4096 == o.mountainAmount4096 && x16 == o.x16 && z16 == o.z16;
        }
    };
    struct Hash {
        size_t operator()(const CacheEntry& e) const {
            uint32_t h = mix32(static_cast<uint32_t>(e.seed));
            h ^= mix32(static_cast<uint32_t>(e.floorY) + 0x9e3779b9u);
            h ^= mix32(static_cast<uint32_t>(e.baseHeight16) + 0x85ebca6bu);
            h ^= mix32(static_cast<uint32_t>(e.hillAmp16) + 0xc2b2ae35u);
            h ^= mix32(static_cast<uint32_t>(e.mountainAmp16) + 0x27d4eb2du);
            h ^= mix32(static_cast<uint32_t>(e.mountainAmount4096) + 0x165667b1u);
            h ^= mix32(static_cast<uint32_t>(e.x16) + 0xd3a2646cu);
            h ^= mix32(static_cast<uint32_t>(e.z16) + 0xfd7046c5u);
            return static_cast<size_t>(h);
        }
    };
    thread_local std::unordered_map<CacheEntry, int, Hash> cache;
    if (cache.size() > 200000) {
        cache.clear();
    }
    CacheEntry key;
    key.seed = cfg.seed;
    key.floorY = cfg.floorY;
    key.baseHeight16 = static_cast<int>(std::round(cfg.baseHeight * 16.0));
    key.hillAmp16 = static_cast<int>(std::round(cfg.hillAmp * 16.0));
    key.mountainAmp16 = static_cast<int>(std::round(cfg.mountainAmp * 16.0));
    key.mountainAmount4096 = static_cast<int>(std::round(cfg.mountainAmount * 4096.0));
    key.x16 = static_cast<int>(std::round(x * 16.0));
    key.z16 = static_cast<int>(std::round(z * 16.0));
    auto it = cache.find(key);
    if (it != cache.end()) {
        return static_cast<double>(it->second);
    }
    RawTerrain t = terrainHeight(cfg, x, z);
    int value = static_cast<int>(std::round(clampDouble(t.h, cfg.floorY + 3, MIN_Y + WORLD_HEIGHT - 40)));
    cache[key] = value;
    return static_cast<double>(value);
}

struct FeatureField {
    double core = 0.0;
    double bank = 0.0;
    int level = 0;
};

FeatureField finiteRiverField(const Config& cfg, double x, double z, double mountain, double temp, double hum) {
    if (!cfg.rivers || cfg.riverAmount <= 0.0) {
        return {0.0, 0.0, cfg.waterLevel};
    }
    double amount = clampDouble(cfg.riverAmount, 0.0, 1.0);
    int spacing = static_cast<int>(std::round(245.0 - amount * 55.0));
    int reach = 118;
    int cx0 = static_cast<int>(std::floor((x - reach) / spacing)) - 1;
    int cx1 = static_cast<int>(std::floor((x + reach) / spacing)) + 1;
    int cz0 = static_cast<int>(std::floor((z - reach) / spacing)) - 1;
    int cz1 = static_cast<int>(std::floor((z + reach) / spacing)) + 1;
    double core = 0.0;
    double valley = 0.0;
    int level = cfg.waterLevel;
    double levelStrength = 0.0;

    for (int cellZ = cz0; cellZ <= cz1; ++cellZ) {
        for (int cellX = cx0; cellX <= cx1; ++cellX) {
            double active = 0.28 + amount * 0.28;
            if (rand2(cellX, cellZ, cfg, 5160) > active) {
                continue;
            }
            RiverNodes nodes = buildRiverNodes(cfg, cellX, cellZ, static_cast<double>(spacing));
            double sy = fastSurfaceHeight(cfg, nodes.nodes.front().first, nodes.nodes.front().second);
            int bodyLevel = static_cast<int>(std::round(sy - randRange(cellX, cellZ, cfg, 5161, 0.15, 0.85)));
            bodyLevel = clampInt(bodyLevel, cfg.floorY + 8, MIN_Y + WORLD_HEIGHT - 42);
            double widthBase = randRange(cellX, cellZ, cfg, 5162, 4.8, 7.4) * (0.88 + amount * 0.24);
            for (size_t i = 0; i + 1 < nodes.nodes.size(); ++i) {
                double ax = nodes.nodes[i].first;
                double az = nodes.nodes[i].second;
                double bx = nodes.nodes[i + 1].first;
                double bz = nodes.nodes[i + 1].second;
                double d = segmentDistance(x, z, ax, az, bx, bz);
                double width = widthBase * randRange(cellX, cellZ, cfg, static_cast<uint32_t>(5170 + i), 0.82, 1.22);
                double bank = width + randRange(cellX, cellZ, cfg, static_cast<uint32_t>(5180 + i), 13.0, 22.0);
                double c = smoothstep01((width - d) / std::max(width, 0.001));
                double v = smoothstep01((bank - d) / std::max(bank, 0.001));
                double wobble = 0.82 + 0.36 * fbm2d(x + cellX * 31.0 + i * 17.0, z - cellZ * 29.0 - i * 19.0, cfg.seed + 5190, 2, 1.0 / 38.0);
                c = clampDouble(c * wobble, 0.0, 1.0);
                v = clampDouble(v * (0.88 + 0.20 * wobble), 0.0, 1.0);
                if (c > levelStrength) {
                    level = bodyLevel;
                    levelStrength = c;
                }
                core = std::max(core, c);
                valley = std::max(valley, v);
            }
        }
    }
    if (temp < cfg.biomeFrozenCut + 0.02) {
        core *= 0.40;
        valley *= 0.70;
    }
    if (hum < 0.18) {
        core *= 0.70;
        valley *= 0.80;
    }
    if (mountain > 0.64) {
        core *= 0.24;
        valley *= 0.46;
    }
    return {clampDouble(core, 0.0, 1.0), clampDouble(valley, 0.0, 1.0), level};
}

FeatureField finiteLakeField(const Config& cfg, double x, double z, double mountain, double temp, double hum) {
    if (!cfg.lakes || cfg.lakeAmount <= 0.0) {
        return {0.0, 0.0, cfg.waterLevel};
    }
    double amount = clampDouble(cfg.lakeAmount, 0.0, 1.0);
    int spacing = static_cast<int>(std::round(235.0 - amount * 48.0));
    int reach = 72;
    int cx0 = static_cast<int>(std::floor((x - reach) / spacing)) - 1;
    int cx1 = static_cast<int>(std::floor((x + reach) / spacing)) + 1;
    int cz0 = static_cast<int>(std::floor((z - reach) / spacing)) - 1;
    int cz1 = static_cast<int>(std::floor((z + reach) / spacing)) + 1;
    double core = 0.0;
    double bank = 0.0;
    int level = cfg.waterLevel;
    double levelStrength = 0.0;
    for (int cellZ = cz0; cellZ <= cz1; ++cellZ) {
        for (int cellX = cx0; cellX <= cx1; ++cellX) {
            double active = 0.08 + amount * 0.18;
            if (rand2(cellX, cellZ, cfg, 5310) > active) {
                continue;
            }
            double ox = cellX * spacing + randRange(cellX, cellZ, cfg, 5311, 24.0, spacing - 24.0);
            double oz = cellZ * spacing + randRange(cellX, cellZ, cfg, 5312, 24.0, spacing - 24.0);
            double sy = fastSurfaceHeight(cfg, ox, oz);
            int bodyLevel = static_cast<int>(std::round(sy - randRange(cellX, cellZ, cfg, 5313, 0.6, 1.2)));
            bodyLevel = clampInt(bodyLevel, cfg.floorY + 8, MIN_Y + WORLD_HEIGHT - 42);
            double rx = randRange(cellX, cellZ, cfg, 5314, 9.0, 22.0) * (0.85 + amount * 0.42);
            double rz = randRange(cellX, cellZ, cfg, 5315, 9.0, 22.0) * (0.85 + amount * 0.42);
            double ang = randRange(cellX, cellZ, cfg, 5316, 0.0, PI * 2.0);
            double ca = std::cos(ang);
            double sa = std::sin(ang);
            double dx = x - ox;
            double dz = z - oz;
            double lx = dx * ca + dz * sa;
            double lz = -dx * sa + dz * ca;
            double d = std::sqrt((lx / std::max(1.0, rx)) * (lx / std::max(1.0, rx)) +
                                 (lz / std::max(1.0, rz)) * (lz / std::max(1.0, rz)));
            double c = smoothstep01((1.0 - d) / 0.22);
            double b = smoothstep01((1.80 - d) / 0.72);
            if (c > levelStrength) {
                level = bodyLevel;
                levelStrength = c;
            }
            core = std::max(core, c);
            bank = std::max(bank, b);
        }
    }
    if (temp < cfg.biomeFrozenCut + 0.02) {
        core *= 0.34;
        bank *= 0.70;
    }
    if (hum < 0.18) {
        core *= 0.80;
        bank *= 0.86;
    }
    if (mountain > 0.46) {
        core *= 0.16;
        bank *= 0.38;
    }
    return {clampDouble(core, 0.0, 1.0), clampDouble(bank, 0.0, 1.0), level};
}

FeatureField lavaPoolField(const Config& cfg, double x, double z, double mountain, double temp, double hum) {
    if (!cfg.surfaceLavaPools || !cfg.fluids) {
        return {0.0, 0.0, cfg.waterLevel};
    }
    int spacing = 285;
    int reach = 44;
    int cx0 = static_cast<int>(std::floor((x - reach) / spacing)) - 1;
    int cx1 = static_cast<int>(std::floor((x + reach) / spacing)) + 1;
    int cz0 = static_cast<int>(std::floor((z - reach) / spacing)) - 1;
    int cz1 = static_cast<int>(std::floor((z + reach) / spacing)) + 1;
    double core = 0.0;
    double bank = 0.0;
    int level = cfg.waterLevel;
    double levelStrength = 0.0;
    for (int cellZ = cz0; cellZ <= cz1; ++cellZ) {
        for (int cellX = cx0; cellX <= cx1; ++cellX) {
            if (rand2(cellX, cellZ, cfg, 5440) > 0.155) {
                continue;
            }
            double ox = cellX * spacing + randRange(cellX, cellZ, cfg, 5441, 28.0, spacing - 28.0);
            double oz = cellZ * spacing + randRange(cellX, cellZ, cfg, 5442, 28.0, spacing - 28.0);
            double sy = fastSurfaceHeight(cfg, ox, oz);
            int bodyLevel = static_cast<int>(std::round(sy - randRange(cellX, cellZ, cfg, 5443, 0.20, 0.70)));
            bodyLevel = clampInt(bodyLevel, cfg.floorY + 10, MIN_Y + WORLD_HEIGHT - 50);
            double rx = randRange(cellX, cellZ, cfg, 5444, 3.5, 6.5);
            double rz = randRange(cellX, cellZ, cfg, 5445, 3.5, 6.5);
            double ang = randRange(cellX, cellZ, cfg, 5446, 0.0, PI * 2.0);
            double ca = std::cos(ang);
            double sa = std::sin(ang);
            double dx = x - ox;
            double dz = z - oz;
            double lx = dx * ca + dz * sa;
            double lz = -dx * sa + dz * ca;
            double d = std::sqrt((lx / std::max(1.0, rx)) * (lx / std::max(1.0, rx)) +
                                 (lz / std::max(1.0, rz)) * (lz / std::max(1.0, rz)));
            double c = smoothstep01((1.0 - d) / 0.24);
            double b = smoothstep01((1.55 - d) / 0.55);
            if (c > levelStrength) {
                level = bodyLevel;
                levelStrength = c;
            }
            core = std::max(core, c);
            bank = std::max(bank, b);
        }
    }
    if (temp >= cfg.biomeWarmCut - 0.04) {
        core *= 1.0;
        bank *= 1.0;
    } else {
        core *= 0.52;
        bank *= 0.62;
    }
    if (hum > 0.72) {
        core *= 0.50;
        bank *= 0.56;
    }
    if (mountain > 0.62) {
        core *= 0.70;
        bank *= 0.82;
    }
    return {clampDouble(core, 0.0, 1.0), clampDouble(bank, 0.0, 1.0), level};
}


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

struct GridD {
    int w = 0;
    int h = 0;
    std::vector<double> v;

    GridD() = default;
    GridD(int width, int height, double fill = 0.0) : w(width), h(height), v(static_cast<size_t>(width * height), fill) {}

    double& at(int z, int x) {
        return v[static_cast<size_t>(z * w + x)];
    }

    double at(int z, int x) const {
        return v[static_cast<size_t>(z * w + x)];
    }

    double clamped(int z, int x) const {
        z = clampInt(z, 0, h - 1);
        x = clampInt(x, 0, w - 1);
        return at(z, x);
    }
};

GridD blurGrid(const GridD& g) {
    GridD out(g.w, g.h);
    for (int z = 0; z < g.h; ++z) {
        for (int x = 0; x < g.w; ++x) {
            out.at(z, x) =
                (g.clamped(z, x) * 0.28 +
                 (g.clamped(z - 1, x) + g.clamped(z + 1, x) + g.clamped(z, x - 1) + g.clamped(z, x + 1)) * 0.13 +
                 (g.clamped(z - 1, x - 1) + g.clamped(z - 1, x + 1) + g.clamped(z + 1, x - 1) + g.clamped(z + 1, x + 1)) * 0.045 +
                 (g.clamped(z - 2, x) + g.clamped(z + 2, x) + g.clamped(z, x - 2) + g.clamped(z, x + 2)) * 0.025) / 1.08;
        }
    }
    return out;
}

GridD relaxGrid(const GridD& in, double maxStep, int passes) {
    GridD out = in;
    for (int pass = 0; pass < std::max(1, passes); ++pass) {
        GridD next(out.w, out.h);
        for (int z = 0; z < out.h; ++z) {
            for (int x = 0; x < out.w; ++x) {
                std::array<double, 8> n = {
                    out.clamped(z - 1, x), out.clamped(z + 1, x), out.clamped(z, x - 1), out.clamped(z, x + 1),
                    out.clamped(z - 1, x - 1), out.clamped(z - 1, x + 1), out.clamped(z + 1, x - 1), out.clamped(z + 1, x + 1)
                };
                std::sort(n.begin(), n.end());
                double med = (n[3] + n[4]) * 0.5;
                double avg = (out.clamped(z - 1, x) + out.clamped(z + 1, x) + out.clamped(z, x - 1) + out.clamped(z, x + 1)) * 0.25;
                double v = out.at(z, x);
                v = std::min(v, med + maxStep);
                v = std::max(v, med - maxStep);
                next.at(z, x) = v * 0.82 + avg * 0.18;
            }
        }
        out = std::move(next);
    }
    return out;
}

GridD softMaskGrid(const GridD& mask, int passes) {
    GridD out = mask;
    for (int i = 0; i < std::max(1, passes); ++i) {
        out = blurGrid(out);
    }
    for (double& v : out.v) {
        v = clampDouble(v, 0.0, 1.0);
    }
    return out;
}

GridD smoothFinalHeightGrid(const GridD& h) {
    if (h.w < 3 || h.h < 3) {
        return h;
    }
    GridD b1 = blurGrid(h);
    GridD b2 = blurGrid(b1);
    GridD smoothed(h.w, h.h);
    for (int z = 0; z < h.h; ++z) {
        for (int x = 0; x < h.w; ++x) {
            smoothed.at(z, x) = h.at(z, x) * 0.78 + b1.at(z, x) * 0.16 + b2.at(z, x) * 0.06;
        }
    }
    return relaxGrid(smoothed, 8.0, 1);
}

GridD limitMountainSlopeGrid(const GridD& h, const GridD& mountain, int passes) {
    GridD out = h;
    for (int pass = 0; pass < std::max(1, passes); ++pass) {
        GridD next = out;
        for (int z = 0; z < out.h; ++z) {
            for (int x = 0; x < out.w; ++x) {
                std::array<double, 8> n = {
                    out.clamped(z - 1, x), out.clamped(z + 1, x), out.clamped(z, x - 1), out.clamped(z, x + 1),
                    out.clamped(z - 1, x - 1), out.clamped(z - 1, x + 1), out.clamped(z + 1, x - 1), out.clamped(z + 1, x + 1)
                };
                std::sort(n.begin(), n.end());
                double med = (n[3] + n[4]) * 0.5;
                double avg = (out.clamped(z - 1, x) + out.clamped(z + 1, x) + out.clamped(z, x - 1) + out.clamped(z, x + 1)) * 0.25;
                double m = smoothstep01((mountain.clamped(z, x) - 0.18) / 0.46);
                double maxStep = 7.75 - m * 3.05;
                double v = out.at(z, x);
                v = std::min(v, med + maxStep);
                v = std::max(v, med - maxStep * 1.18);
                double limited = v * (0.86 - 0.10 * m) + avg * (0.14 + 0.10 * m);
                next.at(z, x) = out.at(z, x) * (1.0 - m * 0.72) + limited * (m * 0.72);
            }
        }
        out = std::move(next);
    }
    return out;
}

void terrainHeightGrid(const Config& cfg, int x0, int z0, int w, int h, GridD& outH, GridD& outM) {
    constexpr int margin = 4;
    GridD rawH(w + margin * 2, h + margin * 2);
    GridD rawM(w + margin * 2, h + margin * 2);
    for (int gz = 0; gz < rawH.h; ++gz) {
        for (int gx = 0; gx < rawH.w; ++gx) {
            RawTerrain r = rawTerrain(cfg, static_cast<double>(x0 + gx - margin), static_cast<double>(z0 + gz - margin));
            rawH.at(gz, gx) = r.h;
            rawM.at(gz, gx) = r.mountain;
        }
    }

    GridD b1 = blurGrid(rawH);
    GridD b2 = blurGrid(b1);
    GridD mixed(rawH.w, rawH.h);
    for (int z = 0; z < rawH.h; ++z) {
        for (int x = 0; x < rawH.w; ++x) {
            mixed.at(z, x) = rawH.at(z, x) * 0.68 + b1.at(z, x) * 0.24 + b2.at(z, x) * 0.08;
        }
    }
    GridD relaxed = relaxGrid(mixed, 8.0, 1);
    GridD mountain = blurGrid(rawM);

    outH = GridD(w, h);
    outM = GridD(w, h);
    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            outH.at(z, x) = relaxed.at(z + margin, x + margin);
            outM.at(z, x) = mountain.at(z + margin, x + margin);
        }
    }
}

void smoothClimateGrid(GridD& temp, GridD& hum, GridD& selector, GridD& edge) {
    if (temp.w < 3 || temp.h < 3) {
        return;
    }
    for (int i = 0; i < 4; ++i) {
        GridD tb = blurGrid(temp);
        GridD hb = blurGrid(hum);
        GridD sb = blurGrid(selector);
        GridD eb = blurGrid(edge);
        for (int z = 0; z < temp.h; ++z) {
            for (int x = 0; x < temp.w; ++x) {
                temp.at(z, x) = clampDouble(temp.at(z, x) * 0.55 + tb.at(z, x) * 0.45, 0.0, 1.0);
                hum.at(z, x) = clampDouble(hum.at(z, x) * 0.62 + hb.at(z, x) * 0.38, 0.0, 1.0);
                selector.at(z, x) = selector.at(z, x) * 0.70 + sb.at(z, x) * 0.30;
                edge.at(z, x) = edge.at(z, x) * 0.45 + eb.at(z, x) * 0.55;
            }
        }
    }
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


} // namespace

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

int netherBiome(const Config& cfg, int x, int z) {
    double n = fbm2d(static_cast<double>(x), static_cast<double>(z), cfg.seed + 9100, 3, 1.0 / 180.0);
    if (n < 0.22) return BIOME_SOUL;
    if (n > 0.82) return BIOME_BASALT;
    if (n > 0.65) return BIOME_WARPED;
    if (n > 0.48) return BIOME_CRIMSON;
    return BIOME_NETHER;
}
