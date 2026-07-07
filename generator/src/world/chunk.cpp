#include "mcwf/common.hpp"

int blockIndex(int yIndex, int z, int x) {
    return (yIndex * 16 + z) * 16 + x;
}

int yIndex(int y) {
    return y - MIN_Y;
}

bool isStoneLike(int id, const BlockIds& ids, const Blocks& blocks) {
    if (id == ids.stone || id == ids.deepslate || id == blocks.id("andesite") || id == blocks.id("diorite") || id == blocks.id("granite")) {
        return true;
    }
    const std::string& name = blocks.names[static_cast<size_t>(id)];
    return name.find("ore") != std::string::npos;
}

int oreBlockId(const OreCfg& ore, int y, const Blocks& blocks, const BlockIds& ids) {
    (void)ids;
    if (y < 0) {
        std::string deepslate = deepDarkOreName(ore.name);
        int id = blocks.id(deepslate);
        if (blocks.names[static_cast<size_t>(id)] == mcName(deepslate)) {
            return id;
        }
    }
    return blocks.id(ore.name);
}

void maybePlaceOre(std::vector<uint16_t>& blocksOut, int idx, int x, int y, int z,
                          const Config& cfg, const Blocks& blocks, const BlockIds& ids) {
    int current = blocksOut[static_cast<size_t>(idx)];
    if (!isStoneLike(current, ids, blocks) || current == ids.bedrock) {
        return;
    }
    for (size_t i = 0; i < cfg.ores.size(); ++i) {
        const OreCfg& ore = cfg.ores[i];
        if (y < ore.yMin || y > ore.yMax || ore.veinsPerChunk <= 0.0) {
            continue;
        }
        int range = std::max(1, ore.yMax - ore.yMin + 1);
        double base = (ore.veinsPerChunk * std::max(1, ore.veinSize)) / (256.0 * static_cast<double>(range));
        double bias = 1.0;
        if (ore.bias == "triangle") {
            double mid = (ore.yMin + ore.yMax) * 0.5;
            double half = std::max(1.0, (ore.yMax - ore.yMin) * 0.5);
            bias = clampDouble(1.0 - std::abs(static_cast<double>(y) - mid) / half, 0.0, 1.0) * 1.8;
        }
        double p = clampDouble(base * bias * 0.45, 0.0, 0.08);
        if (rand01(x, y, z, cfg, static_cast<uint32_t>(12000 + i * 131)) < p) {
            blocksOut[static_cast<size_t>(idx)] = static_cast<uint16_t>(oreBlockId(ore, y, blocks, ids));
            return;
        }
    }
}


bool isDeepDarkOreReplaceable(int id, const BlockIds& ids, const Blocks& blocks) {
    return id == ids.deepslate || id == ids.tuff || id == ids.cobbledDeepslate ||
           id == ids.stone || id == blocks.id("andesite") || id == blocks.id("diorite") ||
           id == blocks.id("granite");
}

bool isDeepDarkOreExposed(const Config& cfg, int x, int y, int z, int fakeSurface) {
    return caveCarves(cfg, x + 1, y, z, fakeSurface, MIN_Y + 1) ||
           caveCarves(cfg, x - 1, y, z, fakeSurface, MIN_Y + 1) ||
           caveCarves(cfg, x, y + 1, z, fakeSurface, MIN_Y + 1) ||
           caveCarves(cfg, x, y - 1, z, fakeSurface, MIN_Y + 1) ||
           caveCarves(cfg, x, y, z + 1, fakeSurface, MIN_Y + 1) ||
           caveCarves(cfg, x, y, z - 1, fakeSurface, MIN_Y + 1);
}

void maybePlaceDeepDarkOre(std::vector<uint16_t>& blocksOut, int idx, int x, int y, int z,
                           const Config& cfg, const Blocks& blocks, const BlockIds& ids, int fakeSurface) {
    int current = blocksOut[static_cast<size_t>(idx)];
    if (!isDeepDarkOreReplaceable(current, ids, blocks)) {
        return;
    }

    int deepLo = MIN_Y + 3;
    int deepHi = std::min(cfg.floorY + 8, MIN_Y + WORLD_HEIGHT - 2);
    if (y < deepLo || y > deepHi || deepHi < deepLo) {
        return;
    }

    int range = std::max(1, deepHi - deepLo + 1);
    bool exposed = isDeepDarkOreExposed(cfg, x, y, z, fakeSurface);
    double exposureScale = exposed ? 1.85 : 0.52;

    for (size_t i = 0; i < cfg.ores.size(); ++i) {
        const OreCfg& ore = cfg.ores[i];
        if (ore.veinsPerChunk <= 0.0) {
            continue;
        }
        double base = (ore.veinsPerChunk * std::max(1, ore.veinSize)) / (256.0 * static_cast<double>(range));
        double p = clampDouble(base * exposureScale * 0.36, 0.0, 0.075);
        if (rand01(x, y, z, cfg, static_cast<uint32_t>(17000 + i * 149)) < p) {
            blocksOut[static_cast<size_t>(idx)] = static_cast<uint16_t>(blocks.id(deepDarkOreName(ore.name)));
            return;
        }
    }
}

void setBlock(std::vector<uint16_t>& a, int lx, int y, int lz, int id) {
    if (lx < 0 || lx >= 16 || lz < 0 || lz >= 16 || y < MIN_Y || y >= MIN_Y + WORLD_HEIGHT) {
        return;
    }
    a[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))] = static_cast<uint16_t>(id);
}

int getBlock(const std::vector<uint16_t>& a, int lx, int y, int lz) {
    if (lx < 0 || lx >= 16 || lz < 0 || lz >= 16 || y < MIN_Y || y >= MIN_Y + WORLD_HEIGHT) {
        return 0;
    }
    return a[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))];
}

bool isPlantLike(int id, const BlockIds& ids) {
    return id == ids.air || id == ids.shortGrass || id == ids.fern ||
           id == ids.tallGrassLo || id == ids.tallGrassHi || id == ids.deadBush ||
           id == ids.vineNorth || id == ids.vineSouth || id == ids.vineEast || id == ids.vineWest ||
           id == ids.lilyPad || id == ids.blueOrchid || id == ids.brownMushroom || id == ids.redMushroom;
}

void placeVineRun(std::vector<uint16_t>& a, int lx, int y, int lz, int vineId, int maxLen, const BlockIds& ids) {
    int len = 0;
    for (int yy = y; yy >= MIN_Y + 1 && len < maxLen; --yy) {
        int cur = getBlock(a, lx, yy, lz);
        if (!isPlantLike(cur, ids)) {
            break;
        }
        setBlock(a, lx, yy, lz, vineId);
        ++len;
    }
}

void placeTree(std::vector<uint16_t>& a, int lx, int lz, int y, const BlockIds& ids) {
    int h = 4;
    for (int i = 1; i <= h; ++i) {
        setBlock(a, lx, y + i, lz, ids.oakLog);
    }
    for (int dy = h - 2; dy <= h + 1; ++dy) {
        int rad = (dy >= h) ? 2 : 1;
        for (int dz = -rad; dz <= rad; ++dz) {
            for (int dx = -rad; dx <= rad; ++dx) {
                if (std::abs(dx) == rad && std::abs(dz) == rad && dy < h) {
                    continue;
                }
                if (getBlock(a, lx + dx, y + dy, lz + dz) == ids.air) {
                    setBlock(a, lx + dx, y + dy, lz + dz, ids.oakLeaves);
                }
            }
        }
    }
}


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

