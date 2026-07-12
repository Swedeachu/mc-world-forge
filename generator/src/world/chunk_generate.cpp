#include "mcwf/chunk.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/fuhc.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/terrain.hpp"
#include "mcwf/util.hpp"
#include "internal/chunk_common.hpp"
#include "internal/chunk_passes.hpp"

using namespace mcwf::chunk;

namespace {

int snowCapDepth(const Config& cfg, const TerrainSample& s, const BlockIds& ids) {
    if (s.topId != ids.snowBlock) {
        return 0;
    }
    double altitude = std::max(0, s.height - (cfg.snowlineY - 6));
    double mountain = clampDouble(s.mountain, 0.0, 1.0);

    return clampInt(2 + static_cast<int>(altitude / 8.0) + static_cast<int>(mountain * 5.0), 2, 10);
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

void applyFuhcBiome(TerrainSample& sample, int biome, const BlockIds& ids) {
    sample.biome = biome;
    sample.badlands = biome == BIOME_BADLANDS;
    sample.desert = biome == BIOME_DESERT;
    sample.snowy = biome == BIOME_SNOW;
    sample.badlandsStrength = sample.badlands ? 1.0 : 0.0;

    if (sample.desert) {
        sample.topId = ids.sand;
        sample.subId = ids.sandstone;
    } else if (sample.badlands) {
        sample.topId = ids.redSand;
        sample.subId = ids.terracotta;
    } else if (sample.snowy) {
        sample.topId = ids.grassSnow;
        sample.subId = ids.dirt;
    } else if (biome == BIOME_TAIGA) {
        sample.topId = ids.podzol;
        sample.subId = ids.dirt;
    } else {
        sample.topId = ids.grass;
        sample.subId = ids.dirt;
    }
}

}  // namespace

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

    if (cfg.generator == "fuhc") {
        for (int lz = 0; lz < 16; ++lz) {
            int z = cz * 16 + lz;
            for (int lx = 0; lx < 16; ++lx) {
                int x = cx * 16 + lx;
                if (!insideArea(x, z, area)) {
                    continue;
                }

                TerrainSample& sample = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
                sample.height = fuhcSurfaceY(cfg, area, x, z);
                sample.river = false;
                sample.lake = false;
                sample.lava = false;
                sample.beach = false;
                sample.waterSurface = sample.height;
                sample.lavaSurface = sample.height;
                sample.mountain = 0.0;
                applyFuhcBiome(sample, fuhcBiomeAt(cfg, area, x, z), ids);
            }
        }
    }

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

