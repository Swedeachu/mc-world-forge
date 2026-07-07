#include "mcwf/chunk.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/constants.hpp"
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