double depthBonus01(int y, int lo, int hi) {
    if (hi <= lo) {
        return 0.0;
    }
    return clampDouble(static_cast<double>(hi - y) / static_cast<double>(hi - lo), 0.0, 1.0);
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

int snowCapDepth(const Config& cfg, const TerrainSample& s, const BlockIds& ids) {
    if (s.topId != ids.snowBlock) {
        return 0;
    }
    double altitude = std::max(0, s.height - (cfg.snowlineY - 6));
    double mountain = clampDouble(s.mountain, 0.0, 1.0);
    return clampInt(2 + static_cast<int>(altitude / 8.0) + static_cast<int>(mountain * 5.0), 2, 10);
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

void placeChunkDeepDarkOres(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16]) {
    (void)a;
    (void)cfg;
    (void)blocks;
    (void)ids;
    (void)cx;
    (void)cz;
    (void)inside;
    // Deep-dark ore generation is handled by the regular chunk ore pass now so
    // we do not create a separate dense band fighting a second lower-density pass.
}


bool isLiquidSolidSurface(int id, const BlockIds& ids) {
    return id != ids.air && id != ids.water && id != ids.lava &&
           id != ids.shortGrass && id != ids.fern && id != ids.tallGrassLo &&
           id != ids.tallGrassHi && id != ids.deadBush && id != ids.cactus &&
           id != ids.sugarCane && id != ids.vineNorth && id != ids.vineSouth &&
           id != ids.vineEast && id != ids.vineWest && id != ids.lilyPad &&
           id != ids.blueOrchid && id != ids.brownMushroom && id != ids.redMushroom;
}

void sealWaterBeds(std::vector<uint16_t>& a, const BlockIds& ids, const bool inside[16][16],
                   const bool onWall[16][16], const bool isWater[16][16], const bool isRiver[16][16],
                   const int heights[16][16], int floorY) {
    for (int lz = 0; lz < 16; ++lz) {
        for (int lx = 0; lx < 16; ++lx) {
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            int buffer = 0;
            for (int dz = -2; dz <= 2; ++dz) {
                for (int dx = -2; dx <= 2; ++dx) {
                    int z2 = lz + dz;
                    int x2 = lx + dx;
                    if (z2 < 0 || z2 >= 16 || x2 < 0 || x2 >= 16) {
                        continue;
                    }
                    if (isWater[z2][x2]) {
                        int d = std::abs(dx) + std::abs(dz);
                        buffer = std::max(buffer, 3 - d);
                    }
                }
            }
            if (buffer <= 0 && !isWater[lz][lx]) {
                continue;
            }
            int surf = heights[lz][lx];
            int depth = isWater[lz][lx] ? (isRiver[lz][lx] ? 8 : 7) : 4 + buffer;
            int y0 = std::max(floorY + 1, surf - depth);
            int y1 = std::min(surf, MIN_Y + WORLD_HEIGHT - 2);
            for (int y = y0; y <= y1; ++y) {
                int cur = getBlock(a, lx, y, lz);
                if (cur != ids.air) {
                    continue;
                }
                if (y >= surf - 1) {
                    setBlock(a, lx, y, lz, ids.sand);
                } else if (y >= surf - 4) {
                    setBlock(a, lx, y, lz, ids.sandstone);
                } else {
                    setBlock(a, lx, y, lz, ids.stone);
                }
            }
        }
    }
}

bool nearSurfaceLava(const std::array<std::array<TerrainSample, 16>, 16>& samples, int lx, int lz, int radius) {
    for (int dz = -radius; dz <= radius; ++dz) {
        for (int dx = -radius; dx <= radius; ++dx) {
            int nx = lx + dx;
            int nz = lz + dz;
            if (nx < 0 || nx >= 16 || nz < 0 || nz >= 16) {
                continue;
            }
            if (std::abs(dx) + std::abs(dz) > radius + 1) {
                continue;
            }
            if (samples[static_cast<size_t>(nz)][static_cast<size_t>(nx)].lava) {
                return true;
            }
        }
    }
    return false;
}

void encaseSurfaceLavaPools(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, int cx, int cz,
                             const std::array<std::array<TerrainSample, 16>, 16>& samples,
                             const bool inside[16][16], const bool onWall[16][16], int floorY) {
    if (!cfg.fluids) {
        return;
    }
    for (int lz = 0; lz < 16; ++lz) {
        for (int lx = 0; lx < 16; ++lx) {
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            const TerrainSample& s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
            bool nearLava = nearSurfaceLava(samples, lx, lz, 2);
            if (!s.lava && !nearLava) {
                continue;
            }

            int y0 = std::max(floorY + 1, s.height - 4);
            int y1 = std::min(s.height, MIN_Y + WORLD_HEIGHT - 2);
            for (int y = y0; y <= y1; ++y) {
                int cur = getBlock(a, lx, y, lz);
                if (cur != ids.bedrock && cur != ids.lava && cur != ids.water) {
                    setBlock(a, lx, y, lz, ids.stone);
                }
            }

            if (s.lava) {
                setBlock(a, lx, s.height, lz, rand01(cx * 16 + lx, s.height, cz * 16 + lz, cfg, 5451) < 0.20 ? ids.magma : ids.stone);
            } else if (nearLava && getBlock(a, lx, s.height, lz) != ids.lava && getBlock(a, lx, s.height, lz) != ids.water) {
                setBlock(a, lx, s.height, lz, ids.stone);
            }
        }
    }
}

void placeSurfaceFluidBeds(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, int cx, int cz,
                           const std::array<std::array<TerrainSample, 16>, 16>& samples,
                           const bool inside[16][16], const bool onWall[16][16]) {
    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            const TerrainSample& s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
            int xw = cx * 16 + lx;
            int surf = s.height;
            if (s.river || s.lake || (s.waterSurface > surf && surf < cfg.waterLevel)) {
                double gnoise = fbm2d(static_cast<double>(xw) + 20.0, static_cast<double>(z) - 20.0, cfg.seed + 71, 2, 1.0 / 14.0);
                double cnoise = fbm2d(static_cast<double>(xw) - 30.0, static_cast<double>(z) + 30.0, cfg.seed + 72, 2, 1.0 / 12.0);
                int bed = ids.sand;
                if (s.biome == BIOME_SWAMP) {
                    bed = (cnoise > 0.54) ? ids.clay : ids.mud;
                } else {
                    if (gnoise > 0.60) {
                        bed = ids.gravel;
                    }
                    if (cnoise > 0.60) {
                        bed = ids.clay;
                    }
                }
                setBlock(a, lx, surf, lz, bed);
                if (bed == ids.clay) {
                    setBlock(a, lx, surf - 1, lz, ids.clay);
                    setBlock(a, lx, surf - 2, lz, ids.clay);
                }
            }
            if (s.lava) {
                double n = fbm2d(static_cast<double>(xw) + 910.0, static_cast<double>(z) - 910.0, cfg.seed + 76, 2, 1.0 / 9.0);
                setBlock(a, lx, surf, lz, n > 0.58 ? ids.magma : ids.stone);
            }
        }
    }
}


void placeCavernWaterways(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                          int cx, int cz, const bool inside[16][16], const int heights[16][16]) {
    // These are underground water features only. A previous pass let the same
    // source planner place water near/above the surface, then the settling pass
    // spread it across grass as a fake lake layer. Keep surface rivers/lakes in
    // terrain sampling, and keep this feature comfortably below terrain.
    int loY = std::max(cfg.floorY + 8, MIN_Y + 8);
    int hiY = std::min(cfg.waterLevel - 18, cfg.floorY + 82);
    if (hiY <= loY) {
        return;
    }

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!inside[lz][lx]) {
                continue;
            }

            int localHi = std::min(hiY, heights[lz][lx] - 12);
            if (localHi <= loY) {
                continue;
            }

            double path = fbm2d(static_cast<double>(x) + 7400.0, static_cast<double>(z) - 6200.0, cfg.seed + 10410, 3, 1.0 / 118.0);
            double local = fbm2d(static_cast<double>(x) - 1290.0, static_cast<double>(z) + 2330.0, cfg.seed + 10411, 2, 1.0 / 34.0);
            double rare = hash01(floorDiv(x, 4), 0, floorDiv(z, 4), cfg.seed, 10418);
            if (path < 0.720 || local < 0.55 || rare > 0.48) {
                continue;
            }

            int baseY = loY + static_cast<int>(hash01(x, 0, z, cfg.seed, 10412) * static_cast<double>(localHi - loY + 1));
            int y0 = std::max(loY, baseY - 2);
            int y1 = std::min(localHi, baseY + 2);
            for (int y = y1; y >= y0; --y) {
                int cur = getBlock(a, lx, y, lz);
                if (cur != ids.air) {
                    continue;
                }
                bool anchored = isLiquidSolidSurface(getBlock(a, lx, y - 1, lz), ids) ||
                    isLiquidSolidSurface(getBlock(a, lx + 1, y, lz), ids) ||
                    isLiquidSolidSurface(getBlock(a, lx - 1, y, lz), ids) ||
                    isLiquidSolidSurface(getBlock(a, lx, y, lz + 1), ids) ||
                    isLiquidSolidSurface(getBlock(a, lx, y, lz - 1), ids);
                if (!anchored) {
                    continue;
                }
                setBlock(a, lx, y, lz, ids.water);
                break;
            }
        }
    }
}

void placeCavernLavaPockets(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16], const int heights[16][16]) {
    int loY = std::max(cfg.floorY + 8, MIN_Y + 8);
    int hiY = std::min(cfg.waterLevel - 24, cfg.floorY + 74);
    if (hiY <= loY) {
        return;
    }

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!inside[lz][lx]) {
                continue;
            }
            int localHi = std::min(hiY, heights[lz][lx] - 16);
            if (localHi <= loY) {
                continue;
            }

            // About half as common as the deep-dark lava planner, and only in
            // actual cavern air pockets so it does not create surface scars.
            double pool = fbm2d(static_cast<double>(x) - 6800.0, static_cast<double>(z) + 4200.0, cfg.seed + 10580, 3, 1.0 / 82.0);
            double edge = fbm2d(static_cast<double>(x) + 870.0, static_cast<double>(z) - 1660.0, cfg.seed + 10581, 2, 1.0 / 22.0);
            if (pool < 0.735 || edge < 0.44) {
                continue;
            }

            int level = loY + static_cast<int>(hash01(x, 0, z, cfg.seed, 10582) * static_cast<double>(localHi - loY + 1));
            level = clampInt(level, loY, localHi);
            for (int y = level; y >= std::max(loY, level - 3); --y) {
                if (getBlock(a, lx, y, lz) == ids.air && isLiquidSolidSurface(getBlock(a, lx, y - 1, lz), ids)) {
                    setBlock(a, lx, y, lz, ids.lava);
                    if (getBlock(a, lx, y - 1, lz) != ids.bedrock) {
                        setBlock(a, lx, y - 1, lz, ids.magma);
                    }
                    break;
                }
            }
        }
    }
}

void placeDeepDarkLavaLakes(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16]) {
    if (!cfg.deepDark) {
        return;
    }

    int loY = MIN_Y + 5;
    int hiY = std::min(cfg.floorY - 6, MIN_Y + 42);
    if (hiY <= loY) {
        return;
    }

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!inside[lz][lx]) {
                continue;
            }

            double pool = fbm2d(static_cast<double>(x) - 8800.0, static_cast<double>(z) + 5500.0, cfg.seed + 10520, 3, 1.0 / 72.0);
            double edge = fbm2d(static_cast<double>(x) + 1230.0, static_cast<double>(z) - 2140.0, cfg.seed + 10521, 2, 1.0 / 18.0);
            if (pool < 0.675 || edge < 0.38) {
                continue;
            }

            int level = loY + static_cast<int>(hash01(x, 0, z, cfg.seed, 10522) * static_cast<double>(hiY - loY + 1));
            level = clampInt(level, loY, hiY);
            for (int y = level; y >= std::max(loY, level - 4); --y) {
                int cur = getBlock(a, lx, y, lz);
                if (cur == ids.air && isLiquidSolidSurface(getBlock(a, lx, y - 1, lz), ids)) {
                    for (int dz = -1; dz <= 1; ++dz) {
                        for (int dx = -1; dx <= 1; ++dx) {
                            if (std::abs(dx) + std::abs(dz) > 1) {
                                continue;
                            }
                            int nx = lx + dx;
                            int nz = lz + dz;
                            if (nx < 0 || nx >= 16 || nz < 0 || nz >= 16 || !inside[nz][nx]) {
                                continue;
                            }
                            if (getBlock(a, nx, y, nz) == ids.air && isLiquidSolidSurface(getBlock(a, nx, y - 1, nz), ids)) {
                                setBlock(a, nx, y, nz, ids.lava);
                                if (getBlock(a, nx, y - 1, nz) != ids.bedrock) {
                                    setBlock(a, nx, y - 1, nz, ids.magma);
                                }
                            }
                        }
                    }
                    break;
                }
            }
        }
    }
}

