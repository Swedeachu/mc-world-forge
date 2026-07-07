#include "internal/chunk_passes.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"
#include "internal/chunk_common.hpp"

namespace mcwf::chunk {

void carveFiniteRavinesNative(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                              int cx, int cz, const bool inside[16][16], const bool onWall[16][16],
                              const std::array<std::array<TerrainSample, 16>, 16>& samples,
                              const int heights[16][16], int floorY) {
    if (!cfg.ravines) {
        return;
    }
    int wx0 = cx * 16;
    int wx1 = wx0 + 15;
    int wz0 = cz * 16;
    int wz1 = wz0 + 15;
    int spacing = 330;
    int reach = 260;
    int cellX0 = floorDiv(wx0 - reach, spacing) - 1;
    int cellX1 = floorDiv(wx1 + reach, spacing) + 1;
    int cellZ0 = floorDiv(wz0 - reach, spacing) - 1;
    int cellZ1 = floorDiv(wz1 + reach, spacing) + 1;

    for (int cellZ = cellZ0; cellZ <= cellZ1; ++cellZ) {
        for (int cellX = cellX0; cellX <= cellX1; ++cellX) {
            if (cellRand01(cellX, cellZ, cfg, 8001) > 0.070) {
                continue;
            }
            double ox = cellX * spacing + cellRandRange(cellX, cellZ, cfg, 8002, 45.0, spacing - 45.0);
            double oz = cellZ * spacing + cellRandRange(cellX, cellZ, cfg, 8003, 45.0, spacing - 45.0);
            double angle = cellRandRange(cellX, cellZ, cfg, 8004, 0.0, PI * 2.0);
            double length = cellRandRange(cellX, cellZ, cfg, 8005, 82.0, 185.0);
            double width = cellRandRange(cellX, cellZ, cfg, 8006, 3.5, 7.0);
            double depth = cellRandRange(cellX, cellZ, cfg, 8007, 18.0, 44.0);
            double wiggle = cellRandRange(cellX, cellZ, cfg, 8008, 2.0, 11.0);
            double phase = cellRandRange(cellX, cellZ, cfg, 8009, 0.0, PI * 2.0);

            double dx = std::cos(angle);
            double dz = std::sin(angle);
            double px = -dz;
            double pz = dx;
            double half = length * 0.5;
            double minX = ox - std::abs(dx) * half - width - wiggle - 4.0;
            double maxX = ox + std::abs(dx) * half + width + wiggle + 4.0;
            double minZ = oz - std::abs(dz) * half - width - wiggle - 4.0;
            double maxZ = oz + std::abs(dz) * half + width + wiggle + 4.0;
            if (wx1 < minX || wx0 > maxX || wz1 < minZ || wz0 > maxZ) {
                continue;
            }

            bool lavaBottom = cellRand01(cellX, cellZ, cfg, 8010) < 0.38;

            for (int lz = 0; lz < 16; ++lz) {
                for (int lx = 0; lx < 16; ++lx) {
                    if (!inside[lz][lx] || onWall[lz][lx]) {
                        continue;
                    }
                    const TerrainSample& s = samples[static_cast<size_t>(lz)][static_cast<size_t>(lx)];
                    if (s.river || s.lake || s.beach || s.waterSurface > s.height || s.height <= cfg.waterLevel + 7) {
                        continue;
                    }
                    double rx = static_cast<double>(cx * 16 + lx) - ox;
                    double rz = static_cast<double>(cz * 16 + lz) - oz;
                    double along = rx * dx + rz * dz;
                    double t = clampDouble((along + half) / std::max(1.0, length), 0.0, 1.0);
                    double taper = std::sin(PI * t);
                    if (taper <= 0.04) {
                        continue;
                    }
                    double lateralCenter = std::sin(t * PI * 1.65 + phase) * wiggle * taper;
                    double lateral = rx * px + rz * pz - lateralCenter;
                    double dist = std::abs(lateral);
                    double radius = width * (0.18 + 0.82 * std::sqrt(clampDouble(taper, 0.0, 1.0)));
                    if (dist >= radius) {
                        continue;
                    }
                    int bottom = std::max(floorY + 4, static_cast<int>(heights[lz][lx] - depth * (0.68 + 0.32 * taper)));
                    double height = std::max(1.0, static_cast<double>(heights[lz][lx] - bottom));

                    for (int y = bottom; y <= heights[lz][lx]; ++y) {
                        double relDepth = clampDouble((static_cast<double>(heights[lz][lx] - y)) / height, 0.0, 1.0);
                        double sideRadius = radius * (1.08 - 0.70 * relDepth);
                        sideRadius = std::max(sideRadius, radius * 0.22);
                        if (dist <= sideRadius) {
                            carveBlockAir(a, lx, y, lz, ids, blocks);
                        }
                    }
                    if (lavaBottom && dist < radius * 0.25 && bottom <= floorY + 9) {
                        for (int y = bottom; y <= std::min(bottom + 1, heights[lz][lx]); ++y) {
                            if (getBlock(a, lx, y, lz) == ids.air) {
                                setBlock(a, lx, y, lz, ids.lava);
                            }
                        }
                    }
                }
            }
        }
    }
}

}  // namespace mcwf::chunk
