#pragma once

#include "mcwf/config.hpp"
#include "internal/terrain_grid.hpp"

namespace mcwf::terrain {

struct RawTerrain {
    double h = 0.0;
    double mountain = 0.0;
};

// Single-column smoothed terrain height + mountain factor.
RawTerrain terrainHeight(const Config& cfg, double x, double z);

// Chunk-sized (w x h) smoothed height + mountain grids starting at (x0, z0).
void terrainHeightGrid(const Config& cfg, int x0, int z0, int w, int h, GridD& outH, GridD& outM);

// Cached surface height probe used by the finite water/lava feature planners.
double fastSurfaceHeight(const Config& cfg, double x, double z);

}  // namespace mcwf::terrain
