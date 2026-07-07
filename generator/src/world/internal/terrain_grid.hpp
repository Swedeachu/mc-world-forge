#pragma once

#include <vector>

#include "mcwf/util.hpp"

// Dense 2D scalar grid plus the blur / relaxation / slope-limit kernels shared
// by terrain height, climate smoothing and biome post-processing.

namespace mcwf::terrain {

struct GridD {
    int w = 0;
    int h = 0;
    std::vector<double> v;

    GridD() = default;
    GridD(int width, int height, double fill = 0.0) : w(width), h(height), v(static_cast<size_t>(width * height), fill) {}

    double& at(int z, int x) {
        return v[static_cast<size_t>(z * w + x)];
    }

    double at(int z, int x) const {
        return v[static_cast<size_t>(z * w + x)];
    }

    double clamped(int z, int x) const {
        z = clampInt(z, 0, h - 1);
        x = clampInt(x, 0, w - 1);
        return at(z, x);
    }
};

GridD blurGrid(const GridD& g);

GridD relaxGrid(const GridD& in, double maxStep, int passes);

GridD softMaskGrid(const GridD& mask, int passes);

GridD smoothFinalHeightGrid(const GridD& h);

GridD limitMountainSlopeGrid(const GridD& h, const GridD& mountain, int passes);

}  // namespace mcwf::terrain
