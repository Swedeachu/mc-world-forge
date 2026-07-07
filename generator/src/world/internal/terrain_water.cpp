#include "internal/terrain_water.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <utility>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"
#include "internal/terrain_height.hpp"

namespace mcwf::terrain {

namespace {

struct RiverNodes {
    std::vector<std::pair<double, double>> nodes;
};

RiverNodes buildRiverNodes(const Config& cfg, int cellX, int cellZ, double spacing) {
    double margin = std::max(18.0, spacing * 0.12);
    double ox = cellX * spacing + cellRandRange(cellX, cellZ, cfg, 5102, margin, spacing - margin);
    double oz = cellZ * spacing + cellRandRange(cellX, cellZ, cfg, 5103, margin, spacing - margin);
    double angle = cellRandRange(cellX, cellZ, cfg, 5104, 0.0, PI * 2.0);
    double bias = cellRandRange(cellX, cellZ, cfg, 5105, -0.105, 0.105);
    int segs = 2 + static_cast<int>(cellRand01(cellX, cellZ, cfg, 5106) * 3.0);
    RiverNodes out;
    out.nodes.push_back({ox, oz});
    double x = ox;
    double z = oz;

    for (int i = 1; i <= segs; ++i) {
        double turn = cellRandRange(cellX, cellZ, cfg, 5110 + i, -0.52, 0.52);
        turn += std::sin(i * 1.81 + cellRandRange(cellX, cellZ, cfg, 5111, 0.0, PI * 2.0)) * 0.12;
        angle += turn + bias;
        double step = cellRandRange(cellX, cellZ, cfg, 5120 + i, 16.0, 34.0);
        if (i == segs) {
            step *= cellRandRange(cellX, cellZ, cfg, 5125 + i, 0.55, 0.88);
        }
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        out.nodes.push_back({x, z});
    }

    return out;
}

double segmentDistance(double x, double z, double ax, double az, double bx, double bz) {
    double vx = bx - ax;
    double vz = bz - az;
    double length2 = vx * vx + vz * vz;
    if (length2 <= 0.001) {
        double dx = x - ax;
        double dz = z - az;

        return std::sqrt(dx * dx + dz * dz);
    }
    double t = ((x - ax) * vx + (z - az) * vz) / length2;
    t = clampDouble(t, 0.0, 1.0);
    double px = ax + vx * t;
    double pz = az + vz * t;
    double dx = x - px;
    double dz = z - pz;

    return std::sqrt(dx * dx + dz * dz);
}

}  // namespace

FeatureField finiteRiverField(const Config& cfg, double x, double z, double mountain, double temp, double hum) {
    if (!cfg.rivers || cfg.riverAmount <= 0.0) {
        return {0.0, 0.0, cfg.waterLevel};
    }
    double amount = clampDouble(cfg.riverAmount, 0.0, 1.0);
    int spacing = static_cast<int>(std::round(245.0 - amount * 55.0));
    int reach = 118;
    int cx0 = static_cast<int>(std::floor((x - reach) / spacing)) - 1;
    int cx1 = static_cast<int>(std::floor((x + reach) / spacing)) + 1;
    int cz0 = static_cast<int>(std::floor((z - reach) / spacing)) - 1;
    int cz1 = static_cast<int>(std::floor((z + reach) / spacing)) + 1;
    double core = 0.0;
    double valley = 0.0;
    int level = cfg.waterLevel;
    double levelStrength = 0.0;

    for (int cellZ = cz0; cellZ <= cz1; ++cellZ) {
        for (int cellX = cx0; cellX <= cx1; ++cellX) {
            double active = 0.28 + amount * 0.28;
            if (cellRand01(cellX, cellZ, cfg, 5160) > active) {
                continue;
            }
            RiverNodes nodes = buildRiverNodes(cfg, cellX, cellZ, static_cast<double>(spacing));
            double sy = fastSurfaceHeight(cfg, nodes.nodes.front().first, nodes.nodes.front().second);
            int bodyLevel = static_cast<int>(std::round(sy - cellRandRange(cellX, cellZ, cfg, 5161, 0.15, 0.85)));
            bodyLevel = clampInt(bodyLevel, cfg.floorY + 8, MIN_Y + WORLD_HEIGHT - 42);
            double widthBase = cellRandRange(cellX, cellZ, cfg, 5162, 4.8, 7.4) * (0.88 + amount * 0.24);

            for (size_t i = 0; i + 1 < nodes.nodes.size(); ++i) {
                double ax = nodes.nodes[i].first;
                double az = nodes.nodes[i].second;
                double bx = nodes.nodes[i + 1].first;
                double bz = nodes.nodes[i + 1].second;
                double d = segmentDistance(x, z, ax, az, bx, bz);
                double width = widthBase * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(5170 + i), 0.82, 1.22);
                double bank = width + cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(5180 + i), 13.0, 22.0);
                double c = smoothstep01((width - d) / std::max(width, 0.001));
                double v = smoothstep01((bank - d) / std::max(bank, 0.001));
                double wobble = 0.82 + 0.36 * fbm2d(x + cellX * 31.0 + i * 17.0, z - cellZ * 29.0 - i * 19.0, cfg.seed + 5190, 2, 1.0 / 38.0);
                c = clampDouble(c * wobble, 0.0, 1.0);
                v = clampDouble(v * (0.88 + 0.20 * wobble), 0.0, 1.0);
                if (c > levelStrength) {
                    level = bodyLevel;
                    levelStrength = c;
                }
                core = std::max(core, c);
                valley = std::max(valley, v);
            }
        }
    }
    if (temp < cfg.biomeFrozenCut + 0.02) {
        core *= 0.40;
        valley *= 0.70;
    }
    if (hum < 0.18) {
        core *= 0.70;
        valley *= 0.80;
    }
    if (mountain > 0.64) {
        core *= 0.24;
        valley *= 0.46;
    }

    return {clampDouble(core, 0.0, 1.0), clampDouble(valley, 0.0, 1.0), level};
}

