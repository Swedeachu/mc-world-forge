#include "internal/terrain_climate.hpp"

#include <algorithm>
#include <cmath>

#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"

namespace mcwf::terrain {

namespace {

double temperatureHeight(const Config& cfg, double h) {
    return clampDouble((h - static_cast<double>(cfg.waterLevel)) / 640.0, 0.0, 0.16);
}

}  // namespace

Climate climateFields(const Config& cfg, double x, double z, double h) {
    double sizeScale = std::max(650.0, std::min(1450.0, cfg.size * 0.92));
    double warpScale = std::max(520.0, sizeScale * 0.78);

    double warpX = (fbm2d(x + 6111.0, z - 4222.0, cfg.seed + 33, 4, 1.0 / warpScale) - 0.5) * 330.0;
    double warpZ = (fbm2d(x - 1777.0, z + 9333.0, cfg.seed + 34, 4, 1.0 / warpScale) - 0.5) * 330.0;
    warpX += (fbm2d(x + 313.0, z + 991.0, cfg.seed + 35, 2, 1.0 / 185.0) - 0.5) * 72.0;
    warpZ += (fbm2d(x - 919.0, z - 707.0, cfg.seed + 36, 2, 1.0 / 185.0) - 0.5) * 72.0;
    double cx = x + warpX;
    double cz = z + warpZ;

    double phaseA = cellRandRange(0, 0, cfg, 41001, 0.0, PI * 2.0);
    double phaseB = cellRandRange(0, 0, cfg, 41002, 0.0, PI * 2.0);
    double angle = cellRandRange(0, 0, cfg, 41003, 0.0, PI * 2.0);
    double ca = std::cos(angle);
    double sa = std::sin(angle);
    double rx = cx * ca - cz * sa;
    double rz = cx * sa + cz * ca;

    double waveTemp = std::sin(rx / sizeScale * PI * 2.15 + phaseA) * 0.28 +
                      std::sin((rx * 0.55 + rz * 0.78) / sizeScale * PI * 1.72 + phaseB) * 0.18;
    double tw = fbm2d(cx + 1000.0, cz - 1000.0, cfg.seed + 3, 5, 1.0 / (sizeScale * 0.92));
    double tempDetail = (fbm2d(cx + 333.0, cz - 777.0, cfg.seed + 31, 3, 1.0 / 260.0) - 0.5) * 0.085;
    double edgeWobble = fbm2d(x + 7077.0, z - 1212.0, cfg.seed + 74, 3, 1.0 / 135.0) - 0.5;
    double temp = 0.5 + waveTemp + (tw - 0.5) * 0.70 + tempDetail + edgeWobble * 0.025;
    temp -= temperatureHeight(cfg, h);

    double mw = fbm2d(cx - 2000.0, cz + 2000.0, cfg.seed + 4, 5, 1.0 / (sizeScale * 0.82));
    double humWave = std::sin((rz * 0.95 - rx * 0.28) / sizeScale * PI * 2.05 + phaseB * 0.7) * 0.16;
    double humDetail = (fbm2d(cx - 444.0, cz + 666.0, cfg.seed + 41, 3, 1.0 / 245.0) - 0.5) * 0.105;
    double hum = 0.5 + (mw - 0.5) * 0.96 + humWave + humDetail - edgeWobble * 0.026;
    double selector = fbm2d(cx + 8811.0, cz - 4417.0, cfg.seed + 73, 3, 1.0 / 255.0);

    return {clampDouble(temp, 0.0, 1.0), clampDouble(hum, 0.0, 1.0), selector, edgeWobble};
}

void smoothClimateGrid(GridD& temp, GridD& hum, GridD& selector, GridD& edge) {
    if (temp.w < 3 || temp.h < 3) {
        return;
    }

    for (int i = 0; i < 4; ++i) {
        GridD tb = blurGrid(temp);
        GridD hb = blurGrid(hum);
        GridD sb = blurGrid(selector);
        GridD eb = blurGrid(edge);

        for (int z = 0; z < temp.h; ++z) {
            for (int x = 0; x < temp.w; ++x) {
                temp.at(z, x) = clampDouble(temp.at(z, x) * 0.55 + tb.at(z, x) * 0.45, 0.0, 1.0);
                hum.at(z, x) = clampDouble(hum.at(z, x) * 0.62 + hb.at(z, x) * 0.38, 0.0, 1.0);
                selector.at(z, x) = selector.at(z, x) * 0.70 + sb.at(z, x) * 0.30;
                edge.at(z, x) = edge.at(z, x) * 0.45 + eb.at(z, x) * 0.55;
            }
        }
    }
}

}  // namespace mcwf::terrain
