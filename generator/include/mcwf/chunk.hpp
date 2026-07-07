#pragma once

#include <cstdint>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"

// Index helpers for the flat per-chunk block array (WORLD_HEIGHT * 16 * 16), laid out as [yIndex][z][x].

int blockIndex(int yIndex, int z, int x);

int yIndex(int y);

void generateOverworldChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                            int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes);

void generateNetherChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                         int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes);