FeatureField finiteLakeField(const Config& cfg, double x, double z, double mountain, double temp, double hum) {
    if (!cfg.lakes || cfg.lakeAmount <= 0.0) {
        return {0.0, 0.0, cfg.waterLevel};
    }
    double amount = clampDouble(cfg.lakeAmount, 0.0, 1.0);
    int spacing = static_cast<int>(std::round(235.0 - amount * 48.0));
    int reach = 72;
    int cx0 = static_cast<int>(std::floor((x - reach) / spacing)) - 1;
    int cx1 = static_cast<int>(std::floor((x + reach) / spacing)) + 1;
    int cz0 = static_cast<int>(std::floor((z - reach) / spacing)) - 1;
    int cz1 = static_cast<int>(std::floor((z + reach) / spacing)) + 1;
    double core = 0.0;
    double bank = 0.0;
    int level = cfg.waterLevel;
    double levelStrength = 0.0;

    for (int cellZ = cz0; cellZ <= cz1; ++cellZ) {
        for (int cellX = cx0; cellX <= cx1; ++cellX) {
            double active = 0.08 + amount * 0.18;
            if (cellRand01(cellX, cellZ, cfg, 5310) > active) {
                continue;
            }
            double ox = cellX * spacing + cellRandRange(cellX, cellZ, cfg, 5311, 24.0, spacing - 24.0);
            double oz = cellZ * spacing + cellRandRange(cellX, cellZ, cfg, 5312, 24.0, spacing - 24.0);
            double sy = fastSurfaceHeight(cfg, ox, oz);
            int bodyLevel = static_cast<int>(std::round(sy - cellRandRange(cellX, cellZ, cfg, 5313, 0.6, 1.2)));
            bodyLevel = clampInt(bodyLevel, cfg.floorY + 8, MIN_Y + WORLD_HEIGHT - 42);
            double rx = cellRandRange(cellX, cellZ, cfg, 5314, 9.0, 22.0) * (0.85 + amount * 0.42);
            double rz = cellRandRange(cellX, cellZ, cfg, 5315, 9.0, 22.0) * (0.85 + amount * 0.42);
            double ang = cellRandRange(cellX, cellZ, cfg, 5316, 0.0, PI * 2.0);
            double ca = std::cos(ang);
            double sa = std::sin(ang);
            double dx = x - ox;
            double dz = z - oz;
            double lx = dx * ca + dz * sa;
            double lz = -dx * sa + dz * ca;
            double d = std::sqrt((lx / std::max(1.0, rx)) * (lx / std::max(1.0, rx)) +
                                 (lz / std::max(1.0, rz)) * (lz / std::max(1.0, rz)));
            double c = smoothstep01((1.0 - d) / 0.22);
            double b = smoothstep01((1.80 - d) / 0.72);
            if (c > levelStrength) {
                level = bodyLevel;
                levelStrength = c;
            }
            core = std::max(core, c);
            bank = std::max(bank, b);
        }
    }
    if (temp < cfg.biomeFrozenCut + 0.02) {
        core *= 0.34;
        bank *= 0.70;
    }
    if (hum < 0.18) {
        core *= 0.80;
        bank *= 0.86;
    }
    if (mountain > 0.46) {
        core *= 0.16;
        bank *= 0.38;
    }

    return {clampDouble(core, 0.0, 1.0), clampDouble(bank, 0.0, 1.0), level};
}