void placeUndergroundFluids(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16], int floorY) {
    int lavaLevel = floorY + 4;
    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!inside[lz][lx]) {
                continue;
            }
            double lavaNoise = fbm2d(static_cast<double>(x) + 141.0, static_cast<double>(z) - 313.0, cfg.seed + 9101, 2, 1.0 / 36.0);
            double chanceGate = cfg.deepDark ? 0.60 : 0.72;
            if (lavaNoise <= chanceGate) {
                continue;
            }
            for (int y = floorY + 1; y <= lavaLevel; ++y) {
                if (getBlock(a, lx, y, lz) == ids.air && isLiquidSolidSurface(getBlock(a, lx, y - 1, lz), ids)) {
                    setBlock(a, lx, y, lz, ids.lava);
                }
            }
        }
    }
}

void fallLiquidColumns(std::vector<uint16_t>& a, const BlockIds& ids, const bool inside[16][16], int liquidId, int loY, int hiY) {
    for (int lz = 0; lz < 16; ++lz) {
        for (int lx = 0; lx < 16; ++lx) {
            if (!inside[lz][lx]) {
                continue;
            }
            bool falling = false;
            for (int y = hiY; y >= loY; --y) {
                int cur = getBlock(a, lx, y, lz);
                if (falling && cur == ids.air) {
                    setBlock(a, lx, y, lz, liquidId);
                    cur = liquidId;
                }
                bool solid = isLiquidSolidSurface(cur, ids);
                falling = ((falling || cur == liquidId) && !solid) || cur == liquidId;
            }
        }
    }
}

void settleOneLiquid(std::vector<uint16_t>& a, const BlockIds& ids, const bool inside[16][16], int liquidId,
                     int lateralSteps, int loY, int hiY, int canFallToY, bool allowFall) {
    int lo = std::max(MIN_Y, canFallToY);
    int hi = std::min(MIN_Y + WORLD_HEIGHT - 2, hiY);
    if (hi <= lo) {
        return;
    }
    for (int step = 0; step < std::max(1, lateralSteps); ++step) {
        if (allowFall) {
            fallLiquidColumns(a, ids, inside, liquidId, lo, hi);
        }
        std::vector<std::array<int, 3>> add;
        for (int y = std::max(loY, lo); y <= hi; ++y) {
            for (int lz = 0; lz < 16; ++lz) {
                for (int lx = 0; lx < 16; ++lx) {
                    if (!inside[lz][lx] || getBlock(a, lx, y, lz) != liquidId) {
                        continue;
                    }
                    if (!isLiquidSolidSurface(getBlock(a, lx, y - 1, lz), ids)) {
                        continue;
                    }
                    const int dirs[4][2] = {{1,0},{-1,0},{0,1},{0,-1}};
                    for (const auto& d : dirs) {
                        int nx = lx + d[0];
                        int nz = lz + d[1];
                        if (nx >= 0 && nx < 16 && nz >= 0 && nz < 16 && inside[nz][nx] && getBlock(a, nx, y, nz) == ids.air) {
                            add.push_back({nx, y, nz});
                        }
                    }
                }
            }
        }
        if (add.empty()) {
            break;
        }
        for (const auto& p : add) {
            setBlock(a, p[0], p[1], p[2], liquidId);
        }
    }
    if (allowFall) {
        fallLiquidColumns(a, ids, inside, liquidId, lo, hi);
    }
}

void settleLiquidFlow(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, const bool inside[16][16], int floorY) {
    int waterLo = std::max(cfg.floorY + 3, floorY + 1);
    int waterHi = std::min(cfg.waterLevel - 18, cfg.floorY + 82);
    if (waterHi > waterLo) {
        settleOneLiquid(a, ids, inside, ids.water, 2, waterLo, waterHi, waterLo, true);
    }

    int lavaLo = floorY + 1;
    int lavaHi = cfg.deepDark ? std::min(cfg.floorY + 64, MIN_Y + WORLD_HEIGHT - 6) : std::min(cfg.waterLevel - 12, MIN_Y + WORLD_HEIGHT - 8);
    if (lavaHi > lavaLo) {
        settleOneLiquid(a, ids, inside, ids.lava, cfg.deepDark ? 3 : 2, lavaLo, lavaHi, lavaLo, true);
    }
}

int badlandsBandBlock(const Config& cfg, const BlockIds& ids, int x, int y, int z) {
    int colors[6] = {ids.terracotta, ids.orangeTerracotta, ids.yellowTerracotta, ids.brownTerracotta, ids.redTerracotta, ids.whiteTerracotta};
    int wobble = static_cast<int>((fbm2d(static_cast<double>(x) + 220.0, static_cast<double>(z) - 440.0, cfg.seed + 8601, 3, 1.0 / 46.0) - 0.5) * 5.0);
    int band = floorDiv(y + wobble + 512, 5) % 6;
    if (band < 0) {
        band += 6;
    }
    return colors[band];
}

int logForBiome(int biome, const BlockIds& ids) {
    if (biome == BIOME_BIRCH) return ids.birchLog;
    if (biome == BIOME_TAIGA) return ids.spruceLog;
    if (biome == BIOME_JUNGLE) return ids.jungleLog;
    return ids.oakLog;
}

int leavesForBiome(int biome, const BlockIds& ids) {
    if (biome == BIOME_BIRCH) return ids.birchLeaves;
    if (biome == BIOME_TAIGA) return ids.spruceLeaves;
    if (biome == BIOME_JUNGLE) return ids.jungleLeaves;
    return ids.oakLeaves;
}

void placeBiomeTree(std::vector<uint16_t>& a, int lx, int lz, int y, int biome, int worldX, int worldZ, const BlockIds& ids, const Config& cfg) {
    double variant = rand01(worldX, y, worldZ, cfg, 6401);
    int trunkH = 4 + static_cast<int>(rand01(worldX, y, worldZ, cfg, 6402) * 2.0);
    if (biome == BIOME_BIRCH) {
        trunkH = 5 + static_cast<int>(rand01(worldX, y, worldZ, cfg, 6403) * 3.0);
    } else if (biome == BIOME_TAIGA) {
        trunkH = 6 + static_cast<int>(rand01(worldX, y, worldZ, cfg, 6404) * 4.0);
    } else if (biome == BIOME_JUNGLE) {
        trunkH = 7 + static_cast<int>(rand01(worldX, y, worldZ, cfg, 6405) * 6.0);
    } else if (biome == BIOME_SWAMP) {
        trunkH = 4 + static_cast<int>(rand01(worldX, y, worldZ, cfg, 6406) * 2.0);
    }

    int log = logForBiome(biome, ids);
    int leaf = leavesForBiome(biome, ids);
    for (int i = 1; i <= trunkH; ++i) {
        setBlock(a, lx, y + i, lz, log);
    }

    int top = y + trunkH;
    if (biome == BIOME_TAIGA) {
        for (int ly = top - 3; ly <= top + 2; ++ly) {
            int rad = 1;
            if (ly == top - 2 || ly == top - 1) rad = 2;
            if (variant > 0.72 && ly == top - 3) rad = 2;
            if (ly >= top + 1) rad = 1;
            for (int dz = -rad; dz <= rad; ++dz) {
                for (int dx = -rad; dx <= rad; ++dx) {
                    if (dx * dx + dz * dz > (rad + 0.25) * (rad + 0.25)) continue;
                    if (getBlock(a, lx + dx, ly, lz + dz) == ids.air) setBlock(a, lx + dx, ly, lz + dz, leaf);
                }
            }
        }
        return;
    }

    int lower = (biome == BIOME_JUNGLE) ? top - 1 : (biome == BIOME_SWAMP ? top - 2 : top - 2);
    int upper = (biome == BIOME_JUNGLE) ? top + 4 : (biome == BIOME_SWAMP ? top + 1 : top + 2);
    for (int ly = lower; ly <= upper; ++ly) {
        int rad = 1;
        if (ly == top - 1 || ly == top) rad = 2;
        if (biome == BIOME_JUNGLE && ly >= top - 1 && ly <= top + 1) rad = variant > 0.55 ? 3 : 2;
        if (biome == BIOME_SWAMP && ly >= top - 1 && ly <= top) rad = 2;
        if (biome != BIOME_JUNGLE && variant > 0.62 && ly == top - 1) rad = 3;
        if (ly >= top + 2) rad = 1;
        for (int dz = -rad; dz <= rad; ++dz) {
            for (int dx = -rad; dx <= rad; ++dx) {
                double d2 = static_cast<double>(dx * dx + dz * dz);
                double edge = rand01(worldX + dx, ly, worldZ + dz, cfg, 6500);
                if (d2 > (rad + 0.42) * (rad + 0.42)) continue;
                if (std::abs(dx) == rad && std::abs(dz) == rad && edge < 0.45) continue;
                if (getBlock(a, lx + dx, ly, lz + dz) == ids.air) setBlock(a, lx + dx, ly, lz + dz, leaf);
            }
        }
    }

    if (biome == BIOME_JUNGLE || biome == BIOME_SWAMP) {
        struct VineSide { int dx; int dz; int id; };
        const VineSide sides[4] = {
            {0, -1, ids.vineSouth}, // block north of the trunk attaches to its south face
            {1, 0, ids.vineWest},   // block east of the trunk attaches to its west face
            {0, 1, ids.vineNorth},  // block south of the trunk attaches to its north face
            {-1, 0, ids.vineEast}   // block west of the trunk attaches to its east face
        };
        double vineChance = biome == BIOME_JUNGLE ? 0.72 : 0.46;
        int vineMax = biome == BIOME_JUNGLE ? 5 : 3;
        for (int ly = y + 1; ly <= top; ++ly) {
            for (const VineSide& side : sides) {
                if (rand01(worldX + side.dx, ly, worldZ + side.dz, cfg, 6510) >= vineChance) {
                    continue;
                }
                int vx = lx + side.dx;
                int vz = lz + side.dz;
                if (!isPlantLike(getBlock(a, vx, ly, vz), ids)) {
                    continue;
                }
                int len = 1 + static_cast<int>(rand01(worldX + side.dx, ly, worldZ + side.dz, cfg, 6511) * static_cast<double>(vineMax));
                placeVineRun(a, vx, ly, vz, side.id, len, ids);
            }
        }
    }
}


