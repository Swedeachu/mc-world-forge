#pragma once

#include <cstdint>
#include <vector>

#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"

// Minimal big-endian NBT / Anvil chunk serialization.

void putU8(std::vector<uint8_t>& out, uint8_t v);

void putI32(std::vector<uint8_t>& out, int32_t v);

std::vector<uint64_t> packIndices(const std::vector<uint32_t>& indices, int bits);

std::vector<uint8_t> buildChunkNbt(const Config& cfg, const Blocks& blocks, int cx, int cz,
                                   const std::vector<uint16_t>& blockIds,
                                   const std::vector<uint16_t>& biomeIds);