FeatureField lavaPoolField(const Config& cfg, double x, double z, double mountain, double temp, double hum) {
    if (!cfg.surfaceLavaPools || !cfg.fluids) {
        return {0.0, 0.0, cfg.waterLevel};
    }
    int spacing = 285;
    int reach = 44;
    int cx0 = static_cast<int>(std::floor((x - reach) / spacing)) - 1;
    int cx1 = static_cast<int>(std::floor((x + reach) / spacing)) + 1;
    int cz0 = static_cast<int>(std::floor((z - reach) / spacing)) - 1;
    int cz1 = static_cast<int>(std::floor((z + reach) / spacing)) + 1;
    double core = 0.0;
    double bank = 0.0;
    int level = cfg.waterLevel;
    double levelStrength = 0.0;

    for (int cellZ = cz0; cellZ <= cz1; ++cellZ) {
        for (int cellX = cx0; cellX <= cx1; ++cellX) {
            if (cellRand01(cellX, cellZ, cfg, 5440) > 0.155) {
                continue;
            }
            double ox = cellX * spacing + cellRandRange(cellX, cellZ, cfg, 5441, 28.0, spacing - 28.0);
            double oz = cellZ * spacing + cellRandRange(cellX, cellZ, cfg, 5442, 28.0, spacing - 28.0);
            double sy = fastSurfaceHeight(cfg, ox, oz);
            int bodyLevel = static_cast<int>(std::round(sy - cellRandRange(cellX, cellZ, cfg, 5443, 0.20, 0.70)));
            bodyLevel = clampInt(bodyLevel, cfg.floorY + 10, MIN_Y + WORLD_HEIGHT - 50);
            double rx = cellRandRange(cellX, cellZ, cfg, 5444, 3.5, 6.5);
            double rz = cellRandRange(cellX, cellZ, cfg, 5445, 3.5, 6.5);
            double ang = cellRandRange(cellX, cellZ, cfg, 5446, 0.0, PI * 2.0);
            double ca = std::cos(ang);
            double sa = std::sin(ang);
            double dx = x - ox;
            double dz = z - oz;
            double lx = dx * ca + dz * sa;
            double lz = -dx * sa + dz * ca;
            double d = std::sqrt((lx / std::max(1.0, rx)) * (lx / std::max(1.0, rx)) +
                                 (lz / std::max(1.0, rz)) * (lz / std::max(1.0, rz)));
            double c = smoothstep01((1.0 - d) / 0.24);
            double b = smoothstep01((1.55 - d) / 0.55);
            if (c > levelStrength) {
                level = bodyLevel;
                levelStrength = c;
            }
            core = std::max(core, c);
            bank = std::max(bank, b);
        }
    }
    if (temp >= cfg.biomeWarmCut - 0.04) {
        core *= 1.0;
        bank *= 1.0;
    } else {
        core *= 0.52;
        bank *= 0.62;
    }
    if (hum > 0.72) {
        core *= 0.50;
        bank *= 0.56;
    }
    if (mountain > 0.62) {
        core *= 0.70;
        bank *= 0.82;
    }

    return {clampDouble(core, 0.0, 1.0), clampDouble(bank, 0.0, 1.0), level};
}

}  // namespace mcwf::terrain