void decorateSurface(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, int cx, int cz,
                     const std::array<std::array<TerrainSample, 16>, 16>& samples,
                     const bool inside[16][16], const bool onWall[16][16]) {
    if (!cfg.decorations) {
        return;
    }
    for (int lz = 0; lz < 16; ++lz) {
        for (int lx = 0; lx < 16; ++lx) {
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            int x = cx * 16 + lx;
            int z = cz * 16 + lz;
            const TerrainSample& s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
            if (s.biome == BIOME_SWAMP && (s.river || s.lake || s.waterSurface > s.height) &&
                s.waterSurface + 1 < MIN_Y + WORLD_HEIGHT &&
                getBlock(a, lx, s.waterSurface, lz) == ids.water &&
                getBlock(a, lx, s.waterSurface + 1, lz) == ids.air &&
                rand01(x, s.waterSurface + 1, z, cfg, 7250) < 0.20) {
                setBlock(a, lx, s.waterSurface + 1, lz, ids.lilyPad);
                continue;
            }
            if (s.river || s.lake || s.waterSurface > s.height || s.lava || nearSurfaceLava(samples, lx, lz, 2)) {
                continue;
            }
            int ay = s.height + 1;
            if (ay >= MIN_Y + WORLD_HEIGHT - 3 || getBlock(a, lx, ay, lz) != ids.air) {
                continue;
            }
            int ground = getBlock(a, lx, s.height, lz);
            bool nearWater = false;
            const int dirs[4][2] = {{1,0},{-1,0},{0,1},{0,-1}};
            for (const auto& d : dirs) {
                int nx = lx + d[0];
                int nz = lz + d[1];
                if (nx >= 0 && nx < 16 && nz >= 0 && nz < 16) {
                    const TerrainSample& n = samples[static_cast<size_t>(nz)][static_cast<size_t>(nx)];
                    nearWater = nearWater || n.river || n.lake || n.waterSurface > n.height;
                }
            }
            if ((ground == ids.grass || ground == ids.grassSnow || ground == ids.dirt || ground == ids.podzol || ground == ids.sand || ground == ids.redSand) &&
                nearWater && rand01(x, ay, z, cfg, 7100) < 0.22) {
                int h = 1 + static_cast<int>(rand01(x, ay, z, cfg, 7101) * 3.0);
                for (int k = 0; k < h; ++k) setBlock(a, lx, ay + k, lz, ids.sugarCane);
                continue;
            }
            if (s.biome == BIOME_SWAMP) {
                double rv = rand01(x, ay, z, cfg, 7250);
                if (ground == ids.grass || ground == ids.mud || ground == ids.dirt || ground == ids.clay) {
                    if (rv < 0.018) {
                        setBlock(a, lx, ay, lz, ids.blueOrchid);
                    } else if (rv < 0.034) {
                        setBlock(a, lx, ay, lz, rand01(x, ay, z, cfg, 7251) < 0.55 ? ids.brownMushroom : ids.redMushroom);
                    } else if (rv < 0.060) {
                        setBlock(a, lx, ay, lz, ids.deadBush);
                    }
                }
            }

            if ((s.desert || s.badlands) && (ground == ids.sand || ground == ids.redSand)) {
                double rv = rand01(x, ay, z, cfg, 7200);
                if (rv < 0.006) {
                    bool ok = true;
                    for (const auto& d : dirs) {
                        if (getBlock(a, lx + d[0], ay, lz + d[1]) != ids.air) ok = false;
                    }
                    if (ok) {
                        int h = 1 + static_cast<int>(rand01(x, ay, z, cfg, 7201) * 3.0);
                        for (int k = 0; k < h; ++k) setBlock(a, lx, ay + k, lz, ids.cactus);
                    }
                } else if (rv < 0.03) {
                    setBlock(a, lx, ay, lz, ids.deadBush);
                }
                continue;
            }
            if (s.snowy) {
                continue;
            }
            if (!(ground == ids.grass || ground == ids.grassSnow || ground == ids.podzol)) {
                continue;
            }
            double rv = rand01(x, ay, z, cfg, 7300);
            if (s.biome == BIOME_JUNGLE) {
                if (rv < 0.34) setBlock(a, lx, ay, lz, ids.shortGrass);
                else if (rv < 0.40 && getBlock(a, lx, ay + 1, lz) == ids.air) { setBlock(a, lx, ay, lz, ids.tallGrassLo); setBlock(a, lx, ay + 1, lz, ids.tallGrassHi); }
            } else if (s.biome == BIOME_TAIGA) {
                if (rv < 0.16) setBlock(a, lx, ay, lz, ids.fern);
            } else if (s.biome == BIOME_SWAMP) {
                if (rv < 0.22) setBlock(a, lx, ay, lz, ids.shortGrass);
                else if (rv < 0.30) setBlock(a, lx, ay, lz, ids.fern);
                else if (rv < 0.34 && getBlock(a, lx, ay + 1, lz) == ids.air) { setBlock(a, lx, ay, lz, ids.tallGrassLo); setBlock(a, lx, ay + 1, lz, ids.tallGrassHi); }
            } else {
                if (rv < 0.18) setBlock(a, lx, ay, lz, ids.shortGrass);
                else if (rv < 0.22 && getBlock(a, lx, ay + 1, lz) == ids.air) { setBlock(a, lx, ay, lz, ids.tallGrassLo); setBlock(a, lx, ay + 1, lz, ids.tallGrassHi); }
                else if (rv < 0.245) setBlock(a, lx, ay, lz, ids.flowers[static_cast<size_t>(clampInt(static_cast<int>(rand01(x, ay, z, cfg, 7301) * 5.0), 0, 4))]);
            }
        }
    }
}


struct CaveNode {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

uint32_t caveHash2(int64_t x, int64_t z, int seed, uint32_t salt) {
    uint32_t n = (static_cast<uint32_t>(x) * 0x632be59bu) ^
                 (static_cast<uint32_t>(z) * 0x85157af5u) ^
                 (static_cast<uint32_t>(seed) * 0x9e3779b9u) ^
                 (salt * 0x165667b1u);
    return mix32(n);
}

double caveRand01(int64_t x, int64_t z, const Config& cfg, uint32_t salt) {
    return static_cast<double>(caveHash2(x, z, cfg.seed, salt)) / 4294967296.0;
}

double caveRandRange(int64_t x, int64_t z, const Config& cfg, uint32_t salt, double lo, double hi) {
    return lo + (hi - lo) * caveRand01(x, z, cfg, salt);
}

int caveTargetY(const Config& cfg, int floorY) {
    if (cfg.deepDark && floorY <= MIN_Y + 1) {
        return floorY + 22;
    }
    int targetY = -12;
    for (const OreCfg& ore : cfg.ores) {
        if (ore.name.find("diamond") != std::string::npos) {
            targetY = std::min(ore.yMax - 2, ore.yMin + 4);
            break;
        }
    }
    return std::max(floorY + 5, std::min(8, targetY));
}

bool caveReplaceable(int id, const BlockIds& ids, const Blocks& blocks) {
    if (id == ids.air || id == ids.bedrock || id == ids.water || id == ids.lava ||
        id == ids.shortGrass || id == ids.fern || id == ids.tallGrassLo || id == ids.tallGrassHi ||
        id == ids.deadBush || id == ids.cactus || id == ids.sugarCane) {
        return false;
    }
    if (id >= 0 && id < static_cast<int>(blocks.names.size())) {
        const std::string& n = blocks.names[static_cast<size_t>(id)];
        if (n.find("log") != std::string::npos || n.find("leaves") != std::string::npos || n.find("snow") != std::string::npos) {
            return false;
        }
    }
    return true;
}

void carveBlockAir(std::vector<uint16_t>& a, int lx, int y, int lz, const BlockIds& ids, const Blocks& blocks) {
    if (lx < 0 || lx >= 16 || lz < 0 || lz >= 16 || y < MIN_Y || y >= MIN_Y + WORLD_HEIGHT) {
        return;
    }
    int cur = getBlock(a, lx, y, lz);
    if (caveReplaceable(cur, ids, blocks)) {
        setBlock(a, lx, y, lz, ids.air);
    }
}

void carveRoomNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                     int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                     const CaveNode& c, double rx, double ry, double rz) {
    rx = std::max(2.10, rx);
    ry = std::max(1.95, ry);
    rz = std::max(2.10, rz);
    double pad = std::max(rx, rz) + 2.0;
    int minX = std::max(0, static_cast<int>(std::floor(c.x - pad)) - cx * 16);
    int maxX = std::min(15, static_cast<int>(std::ceil(c.x + pad)) - cx * 16);
    int minZ = std::max(0, static_cast<int>(std::floor(c.z - pad)) - cz * 16);
    int maxZ = std::min(15, static_cast<int>(std::ceil(c.z + pad)) - cz * 16);
    int minY = std::max(floorY + 2, static_cast<int>(std::floor(c.y - ry - 2.0)));
    int maxY = std::min(MIN_Y + WORLD_HEIGHT - 2, static_cast<int>(std::ceil(c.y + ry + 2.0)));
    if (maxX < minX || maxZ < minZ || maxY <= minY) {
        return;
    }
    for (int lz = minZ; lz <= maxZ; ++lz) {
        for (int lx = minX; lx <= maxX; ++lx) {
            if (!inside[lz][lx]) {
                continue;
            }
            double wx = static_cast<double>(cx * 16 + lx);
            double wz = static_cast<double>(cz * 16 + lz);
            for (int y = minY; y <= std::min(maxY, heights[lz][lx] + 1); ++y) {
                double d = ((wx - c.x) / rx) * ((wx - c.x) / rx) +
                           ((static_cast<double>(y) - c.y) / ry) * ((static_cast<double>(y) - c.y) / ry) +
                           ((wz - c.z) / rz) * ((wz - c.z) / rz);
                if (d <= 1.0) {
                    carveBlockAir(a, lx, y, lz, ids, blocks);
                }
            }
        }
    }
}

void carveCapsuleNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                        int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                        const CaveNode& p0, const CaveNode& p1, double radius) {
    radius = std::max(2.20, radius);
    double vx = p1.x - p0.x;
    double vy = p1.y - p0.y;
    double vz = p1.z - p0.z;
    double denom = vx * vx + vy * vy + vz * vz;
    if (denom <= 0.001) {
        return;
    }
    double pad = radius + 2.0;
    int minX = std::max(0, static_cast<int>(std::floor(std::min(p0.x, p1.x) - pad)) - cx * 16);
    int maxX = std::min(15, static_cast<int>(std::ceil(std::max(p0.x, p1.x) + pad)) - cx * 16);
    int minZ = std::max(0, static_cast<int>(std::floor(std::min(p0.z, p1.z) - pad)) - cz * 16);
    int maxZ = std::min(15, static_cast<int>(std::ceil(std::max(p0.z, p1.z) + pad)) - cz * 16);
    int minY = std::max(floorY + 2, static_cast<int>(std::floor(std::min(p0.y, p1.y) - radius - 2.0)));
    int maxY = std::min(MIN_Y + WORLD_HEIGHT - 2, static_cast<int>(std::ceil(std::max(p0.y, p1.y) + radius + 2.0)));
    if (maxX < minX || maxZ < minZ || maxY <= minY) {
        return;
    }
    double rr = radius * radius;
    for (int lz = minZ; lz <= maxZ; ++lz) {
        for (int lx = minX; lx <= maxX; ++lx) {
            if (!inside[lz][lx]) {
                continue;
            }
            double wx = static_cast<double>(cx * 16 + lx);
            double wz = static_cast<double>(cz * 16 + lz);
            for (int y = minY; y <= std::min(maxY, heights[lz][lx] + 1); ++y) {
                double wy = static_cast<double>(y);
                double t = ((wx - p0.x) * vx + (wy - p0.y) * vy + (wz - p0.z) * vz) / denom;
                t = clampDouble(t, 0.0, 1.0);
                double px = p0.x + vx * t;
                double py = p0.y + vy * t;
                double pz = p0.z + vz * t;
                double dist = (wx - px) * (wx - px) + ((wy - py) / 1.78) * ((wy - py) / 1.78) + (wz - pz) * (wz - pz);
                if (dist <= rr) {
                    carveBlockAir(a, lx, y, lz, ids, blocks);
                }
            }
        }
    }
}

uint32_t segmentSeed(uint32_t salt) {
    return static_cast<uint32_t>((static_cast<uint64_t>(salt) * 2654435761ull) & 0x7fffffffull);
}

std::vector<CaveNode> buildCaveSpineNative(const Config& cfg, int cellX, int cellZ, double ox, double oz, double startY, double targetY) {
    constexpr double PI = 3.14159265358979323846264338327950288;
    int segments = 10 + static_cast<int>(caveRand01(cellX, cellZ, cfg, 7800) * 5.0);
    double angle = caveRandRange(cellX, cellZ, cfg, 7801, 0.0, PI * 2.0);
    double turnBias = caveRandRange(cellX, cellZ, cfg, 7802, -0.18, 0.18);
    double x = ox;
    double z = oz;
    std::vector<CaveNode> nodes;
    nodes.push_back({x, startY, z});
    for (int i = 1; i <= segments; ++i) {
        double t = static_cast<double>(i) / static_cast<double>(segments);
        double turn = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7810 + i), -0.72, 0.72);
        turn += std::sin(i * 1.37 + caveRandRange(cellX, cellZ, cfg, 7820, 0.0, PI * 2.0)) * 0.16;
        angle += turn + turnBias;
        double step = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7830 + i), 16.0, 29.0);
        if (i % 4 == 0) {
            step *= caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7840 + i), 0.55, 0.85);
        }
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        double descent = std::pow(1.0 - t, 1.24);
        double y = targetY + (startY - targetY) * descent;
        y += std::sin(t * PI * 4.0 + caveRandRange(cellX, cellZ, cfg, 7850, 0.0, PI * 2.0)) * (3.6 * (1.0 - t));
        y += caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7860 + i), -2.4, 2.8) * (0.35 + 0.65 * (1.0 - t));
        y = std::max(targetY, std::min(startY, y));
        if (i == segments) {
            y = targetY;
        }
        nodes.push_back({x, y, z});
    }
    return nodes;
}

std::vector<CaveNode> buildCaveBranchNative(const Config& cfg, int cellX, int cellZ, int branch, CaveNode anchor, double targetY) {
    constexpr double PI = 3.14159265358979323846264338327950288;
    double x = anchor.x;
    double y = anchor.y;
    double z = anchor.z;
    double angle = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7900 + branch), 0.0, PI * 2.0);
    int segs = 3 + static_cast<int>(caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7910 + branch)) * 4.0);
    std::vector<CaveNode> nodes;
    nodes.push_back(anchor);
    for (int i = 1; i <= segs; ++i) {
        angle += caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7920 + branch * 7 + i), -0.95, 0.95);
        double step = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7930 + branch * 7 + i), 12.0, 25.0);
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        y -= caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7940 + branch * 7 + i), -1.5, 5.8);
        y = std::max(targetY + 2.0, y);
        nodes.push_back({x, y, z});
    }
    return nodes;
}

std::vector<CaveNode> buildUndergroundArmNative(const Config& cfg, int cellX, int cellZ, int arm, CaveNode center, int floorY, double targetY) {
    constexpr double PI = 3.14159265358979323846264338327950288;
    double x = center.x;
    double y = center.y;
    double z = center.z;
    double angle = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8400 + arm), 0.0, PI * 2.0);
    double pitch = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8401 + arm), -0.42, 0.32);
    int segs = 4 + static_cast<int>(caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8402 + arm)) * 6.0);
    std::vector<CaveNode> nodes;
    nodes.push_back(center);
    for (int i = 1; i <= segs; ++i) {
        angle += caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8410 + arm * 17 + i), -1.18, 1.18);
        pitch += caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8420 + arm * 17 + i), -0.30, 0.30);
        pitch = clampDouble(pitch, -0.62, 0.42);
        double step = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8430 + arm * 17 + i), 11.0, 28.0);
        if (caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8435 + arm * 17 + i)) < 0.22) {
            step *= caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8436 + arm * 17 + i), 1.35, 1.95);
        }
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        y += std::sin(pitch) * step * 0.55;
        y += caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8440 + arm * 17 + i), -4.5, 4.0);
        y = std::max(static_cast<double>(floorY + 6), std::min(static_cast<double>(cfg.waterLevel + 36), y));
        if (i == segs && caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8450 + arm)) < 0.34) {
            y = std::max(targetY + 1.0, y - caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8451 + arm), 8.0, 24.0));
        }
        nodes.push_back({x, y, z});
    }
    return nodes;
}

void carveLumpyRoomNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                          int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                          CaveNode c, double rx, double ry, double rz, int cellX, int cellZ, uint32_t salt, const Config& cfg) {
    constexpr double PI = 3.14159265358979323846264338327950288;
    carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, c, rx, ry, rz);
    uint32_t seedSalt = segmentSeed(salt);
    int lumps = 2 + static_cast<int>(caveRand01(cellX, cellZ, cfg, seedSalt + 8700) * 5.0);
    for (int i = 0; i < lumps; ++i) {
        if (caveRand01(cellX, cellZ, cfg, seedSalt + 8710 + i) < 0.18) {
            continue;
        }
        double a0 = caveRandRange(cellX, cellZ, cfg, seedSalt + 8720 + i, 0.0, PI * 2.0);
        double d = caveRandRange(cellX, cellZ, cfg, seedSalt + 8730 + i, 0.25, 0.82);
        CaveNode cc;
        cc.x = c.x + std::cos(a0) * rx * d;
        cc.z = c.z + std::sin(a0) * rz * d;
        cc.y = c.y + caveRandRange(cellX, cellZ, cfg, seedSalt + 8740 + i, -0.45, 0.52) * ry;
        carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, cc,
                        rx * caveRandRange(cellX, cellZ, cfg, seedSalt + 8750 + i, 0.34, 0.72),
                        ry * caveRandRange(cellX, cellZ, cfg, seedSalt + 8760 + i, 0.30, 0.68),
                        rz * caveRandRange(cellX, cellZ, cfg, seedSalt + 8770 + i, 0.34, 0.72));
    }
}

void carveVariedSegmentNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                              int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                              CaveNode p0, CaveNode p1, double radius, int cellX, int cellZ, uint32_t salt, const Config& cfg) {
    constexpr double PI = 3.14159265358979323846264338327950288;
    radius = std::max(2.20, radius);
    carveCapsuleNative(a, ids, blocks, cx, cz, inside, heights, floorY, p0, p1, radius);
    double vx = p1.x - p0.x;
    double vy = p1.y - p0.y;
    double vz = p1.z - p0.z;
    double length = std::sqrt(vx * vx + vy * vy + vz * vz);
    if (length <= 1.0) {
        return;
    }
    uint32_t seedSalt = segmentSeed(salt);
    int bumps = std::max(1, std::min(7, static_cast<int>(length / 15.0)));
    for (int j = 0; j < bumps; ++j) {
        if (caveRand01(cellX, cellZ, cfg, seedSalt + 8500 + j) > 0.68) {
            continue;
        }
        double t = (static_cast<double>(j) + caveRandRange(cellX, cellZ, cfg, seedSalt + 8510 + j, 0.25, 0.85)) / static_cast<double>(bumps);
        t = clampDouble(t, 0.0, 1.0);
        CaveNode c;
        c.x = p0.x + vx * t;
        c.y = p0.y + vy * t;
        c.z = p0.z + vz * t;
        double sideA = caveRandRange(cellX, cellZ, cfg, seedSalt + 8520 + j, 0.0, PI * 2.0);
        double sideD = radius * caveRandRange(cellX, cellZ, cfg, seedSalt + 8530 + j, 0.15, 1.65);
        c.x += std::cos(sideA) * sideD;
        c.z += std::sin(sideA) * sideD;
        c.y += caveRandRange(cellX, cellZ, cfg, seedSalt + 8540 + j, -1.7, 1.9);
        double rx = radius * caveRandRange(cellX, cellZ, cfg, seedSalt + 8550 + j, 0.70, 1.95);
        double ry = radius * caveRandRange(cellX, cellZ, cfg, seedSalt + 8560 + j, 0.55, 1.38);
        double rz = radius * caveRandRange(cellX, cellZ, cfg, seedSalt + 8570 + j, 0.70, 1.95);
        carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, c, rx, ry, rz);
        if (caveRand01(cellX, cellZ, cfg, seedSalt + 8580 + j) < 0.26) {
            CaveNode end;
            end.x = c.x + std::cos(sideA) * radius * caveRandRange(cellX, cellZ, cfg, seedSalt + 8590 + j, 2.5, 6.5);
            end.y = c.y + caveRandRange(cellX, cellZ, cfg, seedSalt + 8600 + j, -1.5, 2.2);
            end.z = c.z + std::sin(sideA) * radius * caveRandRange(cellX, cellZ, cfg, seedSalt + 8610 + j, 2.5, 6.5);
            carveCapsuleNative(a, ids, blocks, cx, cz, inside, heights, floorY, c, end,
                               radius * caveRandRange(cellX, cellZ, cfg, seedSalt + 8620 + j, 0.38, 0.78));
        }
    }
}

void carveNativeCaveSystems(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY) {
    if (!cfg.caves) {
        return;
    }
    double intensity = std::max(0.2, cfg.caveIntensity);
    int targetY = caveTargetY(cfg, floorY);
    int wx0 = cx * 16;
    int wx1 = wx0 + 15;
    int wz0 = cz * 16;
    int wz1 = wz0 + 15;

    auto iterCells = [&](int spacing, int reach, auto&& fn) {
        int cellX0 = floorDiv(wx0 - reach, spacing) - 1;
        int cellX1 = floorDiv(wx1 + reach, spacing) + 1;
        int cellZ0 = floorDiv(wz0 - reach, spacing) - 1;
        int cellZ1 = floorDiv(wz1 + reach, spacing) + 1;
        for (int cellZ = cellZ0; cellZ <= cellZ1; ++cellZ) {
            for (int cellX = cellX0; cellX <= cellX1; ++cellX) {
                fn(cellX, cellZ);
            }
        }
    };

    iterCells(112, 252, [&](int cellX, int cellZ) {
        if (caveRand01(cellX, cellZ, cfg, 7001) > std::min(0.94, 0.86 * intensity)) {
            return;
        }
        double ox = cellX * 112.0 + caveRandRange(cellX, cellZ, cfg, 7002, 8.0, 104.0);
        double oz = cellZ * 112.0 + caveRandRange(cellX, cellZ, cfg, 7003, 8.0, 104.0);
        int surf = sampleTerrain(cfg, static_cast<int>(std::round(ox)), static_cast<int>(std::round(oz))).height;
        if (surf <= cfg.waterLevel + 8 && caveRand01(cellX, cellZ, cfg, 7004) > 0.22) {
            return;
        }
        double startY = std::min(static_cast<double>(surf - 4), static_cast<double>(cfg.waterLevel + 48));
        startY = std::max(static_cast<double>(targetY + 48), startY);
        if (startY <= targetY + 10) {
            return;
        }
        std::vector<CaveNode> nodes = buildCaveSpineNative(cfg, cellX, cellZ, ox, oz, startY, static_cast<double>(targetY));
        double minX = nodes[0].x, maxX = nodes[0].x, minZ = nodes[0].z, maxZ = nodes[0].z;
        for (const CaveNode& n : nodes) {
            minX = std::min(minX, n.x); maxX = std::max(maxX, n.x);
            minZ = std::min(minZ, n.z); maxZ = std::max(maxZ, n.z);
        }
        if (wx1 < minX - 44.0 || wx0 > maxX + 44.0 || wz1 < minZ - 44.0 || wz0 > maxZ + 44.0) {
            return;
        }
        double baseR = caveRandRange(cellX, cellZ, cfg, 7010, 2.35, 3.75) * (0.98 + 0.20 * intensity);
        for (size_t i = 0; i + 1 < nodes.size(); ++i) {
            double r = baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7020 + static_cast<int>(i)), 0.70, 1.42);
            if (i == 0) r *= 1.42;
            if (i % 4 == 2) r *= 1.22;
            carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], nodes[i + 1], r,
                                     cellX, cellZ, static_cast<uint32_t>(702000 + static_cast<int>(i) * 37), cfg);
        }
        for (size_t i = 0; i < nodes.size(); ++i) {
            bool mandatory = i == 0 || i + 1 == nodes.size() || i % 3 == 1;
            if (!mandatory && caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7100 + static_cast<int>(i))) > 0.52) {
                continue;
            }
            double scale = i == 0 ? 1.32 : (i + 1 == nodes.size() ? 1.45 : (i % 3 == 1 ? 1.20 : 1.0));
            double rx = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7110 + static_cast<int>(i)), 3.8, 8.4) * scale;
            double ry = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7120 + static_cast<int>(i)), 2.6, 5.8) * scale;
            double rz = caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7130 + static_cast<int>(i)), 3.8, 8.4) * scale;
            carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], rx, ry, rz);
            if (caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7140 + static_cast<int>(i))) < 0.46) {
                carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], rx, ry, rz,
                                     cellX, cellZ, static_cast<uint32_t>(715000 + static_cast<int>(i) * 29), cfg);
            }
        }
        int branchCount = 5 + static_cast<int>(caveRand01(cellX, cellZ, cfg, 7200) * 7.0 * std::min(1.80, intensity));
        for (int b = 0; b < branchCount && nodes.size() > 4; ++b) {
            int anchorI = 1 + static_cast<int>(caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7210 + b)) * static_cast<double>(nodes.size() - 3));
            std::vector<CaveNode> branch = buildCaveBranchNative(cfg, cellX, cellZ, b, nodes[static_cast<size_t>(anchorI)], static_cast<double>(targetY));
            for (size_t i = 0; i + 1 < branch.size(); ++i) {
                double br = baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7300 + b * 13 + static_cast<int>(i)), 0.58, 1.18);
                carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, branch[i], branch[i + 1], br,
                                         cellX, cellZ, static_cast<uint32_t>(730000 + b * 101 + static_cast<int>(i) * 17), cfg);
            }
            CaveNode end = branch.back();
            carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, end,
                                 baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7400 + b), 1.25, 2.50),
                                 baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7410 + b), 0.95, 1.85),
                                 baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7420 + b), 1.25, 2.50),
                                 cellX, cellZ, static_cast<uint32_t>(742000 + b * 43), cfg);
        }
    });

    iterCells(86, 196, [&](int cellX, int cellZ) {
        if (caveRand01(cellX, cellZ, cfg, 8101) > std::min(0.96, 0.88 * intensity)) {
            return;
        }
        double ox = cellX * 86.0 + caveRandRange(cellX, cellZ, cfg, 8102, 5.0, 81.0);
        double oz = cellZ * 86.0 + caveRandRange(cellX, cellZ, cfg, 8103, 5.0, 81.0);
        int surf = sampleTerrain(cfg, static_cast<int>(std::round(ox)), static_cast<int>(std::round(oz))).height;
        double roll = caveRand01(cellX, cellZ, cfg, 8104);
        double lo = static_cast<double>(targetY + 3);
        double hi = std::min(static_cast<double>(cfg.waterLevel - 8), static_cast<double>(targetY + 42));
        if (roll >= 0.58 && roll < 0.86) {
            lo = static_cast<double>(targetY + 18);
            hi = std::min(static_cast<double>(cfg.waterLevel + 18), static_cast<double>(surf - 14));
        } else if (roll >= 0.86) {
            lo = std::max(static_cast<double>(targetY + 8), static_cast<double>(floorY + 10));
            hi = std::min(static_cast<double>(cfg.waterLevel + 38), static_cast<double>(surf - 18));
        }
        if (hi <= lo + 3.0) {
            return;
        }
        CaveNode center{ox, caveRandRange(cellX, cellZ, cfg, 8105, lo, hi), oz};
        double maxExtent = caveRandRange(cellX, cellZ, cfg, 8106, 72.0, 138.0);
        if (wx1 < ox - maxExtent || wx0 > ox + maxExtent || wz1 < oz - maxExtent || wz0 > oz + maxExtent) {
            return;
        }
        double baseR = caveRandRange(cellX, cellZ, cfg, 8110, 1.95, 3.95) * (0.95 + 0.18 * intensity);
        if (caveRand01(cellX, cellZ, cfg, 8111) < 0.22) {
            baseR *= caveRandRange(cellX, cellZ, cfg, 8112, 1.35, 1.85);
        }
        carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, center,
                             baseR * caveRandRange(cellX, cellZ, cfg, 8120, 1.7, 3.4),
                             baseR * caveRandRange(cellX, cellZ, cfg, 8121, 1.0, 2.0),
                             baseR * caveRandRange(cellX, cellZ, cfg, 8122, 1.7, 3.4),
                             cellX, cellZ, 812000, cfg);
        int arms = 4 + static_cast<int>(caveRand01(cellX, cellZ, cfg, 8130) * 6.0);
        for (int arm = 0; arm < arms; ++arm) {
            std::vector<CaveNode> nodes = buildUndergroundArmNative(cfg, cellX, cellZ, arm, center, floorY, static_cast<double>(targetY));
            for (size_t i = 0; i + 1 < nodes.size(); ++i) {
                double rr = baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8140 + arm * 19 + static_cast<int>(i)), 0.52, 1.38);
                if (i == 0) rr *= 1.15;
                carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], nodes[i + 1], rr,
                                         cellX, cellZ, static_cast<uint32_t>(814000 + arm * 211 + static_cast<int>(i) * 31), cfg);
                if (caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8150 + arm * 19 + static_cast<int>(i))) < 0.42) {
                    CaveNode n = nodes[i + 1];
                    carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, n,
                                         baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8160 + arm * 19 + static_cast<int>(i)), 1.1, 2.7),
                                         baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8170 + arm * 19 + static_cast<int>(i)), 0.7, 1.7),
                                         baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8180 + arm * 19 + static_cast<int>(i)), 1.1, 2.7),
                                         cellX, cellZ, static_cast<uint32_t>(819000 + arm * 211 + static_cast<int>(i) * 31), cfg);
                }
            }
            if (nodes.size() >= 3 && caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8200 + arm)) < 0.62) {
                int anchor = 1 + static_cast<int>(caveRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8201 + arm)) * static_cast<double>(nodes.size() - 2));
                std::vector<CaveNode> side = buildCaveBranchNative(cfg, cellX, cellZ, 100 + arm, nodes[static_cast<size_t>(anchor)], static_cast<double>(targetY));
                for (size_t i = 0; i + 1 < side.size(); ++i) {
                    carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, side[i], side[i + 1],
                                             baseR * caveRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8210 + arm * 7 + static_cast<int>(i)), 0.48, 1.05),
                                             cellX, cellZ, static_cast<uint32_t>(821000 + arm * 101 + static_cast<int>(i) * 23), cfg);
                }
            }
        }
    });
}


