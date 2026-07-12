#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"
#include "mcwf/terrain.hpp"

// High-level per-chunk generation passes, invoked in order by the overworld chunk assembler (chunk_generate.cpp).

namespace mcwf::chunk {

// Ores (chunk_ores.cpp).
void placeChunkOres(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                    int cx, int cz, const bool inside[16][16], const int heights[16][16]);

// Caves (chunk_caves.cpp).
void carveNativeCaveSystems(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY);

// Ravines (chunk_ravines.cpp).
void carveFiniteRavinesNative(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                              int cx, int cz, const bool inside[16][16], const bool onWall[16][16],
                              const std::array<std::array<TerrainSample, 16>, 16>& samples,
                              const int heights[16][16], int floorY);

// Fluids (chunk_fluids.cpp).
void sealWaterBeds(std::vector<uint16_t>& a, const BlockIds& ids, const bool inside[16][16],
                   const bool onWall[16][16], const bool isWater[16][16], const bool isRiver[16][16],
                   const int heights[16][16], int floorY);

void placeSurfaceFluidBeds(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, int cx, int cz,
                           const std::array<std::array<TerrainSample, 16>, 16>& samples,
                           const bool inside[16][16], const bool onWall[16][16]);

void encaseSurfaceLavaPools(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, int cx, int cz,
                            const std::array<std::array<TerrainSample, 16>, 16>& samples,
                            const bool inside[16][16], const bool onWall[16][16], int floorY);

void placeCavernWaterways(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                          int cx, int cz, const bool inside[16][16], const int heights[16][16]);

void placeCavernLavaPockets(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16], const int heights[16][16]);

void placeDeepDarkLavaLakes(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16]);

void placeUndergroundFluids(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16], int floorY);

void settleLiquidFlow(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, const bool inside[16][16], int floorY);

// Trees + surface decoration (chunk_decorate.cpp).
void placeBiomeTree(std::vector<uint16_t>& a, int lx, int lz, int y, int biome, int worldX, int worldZ, const BlockIds& ids, const Config& cfg);

void decorateSurface(std::vector<uint16_t>& a, const Config& cfg, const BlockIds& ids, const Area& area,
                     int cx, int cz, const std::array<std::array<TerrainSample, 16>, 16>& samples,
                     const bool inside[16][16], const bool onWall[16][16]);

}  // namespace mcwf::chunk
