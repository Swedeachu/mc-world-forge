#pragma once

#include <vector>

#include "mcwf/config.hpp"
#include "internal/terrain_climate.hpp"
#include "internal/terrain_grid.hpp"

namespace mcwf::terrain {

struct BiomeChoice {
    int biome = 0;  // BIOME_PLAINS
    double tempBand = 0.5;
    double humBand = 0.5;
    double badlands = 0.0;
};

BiomeChoice classifyBiome(const Config& cfg, double x, double z, double h, double mnt, const Climate& c);

void cleanBiomeGrid(std::vector<int>& biome, int w, int h);

void applyBiomeGridSafety(const Config& cfg, int x0, int z0, GridD& hf, const GridD& mnt,
                          GridD& tempBand, GridD& humBand, GridD& bad, std::vector<int>& biome, int w, int h);

}  // namespace mcwf::terrain
