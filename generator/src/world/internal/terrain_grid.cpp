#include "internal/terrain_grid.hpp"

#include <algorithm>
#include <array>

#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"

namespace mcwf::terrain {

GridD blurGrid(const GridD& g) {
    GridD out(g.w, g.h);

    for (int z = 0; z < g.h; ++z) {
        for (int x = 0; x < g.w; ++x) {
            out.at(z, x) =
                (g.clamped(z, x) * 0.28 +
                 (g.clamped(z - 1, x) + g.clamped(z + 1, x) + g.clamped(z, x - 1) + g.clamped(z, x + 1)) * 0.13 +
                 (g.clamped(z - 1, x - 1) + g.clamped(z - 1, x + 1) + g.clamped(z + 1, x - 1) + g.clamped(z + 1, x + 1)) * 0.045 +
                 (g.clamped(z - 2, x) + g.clamped(z + 2, x) + g.clamped(z, x - 2) + g.clamped(z, x + 2)) * 0.025) / 1.08;
        }
    }

    return out;
}

GridD relaxGrid(const GridD& in, double maxStep, int passes) {
    GridD out = in;

    for (int pass = 0; pass < std::max(1, passes); ++pass) {
        GridD next(out.w, out.h);

        for (int z = 0; z < out.h; ++z) {
            for (int x = 0; x < out.w; ++x) {
                std::array<double, 8> n = {
                    out.clamped(z - 1, x), out.clamped(z + 1, x), out.clamped(z, x - 1), out.clamped(z, x + 1),
                    out.clamped(z - 1, x - 1), out.clamped(z - 1, x + 1), out.clamped(z + 1, x - 1), out.clamped(z + 1, x + 1)
                };
                std::sort(n.begin(), n.end());
                double med = (n[3] + n[4]) * 0.5;
                double avg = (out.clamped(z - 1, x) + out.clamped(z + 1, x) + out.clamped(z, x - 1) + out.clamped(z, x + 1)) * 0.25;
                double v = out.at(z, x);
                v = std::min(v, med + maxStep);
                v = std::max(v, med - maxStep);
                next.at(z, x) = v * 0.82 + avg * 0.18;
            }
        }
        out = std::move(next);
    }

    return out;
}

GridD softMaskGrid(const GridD& mask, int passes) {
    GridD out = mask;

    for (int i = 0; i < std::max(1, passes); ++i) {
        out = blurGrid(out);
    }

    for (double& v : out.v) {
        v = clampDouble(v, 0.0, 1.0);
    }

    return out;
}

GridD smoothFinalHeightGrid(const GridD& h) {
    if (h.w < 3 || h.h < 3) {
        return h;
    }
    GridD b1 = blurGrid(h);
    GridD b2 = blurGrid(b1);
    GridD smoothed(h.w, h.h);

    for (int z = 0; z < h.h; ++z) {
        for (int x = 0; x < h.w; ++x) {
            smoothed.at(z, x) = h.at(z, x) * 0.78 + b1.at(z, x) * 0.16 + b2.at(z, x) * 0.06;
        }
    }

    return relaxGrid(smoothed, 8.0, 1);
}

GridD limitMountainSlopeGrid(const GridD& h, const GridD& mountain, int passes) {
    GridD out = h;

    for (int pass = 0; pass < std::max(1, passes); ++pass) {
        GridD next = out;

        for (int z = 0; z < out.h; ++z) {
            for (int x = 0; x < out.w; ++x) {
                std::array<double, 8> n = {
                    out.clamped(z - 1, x), out.clamped(z + 1, x), out.clamped(z, x - 1), out.clamped(z, x + 1),
                    out.clamped(z - 1, x - 1), out.clamped(z - 1, x + 1), out.clamped(z + 1, x - 1), out.clamped(z + 1, x + 1)
                };
                std::sort(n.begin(), n.end());
                double med = (n[3] + n[4]) * 0.5;
                double avg = (out.clamped(z - 1, x) + out.clamped(z + 1, x) + out.clamped(z, x - 1) + out.clamped(z, x + 1)) * 0.25;
                double m = smoothstep01((mountain.clamped(z, x) - 0.18) / 0.46);
                double maxStep = 7.75 - m * 3.05;
                double v = out.at(z, x);
                v = std::min(v, med + maxStep);
                v = std::max(v, med - maxStep * 1.18);
                double limited = v * (0.86 - 0.10 * m) + avg * (0.14 + 0.10 * m);
                next.at(z, x) = out.at(z, x) * (1.0 - m * 0.72) + limited * (m * 0.72);
            }
        }
        out = std::move(next);
    }

    return out;
}

}  // namespace mcwf::terrain
