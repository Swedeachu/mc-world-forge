#pragma once

#include "mcwf/config.hpp"

namespace mcwf::terrain {

// Result of a finite water/lava body probe at one column: how strongly the
// column is inside the body core, how strongly it is in the surrounding bank,
// and the chosen fluid surface level.
struct FeatureField {
    double core = 0.0;
    double bank = 0.0;
    int level = 0;
};

FeatureField finiteRiverField(const Config& cfg, double x, double z, double mountain, double temp, double hum);

FeatureField finiteLakeField(const Config& cfg, double x, double z, double mountain, double temp, double hum);

FeatureField lavaPoolField(const Config& cfg, double x, double z, double mountain, double temp, double hum);

}  // namespace mcwf::terrain
