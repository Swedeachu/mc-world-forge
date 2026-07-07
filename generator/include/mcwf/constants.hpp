#pragma once

#include <cstdint>

// Shared numeric constants for the world generator. 

// World vertical layout (matches the target Minecraft version).
constexpr int MIN_Y = -64;
constexpr int WORLD_HEIGHT = 384;
constexpr int MIN_SECTION = MIN_Y / 16;
constexpr int N_SECTIONS = WORLD_HEIGHT / 16;

// Nether box placement.
constexpr int NETHER_BOX_SIZE = 1000;
constexpr int NETHER_BOX_GAP = 500;
constexpr int NETHER_BOX_CEILING_Y = 127;

// Region file sector size (Anvil format).
constexpr int SECTOR_BYTES = 4096;

// Math.
constexpr double PI = 3.14159265358979323846264338327950288;

// Reciprocal of 2^32, used to map a 32-bit hash to a double in [0, 1).
constexpr double INV_U32 = 1.0 / 4294967296.0;

// Multiplier constants for the cell/position hashing used throughout terrain,
// climate, cave and feature generation. Kept identical to the original inline
// literals so hashing (and therefore world output) is unchanged.
constexpr uint32_t HASH_MUL_X = 0x632be59bu;
constexpr uint32_t HASH_MUL_Z = 0x85157af5u;
constexpr uint32_t HASH_MUL_SEED = 0x9e3779b9u;
constexpr uint32_t HASH_MUL_SALT = 0x165667b1u;
constexpr uint32_t HASH_MUL_Y = 0x27d4eb2du;
