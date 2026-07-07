#include "internal/chunk_passes.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>

#include "mcwf/chunk.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"

namespace mcwf::chunk {

namespace {

bool isOreReplaceableFast(int id, const BlockIds& ids, int andesite, int diorite, int granite,
                          int terracotta, int orangeTerracotta, int whiteTerracotta, int redSand) {
    (void)terracotta;
    (void)orangeTerracotta;
    (void)whiteTerracotta;
    (void)redSand;

    return id == ids.stone || id == ids.deepslate || id == ids.tuff || id == ids.cobbledDeepslate ||
           id == andesite || id == diorite || id == granite;
}

int randomIntRange(int cx, int cz, int vein, const Config& cfg, uint32_t salt, int lo, int hi) {
    if (hi <= lo) {
        return lo;
    }
    double r = hash01(cx, cz, vein, cfg.seed, salt);

    return lo + clampInt(static_cast<int>(std::floor(r * static_cast<double>(hi - lo + 1))), 0, hi - lo);
}

double randomUnit(int cx, int cz, int vein, const Config& cfg, uint32_t salt) {
    return hash01(cx, cz, vein, cfg.seed, salt);
}

double oreDensityMultiplier(const OreCfg& ore, int y, int lo, int hi) {
    (void)ore;
    (void)y;
    (void)lo;
    (void)hi;
    // Keep ore density vertically consistent. The biome/deep-dark request is to
    // stop boosting ore amount just because a block is deeper underground.
    return 1.0;
}

int oreVeinSizeAtY(const OreCfg& ore, int y, int lo, int hi) {
    (void)y;
    (void)lo;
    (void)hi;

    return clampInt(std::max(1, ore.veinSize), 2, 12);
}

int sampleOreY(const OreCfg& ore, int lo, int hi, int cx, int cz, int vein, const Config& cfg) {
    double r = randomUnit(cx, cz, vein, cfg, 12500);
    double t = r;
    if (ore.bias == "low") {
        t = r * r;
    } else if (ore.bias == "high") {
        t = 1.0 - (1.0 - r) * (1.0 - r);
    } else if (ore.bias == "mid" || ore.bias == "triangle") {
        double a = randomUnit(cx, cz, vein, cfg, 12501);
        double b = randomUnit(cx, cz, vein, cfg, 12502);
        t = (a + b) * 0.5;
    }

    return lo + clampInt(static_cast<int>(std::floor(t * static_cast<double>(hi - lo + 1))), 0, hi - lo);
}

void placeOreBlob(std::vector<uint16_t>& a, int ox, int oy, int oz, int size, int oreId,
                  const BlockIds& ids, int andesite, int diorite, int granite,
                  int terracotta, int orangeTerracotta, int whiteTerracotta, int redSand,
                  int cx, int cz, int vein, const Config& cfg) {
    size = clampInt(size, 2, 12);
    int placed = 0;
    int lx = ox;
    int y = oy;
    int lz = oz;

    auto tryPlace = [&](int px, int py, int pz) {
        if (px < 0 || px >= 16 || pz < 0 || pz >= 16 || py < MIN_Y || py >= MIN_Y + WORLD_HEIGHT) {
            return;
        }
        int idx = blockIndex(yIndex(py), pz, px);
        int current = a[static_cast<size_t>(idx)];
        if (!isOreReplaceableFast(current, ids, andesite, diorite, granite, terracotta, orangeTerracotta, whiteTerracotta, redSand)) {
            return;
        }
        a[static_cast<size_t>(idx)] = static_cast<uint16_t>(oreId);
        ++placed;
    };

    // A short deterministic random walk gives ores actual connected veins instead
    // of the old scatter of one-to-three isolated blocks. The branch attempts
    // thicken large veins while the cap keeps diamonds from becoming huge blobs.
    for (int i = 0; i < size * 5 && placed < size; ++i) {
        tryPlace(lx, y, lz);
        if (size >= 5 && randomUnit(cx, cz, vein * 37 + i, cfg, 12620) < 0.55) {
            tryPlace(lx + randomIntRange(cx, cz, vein * 37 + i, cfg, 12621, -1, 1),
                     y + randomIntRange(cx, cz, vein * 37 + i, cfg, 12622, -1, 1),
                     lz + randomIntRange(cx, cz, vein * 37 + i, cfg, 12623, -1, 1));
        }
        lx = clampInt(lx + randomIntRange(cx, cz, vein * 41 + i, cfg, 12600, -1, 1), 0, 15);
        y = clampInt(y + randomIntRange(cx, cz, vein * 41 + i, cfg, 12601, -1, 1), MIN_Y + 1, MIN_Y + WORLD_HEIGHT - 2);
        lz = clampInt(lz + randomIntRange(cx, cz, vein * 41 + i, cfg, 12602, -1, 1), 0, 15);
    }
}

}  // namespace

void placeChunkOres(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                    int cx, int cz, const bool inside[16][16], const int heights[16][16]) {
    int andesite = blocks.id("andesite");
    int diorite = blocks.id("diorite");
    int granite = blocks.id("granite");

    for (size_t oreIndex = 0; oreIndex < cfg.ores.size(); ++oreIndex) {
        const OreCfg& ore = cfg.ores[oreIndex];
        if (ore.veinsPerChunk <= 0.0 || ore.veinSize <= 0) {
            continue;
        }
        double targetVeins = ore.veinsPerChunk * 3.54;
        if (ore.yMax <= 16 || ore.bias == "low") {
            targetVeins *= 1.12;
        }
        int veins = static_cast<int>(std::floor(targetVeins));
        double frac = targetVeins - static_cast<double>(veins);
        if (randomUnit(cx, cz, static_cast<int>(oreIndex), cfg, 12700) < frac) {
            ++veins;
        }

        for (int vein = 0; vein < veins; ++vein) {
            int ox = randomIntRange(cx, cz, vein + static_cast<int>(oreIndex) * 1000, cfg, 12710, 0, 15);
            int oz = randomIntRange(cx, cz, vein + static_cast<int>(oreIndex) * 1000, cfg, 12711, 0, 15);
            if (!inside[oz][ox]) {
                continue;
            }
            int regularFloor = MIN_Y;
            int lo = std::max(regularFloor + 1, ore.yMin);
            int hi = std::min(ore.yMax, heights[oz][ox] - 6);
            if (hi < lo) {
                continue;
            }
            int oy = sampleOreY(ore, lo, hi, cx, cz, vein + static_cast<int>(oreIndex) * 1000, cfg);
            if (cfg.deepDark && oy < cfg.floorY &&
                randomUnit(cx, cz, vein + static_cast<int>(oreIndex) * 1000, cfg, 12735) > 0.50) {
                continue;
            }
            double density = oreDensityMultiplier(ore, oy, lo, hi);
            if (density > 1.0) {
                double keep = clampDouble(density - 1.0, 0.0, 0.95);
                if (randomUnit(cx, cz, vein + static_cast<int>(oreIndex) * 1000, cfg, 12740) > keep && vein >= static_cast<int>(std::ceil(ore.veinsPerChunk * 3.0))) {
                    continue;
                }
            }
            int oid = oy < 0 ? blocks.id(deepDarkOreName(ore.name)) : blocks.id(ore.name);
            int veinSize = oreVeinSizeAtY(ore, oy, lo, hi);
            placeOreBlob(a, ox, oy, oz, veinSize, oid, ids, andesite, diorite, granite,
                         ids.terracotta, ids.orangeTerracotta, ids.whiteTerracotta, ids.redSand,
                         cx, cz, vein + static_cast<int>(oreIndex) * 1000, cfg);
        }
    }
}

}  // namespace mcwf::chunk
