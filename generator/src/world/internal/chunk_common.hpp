#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "mcwf/blocks.hpp"
#include "mcwf/terrain.hpp"

// Low-level per-chunk block-array primitives shared by every chunk generation
// pass (surface fill, caves, ravines, fluids, ores, decoration).

namespace mcwf::chunk {

void setBlock(std::vector<uint16_t>& a, int lx, int y, int lz, int id);

int getBlock(const std::vector<uint16_t>& a, int lx, int y, int lz);

bool isPlantLike(int id, const BlockIds& ids);

void placeVineRun(std::vector<uint16_t>& a, int lx, int y, int lz, int vineId, int maxLen, const BlockIds& ids);

// Clears a block to air if it is a carve-replaceable material (used by both the
// finite cave systems and the ravine carver).
void carveBlockAir(std::vector<uint16_t>& a, int lx, int y, int lz, const BlockIds& ids, const Blocks& blocks);

bool nearSurfaceLava(const std::array<std::array<TerrainSample, 16>, 16>& samples, int lx, int lz, int radius);

}  // namespace mcwf::chunk
