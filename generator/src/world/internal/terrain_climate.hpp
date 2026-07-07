#pragma once

#include "mcwf/config.hpp"
#include "internal/terrain_grid.hpp"

namespace mcwf::terrain {

struct Climate {
    double temp = 0.5;
    double hum = 0.5;
    double selector = 0.5;
    double edge = 0.0;
};

Climate climateFields(const Config& cfg, double x, double z, double h);

void smoothClimateGrid(GridD& temp, GridD& hum, GridD& selector, GridD& edge);

}  // namespace mcwf::terrain
