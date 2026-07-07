#pragma once

#include <cstdint>

#include "mcwf/config.hpp"

// Lattice value noise, fBm, and the position/cell hashing primitives shared by
// terrain, climate, cave and feature generation.

uint32_t mix32(uint32_t n);
uint32_t hashInts(int64_t x, int64_t y, int64_t z, int seed, uint32_t salt);
double hash01(int64_t x, int64_t y, int64_t z, int seed, uint32_t salt = 0);
double smooth(double t);
double lerp(double a, double b, double t);
double value2d(double x, double z, int seed, uint32_t salt = 0);
double value3d(double x, double y, double z, int seed, uint32_t salt = 0);
double fbm2d(double x, double z, int seed, int octaves, double freq, double gain = 0.5);
double fbm3d(double x, double y, double z, int seed, int octaves, double freq, double gain = 0.5);
double rand01(int x, int y, int z, const Config& cfg, uint32_t salt);
double smoothstep01(double t);

// Cell/position hashing used by 2D feature planners (rivers, lakes, lava pools,
// caves, ravines). These share one implementation so the terrain and cave
// planners cannot drift apart.
uint32_t cellHash(int64_t x, int64_t z, int seed, uint32_t salt = 0);
double cellRand01(int64_t x, int64_t z, const Config& cfg, uint32_t salt = 0);
double cellRandRange(int64_t x, int64_t z, const Config& cfg, uint32_t salt, double lo, double hi);