void carveFiniteRavinesNative(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                              int cx, int cz, const bool inside[16][16], const bool onWall[16][16],
                              const std::array<std::array<TerrainSample, 16>, 16>& samples,
                              const int heights[16][16], int floorY) {
    if (!cfg.ravines) {
        return;
    }
    constexpr double PI = 3.14159265358979323846264338327950288;
    int wx0 = cx * 16;
    int wx1 = wx0 + 15;
    int wz0 = cz * 16;
    int wz1 = wz0 + 15;
    int spacing = 330;
    int reach = 260;
    int cellX0 = floorDiv(wx0 - reach, spacing) - 1;
    int cellX1 = floorDiv(wx1 + reach, spacing) + 1;
    int cellZ0 = floorDiv(wz0 - reach, spacing) - 1;
    int cellZ1 = floorDiv(wz1 + reach, spacing) + 1;

    for (int cellZ = cellZ0; cellZ <= cellZ1; ++cellZ) {
        for (int cellX = cellX0; cellX <= cellX1; ++cellX) {
            if (caveRand01(cellX, cellZ, cfg, 8001) > 0.070) {
                continue;
            }
            double ox = cellX * spacing + caveRandRange(cellX, cellZ, cfg, 8002, 45.0, spacing - 45.0);
            double oz = cellZ * spacing + caveRandRange(cellX, cellZ, cfg, 8003, 45.0, spacing - 45.0);
            double angle = caveRandRange(cellX, cellZ, cfg, 8004, 0.0, PI * 2.0);
            double length = caveRandRange(cellX, cellZ, cfg, 8005, 82.0, 185.0);
            double width = caveRandRange(cellX, cellZ, cfg, 8006, 3.5, 7.0);
            double depth = caveRandRange(cellX, cellZ, cfg, 8007, 18.0, 44.0);
            double wiggle = caveRandRange(cellX, cellZ, cfg, 8008, 2.0, 11.0);
            double phase = caveRandRange(cellX, cellZ, cfg, 8009, 0.0, PI * 2.0);

            double dx = std::cos(angle);
            double dz = std::sin(angle);
            double px = -dz;
            double pz = dx;
            double half = length * 0.5;
            double minX = ox - std::abs(dx) * half - width - wiggle - 4.0;
            double maxX = ox + std::abs(dx) * half + width + wiggle + 4.0;
            double minZ = oz - std::abs(dz) * half - width - wiggle - 4.0;
            double maxZ = oz + std::abs(dz) * half + width + wiggle + 4.0;
            if (wx1 < minX || wx0 > maxX || wz1 < minZ || wz0 > maxZ) {
                continue;
            }

            bool lavaBottom = caveRand01(cellX, cellZ, cfg, 8010) < 0.38;
            for (int lz = 0; lz < 16; ++lz) {
                for (int lx = 0; lx < 16; ++lx) {
                    if (!inside[lz][lx] || onWall[lz][lx]) {
                        continue;
                    }
                    const TerrainSample& s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
                    if (s.river || s.lake || s.beach || s.waterSurface > s.height || s.height <= cfg.waterLevel + 7) {
                        continue;
                    }
                    double rx = static_cast<double>(cx * 16 + lx) - ox;
                    double rz = static_cast<double>(cz * 16 + lz) - oz;
                    double along = rx * dx + rz * dz;
                    double t = clampDouble((along + half) / std::max(1.0, length), 0.0, 1.0);
                    double taper = std::sin(PI * t);
                    if (taper <= 0.04) {
                        continue;
                    }
                    double lateralCenter = std::sin(t * PI * 1.65 + phase) * wiggle * taper;
                    double lateral = rx * px + rz * pz - lateralCenter;
                    double dist = std::abs(lateral);
                    double radius = width * (0.18 + 0.82 * std::sqrt(clampDouble(taper, 0.0, 1.0)));
                    if (dist >= radius) {
                        continue;
                    }
                    int bottom = std::max(floorY + 4, static_cast<int>(heights[lz][lx] - depth * (0.68 + 0.32 * taper)));
                    double height = std::max(1.0, static_cast<double>(heights[lz][lx] - bottom));
                    for (int y = bottom; y <= heights[lz][lx]; ++y) {
                        double relDepth = clampDouble((static_cast<double>(heights[lz][lx] - y)) / height, 0.0, 1.0);
                        double sideRadius = radius * (1.08 - 0.70 * relDepth);
                        sideRadius = std::max(sideRadius, radius * 0.22);
                        if (dist <= sideRadius) {
                            carveBlockAir(a, lx, y, lz, ids, blocks);
                        }
                    }
                    if (lavaBottom && dist < radius * 0.25 && bottom <= floorY + 9) {
                        for (int y = bottom; y <= std::min(bottom + 1, heights[lz][lx]); ++y) {
                            if (getBlock(a, lx, y, lz) == ids.air) {
                                setBlock(a, lx, y, lz, ids.lava);
                            }
                        }
                    }
                }
            }
        }
    }
}

void generateOverworldChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                            int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes) {
    std::fill(outBlocks.begin(), outBlocks.end(), static_cast<uint16_t>(ids.air));
    std::fill(outBiomes.begin(), outBiomes.end(), static_cast<uint16_t>(BIOME_PLAINS));

    int heights[16][16] = {};
    bool inside[16][16] = {};
    bool onWall[16][16] = {};
    bool isWater[16][16] = {};
    bool isRiver[16][16] = {};
    std::array<std::array<TerrainSample, 16>, 16> samples{};
    sampleTerrainChunk(cfg, blocks, ids, cx, cz, samples);

    int floorY = cfg.deepDark ? MIN_Y : cfg.floorY;
    int andesite = blocks.id("andesite");
    int diorite = blocks.id("diorite");
    int granite = blocks.id("granite");

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!insideArea(x, z, area)) {
                continue;
            }
            inside[lz][lx] = true;
            onWall[lz][lx] = cfg.wall && atAreaWall(x, z, area);
            TerrainSample s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
            heights[lz][lx] = s.height;
            isWater[lz][lx] = (s.waterSurface > s.height) && !s.lava;
            isRiver[lz][lx] = s.river;
            outBiomes[static_cast<size_t>(lz * 16 + lx)] = static_cast<uint16_t>(s.biome);

            for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                int id = ids.air;
                if (onWall[lz][lx] && y >= cfg.wallMinY && y <= cfg.wallMaxY) {
                    id = ids.bedrock;
                } else if (cfg.deepDark && y < cfg.floorY) {
                    if (y == MIN_Y) {
                        id = ids.bedrock;
                    } else {
                        double skin = fbm3d(static_cast<double>(x), static_cast<double>(y), static_cast<double>(z), cfg.seed + 10010, 2, 1.0 / 18.0);
                        if (skin > 0.74) id = ids.sculk;
                        else if (skin < 0.18) id = ids.tuff;
                        else id = ids.deepslate;
                        if (caveCarves(cfg, x, y, z, cfg.floorY - 1, MIN_Y + 1)) {
                            id = ids.air;
                        }
                    }
                } else if (y < floorY) {
                    id = ids.air;
                } else if (y == floorY) {
                    id = ids.bedrock;
                } else if (y <= s.height) {
                    int depth = s.height - y;
                    if (depth <= 3) {
                        id = s.subId != 0 ? s.subId : ids.dirt;
                        if (s.biome == BIOME_SWAMP) {
                            double swampMat = value3d(static_cast<double>(x) / 18.0, static_cast<double>(y) / 12.0, static_cast<double>(z) / 18.0, cfg.seed + 5331);
                            if (depth == 0 && swampMat > 0.62) {
                                id = ids.mud;
                            } else if (depth <= 2 && swampMat < 0.16) {
                                id = ids.clay;
                            } else if (depth <= 2 && swampMat > 0.52) {
                                id = ids.mud;
                            }
                        }
                    } else {
                        id = y < 0 ? ids.deepslate : ids.stone;
                        if (cfg.stoneVariants && y >= cfg.floorY) {
                            double v = value3d(static_cast<double>(x) / 22.0, static_cast<double>(y) / 18.0, static_cast<double>(z) / 22.0, cfg.seed + 3200);
                            double sediment = value3d(static_cast<double>(x) / 26.0, static_cast<double>(y) / 15.0, static_cast<double>(z) / 26.0, cfg.seed + 3219);
                            double coarse = value3d(static_cast<double>(x) / 15.0, static_cast<double>(y) / 11.0, static_cast<double>(z) / 15.0, cfg.seed + 3227);
                            bool patch = v > 0.80 || v < 0.14 || (v > 0.70 && v < 0.78);
                            if (patch && sediment > 0.835 && y < s.height - 8) {
                                id = ids.gravel;
                            } else if (patch && sediment < 0.105 && coarse > 0.42 && y < s.height - 10) {
                                id = coarse > 0.62 ? ids.coarseDirt : ids.dirt;
                            } else if (v > 0.82) {
                                id = andesite;
                            } else if (v < 0.12) {
                                id = granite;
                            } else if (v > 0.72 && v < 0.76) {
                                id = diorite;
                            }
                        }
                    }
                    int snowDepth = snowCapDepth(cfg, s, ids);
                    if (snowDepth > 0 && depth <= snowDepth) {
                        id = ids.snowBlock;
                    }
                    if (s.badlands) {
                        if (y >= s.height - 3) {
                            id = ids.redSand;
                        } else if (y <= s.height - 1 && y > floorY) {
                            id = badlandsBandBlock(cfg, ids, x, y, z);
                        }
                    }
                    if (caveCarves(cfg, x, y, z, s.height, floorY) && y > floorY + 1 && id != ids.bedrock) {
                        id = ids.air;
                    }
                } else if (cfg.fluids && s.lava && y <= s.lavaSurface) {
                    id = ids.lava;
                } else if (cfg.fluids && isWater[lz][lx] && y <= s.waterSurface) {
                    id = ids.water;
                }
                outBlocks[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))] = static_cast<uint16_t>(id);
            }
        }
    }

    carveNativeCaveSystems(outBlocks, cfg, blocks, ids, cx, cz, inside, heights, floorY);
    carveFiniteRavinesNative(outBlocks, cfg, blocks, ids, cx, cz, inside, onWall, samples, heights, floorY);
    sealWaterBeds(outBlocks, ids, inside, onWall, isWater, isRiver, heights, floorY);
    placeSurfaceFluidBeds(outBlocks, cfg, ids, cx, cz, samples, inside, onWall);
    encaseSurfaceLavaPools(outBlocks, cfg, ids, cx, cz, samples, inside, onWall, floorY);
    if (cfg.fluids) {
        placeCavernWaterways(outBlocks, cfg, ids, cx, cz, inside, heights);
        placeCavernLavaPockets(outBlocks, cfg, ids, cx, cz, inside, heights);
        placeDeepDarkLavaLakes(outBlocks, cfg, ids, cx, cz, inside);
        placeUndergroundFluids(outBlocks, cfg, ids, cx, cz, inside, floorY);
        settleLiquidFlow(outBlocks, cfg, ids, inside, floorY);
    }

    for (int lz = 0; lz < 16; ++lz) {
        for (int lx = 0; lx < 16; ++lx) {
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            TerrainSample s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
            if (!isWater[lz][lx] && !s.lava && !nearSurfaceLava(samples, lx, lz, 2) &&
                s.height > floorY + 3 && getBlock(outBlocks, lx, s.height, lz) != ids.air) {
                setBlock(outBlocks, lx, s.height, lz, s.topId != 0 ? s.topId : ids.grass);
            }
        }
    }

    placeChunkOres(outBlocks, cfg, blocks, ids, cx, cz, inside, heights);
    placeChunkDeepDarkOres(outBlocks, cfg, blocks, ids, cx, cz, inside);

    if (cfg.trees) {
        for (int lz = 1; lz < 15; ++lz) {
            int z = cz * 16 + lz;
            for (int lx = 1; lx < 15; ++lx) {
                int x = cx * 16 + lx;
                if (!inside[lz][lx] || onWall[lz][lx]) {
                    continue;
                }
                TerrainSample s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
                if (s.desert || s.badlands || s.river || s.lake || s.waterSurface > s.height || s.lava || nearSurfaceLava(samples, lx, lz, 2) || s.beach || s.snowy) {
                    continue;
                }
                int ground = getBlock(outBlocks, lx, s.height, lz);
                if (!(ground == ids.grass || ground == ids.grassSnow || ground == ids.podzol || ground == ids.dirt)) {
                    continue;
                }
                double density = 0.004;
                if (s.biome == BIOME_FOREST) density = 0.019;
                if (s.biome == BIOME_BIRCH) density = 0.020;
                if (s.biome == BIOME_JUNGLE) density = 0.052;
                if (s.biome == BIOME_TAIGA) density = 0.027;
                if (s.biome == BIOME_SWAMP) density = 0.016;
                density *= clampDouble(cfg.treeDensity, 0.0, 8.0);
                if (rand01(x, 0, z, cfg, 6100) < density) {
                    placeBiomeTree(outBlocks, lx, lz, s.height, s.biome, x, z, ids, cfg);
                }
            }
        }
    }

    decorateSurface(outBlocks, cfg, ids, cx, cz, samples, inside, onWall);

    for (int lz = 0; lz < 16; ++lz) {
        for (int lx = 0; lx < 16; ++lx) {
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            TerrainSample s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
            if (s.snowy && !isWater[lz][lx] && !s.lava && !nearSurfaceLava(samples, lx, lz, 2) && s.height > floorY + 3) {
                int ay = s.height + 1;
                int below = getBlock(outBlocks, lx, s.height, lz);
                if (getBlock(outBlocks, lx, ay, lz) == ids.air && below != ids.air && below != ids.water) {
                    setBlock(outBlocks, lx, ay, lz, ids.snowLayer);
                }
            }
        }
    }

    if (cfg.wall) {
        int wmin = std::max(MIN_Y, cfg.wallMinY);
        int wmax = std::min(MIN_Y + WORLD_HEIGHT - 1, cfg.wallMaxY);
        for (int lz = 0; lz < 16; ++lz) {
            for (int lx = 0; lx < 16; ++lx) {
                if (!onWall[lz][lx]) {
                    continue;
                }
                for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                    setBlock(outBlocks, lx, y, lz, (y >= wmin && y <= wmax) ? ids.bedrock : ids.air);
                }
            }
        }
    }
}

void generateNetherChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                                int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes) {
    std::fill(outBlocks.begin(), outBlocks.end(), static_cast<uint16_t>(ids.air));
    std::fill(outBiomes.begin(), outBiomes.end(), static_cast<uint16_t>(BIOME_NETHER));
    int netherrack = blocks.id("netherrack");
    int soulSand = blocks.id("soul_sand");
    int basalt = blocks.id("basalt");
    int blackstone = blocks.id("blackstone");
    int quartz = blocks.id("nether_quartz_ore");
    int netherGold = blocks.id("nether_gold_ore");

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;
        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!insideArea(x, z, area)) {
                continue;
            }
            int biome = netherBiome(cfg, x, z);
            outBiomes[static_cast<size_t>(lz * 16 + lx)] = static_cast<uint16_t>(biome);
            bool wallColumn = atAreaWall(x, z, area);
            for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                int id = ids.air;
                if (y < MIN_Y || y > NETHER_BOX_CEILING_Y) {
                    id = ids.air;
                } else if (wallColumn || y == MIN_Y || y == NETHER_BOX_CEILING_Y) {
                    id = ids.bedrock;
                } else {
                    id = netherrack;
                    if (biome == BIOME_SOUL && y < 45 && rand01(x, y, z, cfg, 9600) < 0.28) id = soulSand;
                    if (biome == BIOME_BASALT && rand01(x, y, z, cfg, 9700) < 0.38) id = basalt;
                    if (biome == BIOME_BASALT && rand01(x, y, z, cfg, 9701) < 0.08) id = blackstone;
                    double cave = fbm3d(static_cast<double>(x), static_cast<double>(y), static_cast<double>(z), cfg.seed + 9300, 3, 1.0 / 42.0);
                    if (cave > 0.62 && y > MIN_Y + 5 && y < NETHER_BOX_CEILING_Y - 5) {
                        id = (cfg.fluids && y < 30) ? ids.lava : ids.air;
                    } else if (rand01(x, y, z, cfg, 9800) < 0.0025 && y < 80) {
                        id = quartz;
                    } else if (rand01(x, y, z, cfg, 9801) < 0.0018 && y < 70) {
                        id = netherGold;
                    }
                }
                outBlocks[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))] = static_cast<uint16_t>(id);
            }
        }
    }
}
