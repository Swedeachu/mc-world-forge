#include "internal/chunk_passes.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/fuhc.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"
#include "internal/chunk_common.hpp"

namespace mcwf::chunk {

namespace {

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

void placeGrassPlant(std::vector<uint16_t>& a, int lx, int y, int lz,
                     const BlockIds& ids, const Config& cfg) {
    if (cfg.generator == "fuhc") {
        setBlock(a, lx, y, lz, ids.shortGrass);
        return;
    }

    setBlock(a, lx, y, lz, ids.tallGrassLo);
    setBlock(a, lx, y + 1, lz, ids.tallGrassHi);
}

}  // namespace

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

    if (cfg.generator == "fuhc" && cfg.fuhcGlassRoof) {
        int canopyHeight = biome == BIOME_JUNGLE ? 4 : 2;
        int roofY = cfg.fuhcBaseY + cfg.fuhcBedrockWallHeight + cfg.fuhcGlassWallHeight;
        int maxTrunkH = roofY - y - canopyHeight - 1;
        if (maxTrunkH < 4) {
            return;
        }
        trunkH = std::min(trunkH, maxTrunkH);
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

void decorateSurface(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, const Area& area,
                     int cx, int cz, const std::array<std::array<TerrainSample, 16>, 16>& samples,
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
            if (cfg.generator == "fuhc" && withinFuhcSpawnRadius(cfg, area, x, z, 0)) {
                continue;
            }
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
                else if (rv < 0.40 && getBlock(a, lx, ay + 1, lz) == ids.air) placeGrassPlant(a, lx, ay, lz, ids, cfg);
            } else if (s.biome == BIOME_TAIGA) {
                if (rv < 0.16) setBlock(a, lx, ay, lz, ids.fern);
            } else if (s.biome == BIOME_SWAMP) {
                if (rv < 0.22) setBlock(a, lx, ay, lz, ids.shortGrass);
                else if (rv < 0.30) setBlock(a, lx, ay, lz, ids.fern);
                else if (rv < 0.34 && getBlock(a, lx, ay + 1, lz) == ids.air) placeGrassPlant(a, lx, ay, lz, ids, cfg);
            } else {
                if (rv < 0.18) setBlock(a, lx, ay, lz, ids.shortGrass);
                else if (rv < 0.22 && getBlock(a, lx, ay + 1, lz) == ids.air) placeGrassPlant(a, lx, ay, lz, ids, cfg);
                else if (rv < 0.245) setBlock(a, lx, ay, lz, ids.flowers[static_cast<size_t>(clampInt(static_cast<int>(rand01(x, ay, z, cfg, 7301) * 5.0), 0, 4))]);
            }
        }
    }
}

}  // namespace mcwf::chunk