    if (cfg.trees) {
        for (int lz = 1; lz < 15; ++lz) {
            int z = cz * 16 + lz;

            for (int lx = 1; lx < 15; ++lx) {
                int x = cx * 16 + lx;
                if (!inside[lz][lx] || onWall[lz][lx]) {
                    continue;
                }
                if (cfg.generator == "fuhc") {
                    constexpr int treeWallClearance = 4;
                    constexpr int treeSpawnClearance = 4;
                    if (x - area.xmin < treeWallClearance || area.xmax - x < treeWallClearance ||
                        z - area.zmin < treeWallClearance || area.zmax - z < treeWallClearance ||
                        withinFuhcSpawnRadius(cfg, area, x, z, treeSpawnClearance)) {
                        continue;
                    }
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

    decorateSurface(outBlocks, cfg, ids, area, cx, cz, samples, inside, onWall);

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

bool isNetherTerrainBlock(int id, const BlockIds& ids) {
    return id == ids.netherrack || id == ids.crimsonNylium || id == ids.warpedNylium ||
           id == ids.soulSand || id == ids.soulSoil || id == ids.basalt || id == ids.blackstone ||
           id == ids.magma || id == ids.netherQuartzOre || id == ids.netherGoldOre ||
           id == ids.ancientDebris;
}

bool isNetherAirReplaceable(int id, const BlockIds& ids) {
    return id == ids.air || id == ids.brownMushroom || id == ids.redMushroom || id == ids.glowstone;
}

bool isNetherBedrockShell(const Config& cfg, int x, int y, int z, bool wallColumn) {
    if (wallColumn) {
        return y >= NETHER_BOX_FLOOR_Y && y <= NETHER_BOX_CEILING_Y;
    }
    if (y == NETHER_BOX_FLOOR_Y || y == NETHER_BOX_CEILING_Y) {
        return true;
    }
    if (y > NETHER_BOX_FLOOR_Y && y < NETHER_BOX_FLOOR_Y + 5) {
        int d = y - NETHER_BOX_FLOOR_Y;
        double chance = d == 1 ? 0.72 : d == 2 ? 0.54 : d == 3 ? 0.30 : 0.12;

        return rand01(x, y, z, cfg, 12000) < chance;
    }
    if (y < NETHER_BOX_CEILING_Y && y > NETHER_BOX_CEILING_Y - 5) {
        int d = NETHER_BOX_CEILING_Y - y;
        double chance = d == 1 ? 0.72 : d == 2 ? 0.54 : d == 3 ? 0.30 : 0.12;

        return rand01(x, y, z, cfg, 12001) < chance;
    }

    return false;
}

bool netherDensitySolid(const Config& cfg, int x, int y, int z, int biome) {
    double d1 = fbm3d(static_cast<double>(x), static_cast<double>(y) * 1.52, static_cast<double>(z), cfg.seed + 12100, 4, 1.0 / 35.0);
    double d2 = fbm3d(static_cast<double>(x) - 850.0, static_cast<double>(y) * 1.44, static_cast<double>(z) + 850.0, cfg.seed + 12101, 3, 1.0 / 48.0);
    double ridged = 1.0 - std::abs(fbm3d(static_cast<double>(x) + 1600.0, static_cast<double>(y) * 1.12, static_cast<double>(z) - 1600.0, cfg.seed + 12102, 3, 1.0 / 58.0) - 0.5) * 2.0;
    double density = std::max(d1, d2) + ridged * 0.12;
    double ty = static_cast<double>(y - NETHER_BOX_FLOOR_Y) / static_cast<double>(NETHER_BOX_CEILING_Y - NETHER_BOX_FLOOR_Y);
    double edge = std::min(ty, 1.0 - ty);
    double threshold = 0.455 + 0.29 * (0.5 - edge) * 2.0;

    if (biome == BIOME_BASALT) {
        threshold -= 0.020;
    } else if (biome == BIOME_SOUL) {
        threshold += 0.015;
    }
    if (y <= NETHER_LAVA_SEA_Y + 3) {
        threshold += 0.020;
    }

    return density > threshold;
}

int netherBaseBlockForBiome(const Config& cfg, const BlockIds& ids, int x, int y, int z, int biome) {
    if (biome == BIOME_BASALT) {
        double rough = fbm3d(static_cast<double>(x), static_cast<double>(y), static_cast<double>(z), cfg.seed + 12200, 3, 1.0 / 18.0);
        if (rough > 0.70) return ids.blackstone;
        if (rough < 0.18) return ids.magma;
        return ids.basalt;
    }

    return ids.netherrack;
}

void skinNetherSurface(std::vector<uint16_t>& outBlocks, const Config& cfg, const BlockIds& ids, int lx, int y, int lz, int x, int z, int biome) {
    int block = getBlock(outBlocks, lx, y, lz);
    if (!isNetherTerrainBlock(block, ids)) {
        return;
    }
    if (getBlock(outBlocks, lx, y + 1, lz) != ids.air) {
        return;
    }

    if (biome == BIOME_CRIMSON) {
        if (block == ids.netherrack) {
            setBlock(outBlocks, lx, y, lz, ids.crimsonNylium);
        }
    } else if (biome == BIOME_WARPED) {
        if (block == ids.netherrack) {
            setBlock(outBlocks, lx, y, lz, ids.warpedNylium);
        }
    } else if (biome == BIOME_SOUL) {
        setBlock(outBlocks, lx, y, lz, rand01(x, y, z, cfg, 12300) < 0.62 ? ids.soulSand : ids.soulSoil);
    } else if (biome == BIOME_BASALT) {
        double r = rand01(x, y, z, cfg, 12301);
        setBlock(outBlocks, lx, y, lz, r < 0.48 ? ids.basalt : r < 0.86 ? ids.blackstone : ids.magma);
    }
}

void placeNetherFungus(std::vector<uint16_t>& outBlocks, const Config& cfg, const BlockIds& ids, int lx, int y, int lz, int x, int z, int biome) {
    if (biome != BIOME_CRIMSON && biome != BIOME_WARPED) {
        return;
    }
    if (getBlock(outBlocks, lx, y, lz) != (biome == BIOME_CRIMSON ? ids.crimsonNylium : ids.warpedNylium)) {
        return;
    }
    double chance = biome == BIOME_CRIMSON ? 0.030 : 0.026;
    if (rand01(x, y, z, cfg, biome == BIOME_CRIMSON ? 12400 : 12401) >= chance) {
        return;
    }

    int stem = biome == BIOME_CRIMSON ? ids.crimsonStem : ids.warpedStem;
    int cap = biome == BIOME_CRIMSON ? ids.netherWartBlock : ids.warpedWartBlock;
    int height = 4 + static_cast<int>(rand01(x, y, z, cfg, 12402) * 5.0);
    for (int k = 1; k <= height; ++k) {
        int yy = y + k;
        if (yy >= NETHER_BOX_CEILING_Y - 2 || !isNetherAirReplaceable(getBlock(outBlocks, lx, yy, lz), ids)) {
            return;
        }
    }

    for (int k = 1; k <= height; ++k) {
        setBlock(outBlocks, lx, y + k, lz, stem);
    }

    int capY = y + height;
    for (int dz = -2; dz <= 2; ++dz) {
        for (int dx = -2; dx <= 2; ++dx) {
            int ax = lx + dx;
            int az = lz + dz;
            if (ax < 0 || ax >= 16 || az < 0 || az >= 16) {
                continue;
            }
            int dist = std::abs(dx) + std::abs(dz);
            if (dist > 3) {
                continue;
            }
            int capBlock = (dx == 0 && dz == 0 && rand01(x + dx, capY, z + dz, cfg, 12403) < 0.46) ? ids.shroomlight : cap;
            if (isNetherAirReplaceable(getBlock(outBlocks, ax, capY, az), ids)) {
                setBlock(outBlocks, ax, capY, az, capBlock);
            }
            if (dist <= 2 && capY - 1 > y + 1 && isNetherAirReplaceable(getBlock(outBlocks, ax, capY - 1, az), ids)) {
                setBlock(outBlocks, ax, capY - 1, az, cap);
            }
        }
    }
}

void placeSoulValleyFeature(std::vector<uint16_t>& outBlocks, const Config& cfg, const BlockIds& ids, int lx, int y, int lz, int x, int z, int biome) {
    if (biome != BIOME_SOUL || getBlock(outBlocks, lx, y + 1, lz) != ids.air) {
        return;
    }
    if (rand01(x, y, z, cfg, 12500) >= 0.012) {
        return;
    }
    int height = 2 + static_cast<int>(rand01(x, y, z, cfg, 12501) * 5.0);
    for (int k = 1; k <= height; ++k) {
        if (getBlock(outBlocks, lx, y + k, lz) != ids.air) {
            break;
        }
        setBlock(outBlocks, lx, y + k, lz, ids.boneBlock);
    }
}

void placeBasaltColumn(std::vector<uint16_t>& outBlocks, const Config& cfg, const BlockIds& ids, int lx, int y, int lz, int x, int z, int biome) {
    if (biome != BIOME_BASALT || getBlock(outBlocks, lx, y + 1, lz) != ids.air) {
        return;
    }
    if (rand01(x, y, z, cfg, 12600) >= 0.035) {
        return;
    }
    int height = 3 + static_cast<int>(rand01(x, y, z, cfg, 12601) * 12.0);
    for (int k = 1; k <= height; ++k) {
        int yy = y + k;
        if (yy >= NETHER_BOX_CEILING_Y - 2 || getBlock(outBlocks, lx, yy, lz) != ids.air) {
            break;
        }
        setBlock(outBlocks, lx, yy, lz, ids.basalt);
    }
}

void placeGlowstoneBlob(std::vector<uint16_t>& outBlocks, const Config& cfg, const BlockIds& ids, int lx, int y, int lz, int x, int z) {
    if (getBlock(outBlocks, lx, y, lz) == ids.air || getBlock(outBlocks, lx, y - 1, lz) != ids.air) {
        return;
    }
    if (rand01(x, y, z, cfg, 12700) >= 0.006) {
        return;
    }

    int radius = 1 + static_cast<int>(rand01(x, y, z, cfg, 12701) * 2.0);
    for (int dy = 1; dy <= 2 + radius; ++dy) {
        for (int dz = -radius; dz <= radius; ++dz) {
            for (int dx = -radius; dx <= radius; ++dx) {
                if (std::abs(dx) + std::abs(dz) + dy > radius + 2) {
                    continue;
                }
                int ax = lx + dx;
                int ay = y - dy;
                int az = lz + dz;
                if (ax < 0 || ax >= 16 || az < 0 || az >= 16 || ay <= NETHER_BOX_FLOOR_Y + 4) {
                    continue;
                }
                if (getBlock(outBlocks, ax, ay, az) == ids.air) {
                    setBlock(outBlocks, ax, ay, az, ids.glowstone);
                }
            }
        }
    }
}

void generateNetherChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                         int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes) {
    (void)blocks;
    std::fill(outBlocks.begin(), outBlocks.end(), static_cast<uint16_t>(ids.air));
    std::fill(outBiomes.begin(), outBiomes.end(), static_cast<uint16_t>(BIOME_NETHER));
    bool inside[16][16] = {};
    bool onWall[16][16] = {};
    int biomeMap[16][16] = {};

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;

        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!insideArea(x, z, area)) {
                continue;
            }
            inside[lz][lx] = true;
            onWall[lz][lx] = atAreaWall(x, z, area);
            int biome = netherBiome(cfg, x, z);
            biomeMap[lz][lx] = biome;
            outBiomes[static_cast<size_t>(lz * 16 + lx)] = static_cast<uint16_t>(biome);

            for (int y = MIN_Y; y < MIN_Y + WORLD_HEIGHT; ++y) {
                int id = ids.air;
                if (y >= NETHER_BOX_FLOOR_Y && y <= NETHER_BOX_CEILING_Y) {
                    if (isNetherBedrockShell(cfg, x, y, z, onWall[lz][lx])) {
                        id = ids.bedrock;
                    } else {
                        bool solid = netherDensitySolid(cfg, x, y, z, biome);
                        if (solid) {
                            id = netherBaseBlockForBiome(cfg, ids, x, y, z, biome);
                        } else if (cfg.fluids && y <= NETHER_LAVA_SEA_Y) {
                            id = ids.lava;
                        }
                    }
                }
                outBlocks[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))] = static_cast<uint16_t>(id);
            }
        }
    }

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;

        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            int biome = biomeMap[lz][lx];
            for (int y = NETHER_BOX_FLOOR_Y + 1; y < NETHER_BOX_CEILING_Y; ++y) {
                int cur = getBlock(outBlocks, lx, y, lz);
                if (cur == ids.netherrack || cur == ids.basalt || cur == ids.blackstone) {
                    double ore = rand01(x, y, z, cfg, 12800);
                    if (y >= 10 && y <= 117 && cur == ids.netherrack && ore < (biome == BIOME_BASALT ? 0.020 : 0.010)) {
                        setBlock(outBlocks, lx, y, lz, ids.netherQuartzOre);
                    } else if (y >= 10 && y <= 117 && ore >= 0.026 && ore < 0.033) {
                        setBlock(outBlocks, lx, y, lz, ids.netherGoldOre);
                    } else if (y >= 8 && y <= 22 && ore >= 0.9965) {
                        setBlock(outBlocks, lx, y, lz, ids.ancientDebris);
                    } else if (y >= 8 && y <= 119 && ore >= 0.9989) {
                        setBlock(outBlocks, lx, y, lz, ids.ancientDebris);
                    }
                }
            }
        }
    }

    for (int lz = 0; lz < 16; ++lz) {
        int z = cz * 16 + lz;

        for (int lx = 0; lx < 16; ++lx) {
            int x = cx * 16 + lx;
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            int biome = biomeMap[lz][lx];
            for (int y = NETHER_BOX_FLOOR_Y + 1; y < NETHER_BOX_CEILING_Y; ++y) {
                skinNetherSurface(outBlocks, cfg, ids, lx, y, lz, x, z, biome);
            }
        }
    }

    for (int lz = 1; lz < 15; ++lz) {
        int z = cz * 16 + lz;

        for (int lx = 1; lx < 15; ++lx) {
            int x = cx * 16 + lx;
            if (!inside[lz][lx] || onWall[lz][lx]) {
                continue;
            }
            int biome = biomeMap[lz][lx];
            for (int y = NETHER_BOX_FLOOR_Y + 5; y < NETHER_BOX_CEILING_Y - 8; ++y) {
                if (getBlock(outBlocks, lx, y, lz) == ids.air && isNetherTerrainBlock(getBlock(outBlocks, lx, y - 1, lz), ids)) {
                    placeNetherFungus(outBlocks, cfg, ids, lx, y - 1, lz, x, z, biome);
                    placeSoulValleyFeature(outBlocks, cfg, ids, lx, y - 1, lz, x, z, biome);
                    placeBasaltColumn(outBlocks, cfg, ids, lx, y - 1, lz, x, z, biome);
                }
                if (getBlock(outBlocks, lx, y, lz) != ids.air && getBlock(outBlocks, lx, y - 1, lz) == ids.air) {
                    placeGlowstoneBlob(outBlocks, cfg, ids, lx, y, lz, x, z);
                }
            }
        }
    }
}
