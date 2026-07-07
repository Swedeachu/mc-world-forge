#include "internal/chunk_passes.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"
#include "internal/chunk_common.hpp"

namespace mcwf::chunk {

namespace {

bool isLiquidSolidSurface(int id, const BlockIds& ids) {
    return id != ids.air && id != ids.water && id != ids.lava &&
           id != ids.shortGrass && id != ids.fern && id != ids.tallGrassLo &&
           id != ids.tallGrassHi && id != ids.deadBush && id != ids.cactus &&
           id != ids.sugarCane && id != ids.vineNorth && id != ids.vineSouth &&
           id != ids.vineEast && id != ids.vineWest && id != ids.lilyPad &&
           id != ids.blueOrchid && id != ids.brownMushroom && id != ids.redMushroom;
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

}  // namespace

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

}  // namespace mcwf::chunk
