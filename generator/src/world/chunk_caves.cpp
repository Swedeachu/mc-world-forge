#include "internal/chunk_passes.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/terrain.hpp"
#include "mcwf/util.hpp"
#include "internal/chunk_common.hpp"

namespace mcwf::chunk {

namespace {

struct CaveNode {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

int caveTargetY(const Config& cfg, int floorY) {
    if (cfg.deepDark && floorY <= MIN_Y + 1) {
        return floorY + 22;
    }
    int targetY = -12;

    for (const OreCfg& ore : cfg.ores) {
        if (ore.name.find("diamond") != std::string::npos) {
            targetY = std::min(ore.yMax - 2, ore.yMin + 4);
            break;
        }
    }

    return std::max(floorY + 5, std::min(8, targetY));
}

void carveRoomNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                     int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                     const CaveNode& c, double rx, double ry, double rz) {
    rx = std::max(2.10, rx);
    ry = std::max(1.95, ry);
    rz = std::max(2.10, rz);
    double pad = std::max(rx, rz) + 2.0;
    int minX = std::max(0, static_cast<int>(std::floor(c.x - pad)) - cx * 16);
    int maxX = std::min(15, static_cast<int>(std::ceil(c.x + pad)) - cx * 16);
    int minZ = std::max(0, static_cast<int>(std::floor(c.z - pad)) - cz * 16);
    int maxZ = std::min(15, static_cast<int>(std::ceil(c.z + pad)) - cz * 16);
    int minY = std::max(floorY + 2, static_cast<int>(std::floor(c.y - ry - 2.0)));
    int maxY = std::min(MIN_Y + WORLD_HEIGHT - 2, static_cast<int>(std::ceil(c.y + ry + 2.0)));
    if (maxX < minX || maxZ < minZ || maxY <= minY) {
        return;
    }

    for (int lz = minZ; lz <= maxZ; ++lz) {
        for (int lx = minX; lx <= maxX; ++lx) {
            if (!inside[lz][lx]) {
                continue;
            }
            double wx = static_cast<double>(cx * 16 + lx);
            double wz = static_cast<double>(cz * 16 + lz);

            for (int y = minY; y <= std::min(maxY, heights[lz][lx] + 1); ++y) {
                double d = ((wx - c.x) / rx) * ((wx - c.x) / rx) +
                           ((static_cast<double>(y) - c.y) / ry) * ((static_cast<double>(y) - c.y) / ry) +
                           ((wz - c.z) / rz) * ((wz - c.z) / rz);
                if (d <= 1.0) {
                    carveBlockAir(a, lx, y, lz, ids, blocks);
                }
            }
        }
    }
}

void carveCapsuleNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                        int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                        const CaveNode& p0, const CaveNode& p1, double radius) {
    radius = std::max(2.20, radius);
    double vx = p1.x - p0.x;
    double vy = p1.y - p0.y;
    double vz = p1.z - p0.z;
    double denom = vx * vx + vy * vy + vz * vz;
    if (denom <= 0.001) {
        return;
    }
    double pad = radius + 2.0;
    int minX = std::max(0, static_cast<int>(std::floor(std::min(p0.x, p1.x) - pad)) - cx * 16);
    int maxX = std::min(15, static_cast<int>(std::ceil(std::max(p0.x, p1.x) + pad)) - cx * 16);
    int minZ = std::max(0, static_cast<int>(std::floor(std::min(p0.z, p1.z) - pad)) - cz * 16);
    int maxZ = std::min(15, static_cast<int>(std::ceil(std::max(p0.z, p1.z) + pad)) - cz * 16);
    int minY = std::max(floorY + 2, static_cast<int>(std::floor(std::min(p0.y, p1.y) - radius - 2.0)));
    int maxY = std::min(MIN_Y + WORLD_HEIGHT - 2, static_cast<int>(std::ceil(std::max(p0.y, p1.y) + radius + 2.0)));
    if (maxX < minX || maxZ < minZ || maxY <= minY) {
        return;
    }
    double rr = radius * radius;

    for (int lz = minZ; lz <= maxZ; ++lz) {
        for (int lx = minX; lx <= maxX; ++lx) {
            if (!inside[lz][lx]) {
                continue;
            }
            double wx = static_cast<double>(cx * 16 + lx);
            double wz = static_cast<double>(cz * 16 + lz);

            for (int y = minY; y <= std::min(maxY, heights[lz][lx] + 1); ++y) {
                double wy = static_cast<double>(y);
                double t = ((wx - p0.x) * vx + (wy - p0.y) * vy + (wz - p0.z) * vz) / denom;
                t = clampDouble(t, 0.0, 1.0);
                double px = p0.x + vx * t;
                double py = p0.y + vy * t;
                double pz = p0.z + vz * t;
                double dist = (wx - px) * (wx - px) + ((wy - py) / 1.78) * ((wy - py) / 1.78) + (wz - pz) * (wz - pz);
                if (dist <= rr) {
                    carveBlockAir(a, lx, y, lz, ids, blocks);
                }
            }
        }
    }
}

uint32_t segmentSeed(uint32_t salt) {
    return static_cast<uint32_t>((static_cast<uint64_t>(salt) * 2654435761ull) & 0x7fffffffull);
}

std::vector<CaveNode> buildCaveSpineNative(const Config& cfg, int cellX, int cellZ, double ox, double oz, double startY, double targetY) {
    int segments = 10 + static_cast<int>(cellRand01(cellX, cellZ, cfg, 7800) * 5.0);
    double angle = cellRandRange(cellX, cellZ, cfg, 7801, 0.0, PI * 2.0);
    double turnBias = cellRandRange(cellX, cellZ, cfg, 7802, -0.18, 0.18);
    double x = ox;
    double z = oz;
    std::vector<CaveNode> nodes;
    nodes.push_back({x, startY, z});

    for (int i = 1; i <= segments; ++i) {
        double t = static_cast<double>(i) / static_cast<double>(segments);
        double turn = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7810 + i), -0.72, 0.72);
        turn += std::sin(i * 1.37 + cellRandRange(cellX, cellZ, cfg, 7820, 0.0, PI * 2.0)) * 0.16;
        angle += turn + turnBias;
        double step = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7830 + i), 16.0, 29.0);
        if (i % 4 == 0) {
            step *= cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7840 + i), 0.55, 0.85);
        }
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        double descent = std::pow(1.0 - t, 1.24);
        double y = targetY + (startY - targetY) * descent;
        y += std::sin(t * PI * 4.0 + cellRandRange(cellX, cellZ, cfg, 7850, 0.0, PI * 2.0)) * (3.6 * (1.0 - t));
        y += cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7860 + i), -2.4, 2.8) * (0.35 + 0.65 * (1.0 - t));
        y = std::max(targetY, std::min(startY, y));
        if (i == segments) {
            y = targetY;
        }
        nodes.push_back({x, y, z});
    }

    return nodes;
}

std::vector<CaveNode> buildCaveBranchNative(const Config& cfg, int cellX, int cellZ, int branch, CaveNode anchor, double targetY) {
    double x = anchor.x;
    double y = anchor.y;
    double z = anchor.z;
    double angle = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7900 + branch), 0.0, PI * 2.0);
    int segs = 3 + static_cast<int>(cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7910 + branch)) * 4.0);
    std::vector<CaveNode> nodes;
    nodes.push_back(anchor);

    for (int i = 1; i <= segs; ++i) {
        angle += cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7920 + branch * 7 + i), -0.95, 0.95);
        double step = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7930 + branch * 7 + i), 12.0, 25.0);
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        y -= cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7940 + branch * 7 + i), -1.5, 5.8);
        y = std::max(targetY + 2.0, y);
        nodes.push_back({x, y, z});
    }

    return nodes;
}

std::vector<CaveNode> buildUndergroundArmNative(const Config& cfg, int cellX, int cellZ, int arm, CaveNode center, int floorY, double targetY) {
    double x = center.x;
    double y = center.y;
    double z = center.z;
    double angle = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8400 + arm), 0.0, PI * 2.0);
    double pitch = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8401 + arm), -0.42, 0.32);
    int segs = 4 + static_cast<int>(cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8402 + arm)) * 6.0);
    std::vector<CaveNode> nodes;
    nodes.push_back(center);

    for (int i = 1; i <= segs; ++i) {
        angle += cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8410 + arm * 17 + i), -1.18, 1.18);
        pitch += cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8420 + arm * 17 + i), -0.30, 0.30);
        pitch = clampDouble(pitch, -0.62, 0.42);
        double step = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8430 + arm * 17 + i), 11.0, 28.0);
        if (cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8435 + arm * 17 + i)) < 0.22) {
            step *= cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8436 + arm * 17 + i), 1.35, 1.95);
        }
        x += std::cos(angle) * step;
        z += std::sin(angle) * step;
        y += std::sin(pitch) * step * 0.55;
        y += cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8440 + arm * 17 + i), -4.5, 4.0);
        y = std::max(static_cast<double>(floorY + 6), std::min(static_cast<double>(cfg.waterLevel + 36), y));
        if (i == segs && cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8450 + arm)) < 0.34) {
            y = std::max(targetY + 1.0, y - cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8451 + arm), 8.0, 24.0));
        }
        nodes.push_back({x, y, z});
    }

    return nodes;
}

void carveLumpyRoomNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                          int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                          CaveNode c, double rx, double ry, double rz, int cellX, int cellZ, uint32_t salt, const Config& cfg) {
    carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, c, rx, ry, rz);
    uint32_t seedSalt = segmentSeed(salt);
    int lumps = 2 + static_cast<int>(cellRand01(cellX, cellZ, cfg, seedSalt + 8700) * 5.0);

    for (int i = 0; i < lumps; ++i) {
        if (cellRand01(cellX, cellZ, cfg, seedSalt + 8710 + i) < 0.18) {
            continue;
        }
        double a0 = cellRandRange(cellX, cellZ, cfg, seedSalt + 8720 + i, 0.0, PI * 2.0);
        double d = cellRandRange(cellX, cellZ, cfg, seedSalt + 8730 + i, 0.25, 0.82);
        CaveNode cc;
        cc.x = c.x + std::cos(a0) * rx * d;
        cc.z = c.z + std::sin(a0) * rz * d;
        cc.y = c.y + cellRandRange(cellX, cellZ, cfg, seedSalt + 8740 + i, -0.45, 0.52) * ry;
        carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, cc,
                        rx * cellRandRange(cellX, cellZ, cfg, seedSalt + 8750 + i, 0.34, 0.72),
                        ry * cellRandRange(cellX, cellZ, cfg, seedSalt + 8760 + i, 0.30, 0.68),
                        rz * cellRandRange(cellX, cellZ, cfg, seedSalt + 8770 + i, 0.34, 0.72));
    }
}

void carveVariedSegmentNative(std::vector<uint16_t>& a, const BlockIds& ids, const Blocks& blocks,
                              int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY,
                              CaveNode p0, CaveNode p1, double radius, int cellX, int cellZ, uint32_t salt, const Config& cfg) {
    radius = std::max(2.20, radius);
    carveCapsuleNative(a, ids, blocks, cx, cz, inside, heights, floorY, p0, p1, radius);
    double vx = p1.x - p0.x;
    double vy = p1.y - p0.y;
    double vz = p1.z - p0.z;
    double length = std::sqrt(vx * vx + vy * vy + vz * vz);
    if (length <= 1.0) {
        return;
    }
    uint32_t seedSalt = segmentSeed(salt);
    int bumps = std::max(1, std::min(7, static_cast<int>(length / 15.0)));

    for (int j = 0; j < bumps; ++j) {
        if (cellRand01(cellX, cellZ, cfg, seedSalt + 8500 + j) > 0.68) {
            continue;
        }
        double t = (static_cast<double>(j) + cellRandRange(cellX, cellZ, cfg, seedSalt + 8510 + j, 0.25, 0.85)) / static_cast<double>(bumps);
        t = clampDouble(t, 0.0, 1.0);
        CaveNode c;
        c.x = p0.x + vx * t;
        c.y = p0.y + vy * t;
        c.z = p0.z + vz * t;
        double sideA = cellRandRange(cellX, cellZ, cfg, seedSalt + 8520 + j, 0.0, PI * 2.0);
        double sideD = radius * cellRandRange(cellX, cellZ, cfg, seedSalt + 8530 + j, 0.15, 1.65);
        c.x += std::cos(sideA) * sideD;
        c.z += std::sin(sideA) * sideD;
        c.y += cellRandRange(cellX, cellZ, cfg, seedSalt + 8540 + j, -1.7, 1.9);
        double rx = radius * cellRandRange(cellX, cellZ, cfg, seedSalt + 8550 + j, 0.70, 1.95);
        double ry = radius * cellRandRange(cellX, cellZ, cfg, seedSalt + 8560 + j, 0.55, 1.38);
        double rz = radius * cellRandRange(cellX, cellZ, cfg, seedSalt + 8570 + j, 0.70, 1.95);
        carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, c, rx, ry, rz);
        if (cellRand01(cellX, cellZ, cfg, seedSalt + 8580 + j) < 0.26) {
            CaveNode end;
            end.x = c.x + std::cos(sideA) * radius * cellRandRange(cellX, cellZ, cfg, seedSalt + 8590 + j, 2.5, 6.5);
            end.y = c.y + cellRandRange(cellX, cellZ, cfg, seedSalt + 8600 + j, -1.5, 2.2);
            end.z = c.z + std::sin(sideA) * radius * cellRandRange(cellX, cellZ, cfg, seedSalt + 8610 + j, 2.5, 6.5);
            carveCapsuleNative(a, ids, blocks, cx, cz, inside, heights, floorY, c, end,
                               radius * cellRandRange(cellX, cellZ, cfg, seedSalt + 8620 + j, 0.38, 0.78));
        }
    }
}

}  // namespace

void carveNativeCaveSystems(std::vector<uint16_t>& a, const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                            int cx, int cz, const bool inside[16][16], const int heights[16][16], int floorY) {
    if (!cfg.caves) {
        return;
    }
    double intensity = std::max(0.2, cfg.caveIntensity);
    int targetY = caveTargetY(cfg, floorY);
    int wx0 = cx * 16;
    int wx1 = wx0 + 15;
    int wz0 = cz * 16;
    int wz1 = wz0 + 15;

    auto iterCells = [&](int spacing, int reach, auto&& fn) {
        int cellX0 = floorDiv(wx0 - reach, spacing) - 1;
        int cellX1 = floorDiv(wx1 + reach, spacing) + 1;
        int cellZ0 = floorDiv(wz0 - reach, spacing) - 1;
        int cellZ1 = floorDiv(wz1 + reach, spacing) + 1;

        for (int cellZ = cellZ0; cellZ <= cellZ1; ++cellZ) {
            for (int cellX = cellX0; cellX <= cellX1; ++cellX) {
                fn(cellX, cellZ);
            }
        }
    };

    iterCells(112, 252, [&](int cellX, int cellZ) {
        if (cellRand01(cellX, cellZ, cfg, 7001) > std::min(0.94, 0.86 * intensity)) {
            return;
        }
        double ox = cellX * 112.0 + cellRandRange(cellX, cellZ, cfg, 7002, 8.0, 104.0);
        double oz = cellZ * 112.0 + cellRandRange(cellX, cellZ, cfg, 7003, 8.0, 104.0);
        int surf = sampleTerrain(cfg, static_cast<int>(std::round(ox)), static_cast<int>(std::round(oz))).height;
        if (surf <= cfg.waterLevel + 8 && cellRand01(cellX, cellZ, cfg, 7004) > 0.22) {
            return;
        }
        double startY = std::min(static_cast<double>(surf - 4), static_cast<double>(cfg.waterLevel + 48));
        startY = std::max(static_cast<double>(targetY + 48), startY);
        if (startY <= targetY + 10) {
            return;
        }
        std::vector<CaveNode> nodes = buildCaveSpineNative(cfg, cellX, cellZ, ox, oz, startY, static_cast<double>(targetY));
        double minX = nodes[0].x, maxX = nodes[0].x, minZ = nodes[0].z, maxZ = nodes[0].z;

        for (const CaveNode& n : nodes) {
            minX = std::min(minX, n.x); maxX = std::max(maxX, n.x);
            minZ = std::min(minZ, n.z); maxZ = std::max(maxZ, n.z);
        }
        if (wx1 < minX - 44.0 || wx0 > maxX + 44.0 || wz1 < minZ - 44.0 || wz0 > maxZ + 44.0) {
            return;
        }
        double baseR = cellRandRange(cellX, cellZ, cfg, 7010, 2.35, 3.75) * (0.98 + 0.20 * intensity);

        for (size_t i = 0; i + 1 < nodes.size(); ++i) {
            double r = baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7020 + static_cast<int>(i)), 0.70, 1.42);
            if (i == 0) r *= 1.42;
            if (i % 4 == 2) r *= 1.22;
            carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], nodes[i + 1], r,
                                     cellX, cellZ, static_cast<uint32_t>(702000 + static_cast<int>(i) * 37), cfg);
        }

        for (size_t i = 0; i < nodes.size(); ++i) {
            bool mandatory = i == 0 || i + 1 == nodes.size() || i % 3 == 1;
            if (!mandatory && cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7100 + static_cast<int>(i))) > 0.52) {
                continue;
            }
            double scale = i == 0 ? 1.32 : (i + 1 == nodes.size() ? 1.45 : (i % 3 == 1 ? 1.20 : 1.0));
            double rx = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7110 + static_cast<int>(i)), 3.8, 8.4) * scale;
            double ry = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7120 + static_cast<int>(i)), 2.6, 5.8) * scale;
            double rz = cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7130 + static_cast<int>(i)), 3.8, 8.4) * scale;
            carveRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], rx, ry, rz);
            if (cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7140 + static_cast<int>(i))) < 0.46) {
                carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], rx, ry, rz,
                                     cellX, cellZ, static_cast<uint32_t>(715000 + static_cast<int>(i) * 29), cfg);
            }
        }
        int branchCount = 5 + static_cast<int>(cellRand01(cellX, cellZ, cfg, 7200) * 7.0 * std::min(1.80, intensity));

        for (int b = 0; b < branchCount && nodes.size() > 4; ++b) {
            int anchorI = 1 + static_cast<int>(cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(7210 + b)) * static_cast<double>(nodes.size() - 3));
            std::vector<CaveNode> branch = buildCaveBranchNative(cfg, cellX, cellZ, b, nodes[static_cast<size_t>(anchorI)], static_cast<double>(targetY));

            for (size_t i = 0; i + 1 < branch.size(); ++i) {
                double br = baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7300 + b * 13 + static_cast<int>(i)), 0.58, 1.18);
                carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, branch[i], branch[i + 1], br,
                                         cellX, cellZ, static_cast<uint32_t>(730000 + b * 101 + static_cast<int>(i) * 17), cfg);
            }
            CaveNode end = branch.back();
            carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, end,
                                 baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7400 + b), 1.25, 2.50),
                                 baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7410 + b), 0.95, 1.85),
                                 baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(7420 + b), 1.25, 2.50),
                                 cellX, cellZ, static_cast<uint32_t>(742000 + b * 43), cfg);
        }
    });

    iterCells(86, 196, [&](int cellX, int cellZ) {
        if (cellRand01(cellX, cellZ, cfg, 8101) > std::min(0.96, 0.88 * intensity)) {
            return;
        }
        double ox = cellX * 86.0 + cellRandRange(cellX, cellZ, cfg, 8102, 5.0, 81.0);
        double oz = cellZ * 86.0 + cellRandRange(cellX, cellZ, cfg, 8103, 5.0, 81.0);
        int surf = sampleTerrain(cfg, static_cast<int>(std::round(ox)), static_cast<int>(std::round(oz))).height;
        double roll = cellRand01(cellX, cellZ, cfg, 8104);
        double lo = static_cast<double>(targetY + 3);
        double hi = std::min(static_cast<double>(cfg.waterLevel - 8), static_cast<double>(targetY + 42));
        if (roll >= 0.58 && roll < 0.86) {
            lo = static_cast<double>(targetY + 18);
            hi = std::min(static_cast<double>(cfg.waterLevel + 18), static_cast<double>(surf - 14));
        } else if (roll >= 0.86) {
            lo = std::max(static_cast<double>(targetY + 8), static_cast<double>(floorY + 10));
            hi = std::min(static_cast<double>(cfg.waterLevel + 38), static_cast<double>(surf - 18));
        }
        if (hi <= lo + 3.0) {
            return;
        }
        CaveNode center{ox, cellRandRange(cellX, cellZ, cfg, 8105, lo, hi), oz};
        double maxExtent = cellRandRange(cellX, cellZ, cfg, 8106, 72.0, 138.0);
        if (wx1 < ox - maxExtent || wx0 > ox + maxExtent || wz1 < oz - maxExtent || wz0 > oz + maxExtent) {
            return;
        }
        double baseR = cellRandRange(cellX, cellZ, cfg, 8110, 1.95, 3.95) * (0.95 + 0.18 * intensity);
        if (cellRand01(cellX, cellZ, cfg, 8111) < 0.22) {
            baseR *= cellRandRange(cellX, cellZ, cfg, 8112, 1.35, 1.85);
        }
        carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, center,
                             baseR * cellRandRange(cellX, cellZ, cfg, 8120, 1.7, 3.4),
                             baseR * cellRandRange(cellX, cellZ, cfg, 8121, 1.0, 2.0),
                             baseR * cellRandRange(cellX, cellZ, cfg, 8122, 1.7, 3.4),
                             cellX, cellZ, 812000, cfg);
        int arms = 4 + static_cast<int>(cellRand01(cellX, cellZ, cfg, 8130) * 6.0);

        for (int arm = 0; arm < arms; ++arm) {
            std::vector<CaveNode> nodes = buildUndergroundArmNative(cfg, cellX, cellZ, arm, center, floorY, static_cast<double>(targetY));

            for (size_t i = 0; i + 1 < nodes.size(); ++i) {
                double rr = baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8140 + arm * 19 + static_cast<int>(i)), 0.52, 1.38);
                if (i == 0) rr *= 1.15;
                carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, nodes[i], nodes[i + 1], rr,
                                         cellX, cellZ, static_cast<uint32_t>(814000 + arm * 211 + static_cast<int>(i) * 31), cfg);
                if (cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8150 + arm * 19 + static_cast<int>(i))) < 0.42) {
                    CaveNode n = nodes[i + 1];
                    carveLumpyRoomNative(a, ids, blocks, cx, cz, inside, heights, floorY, n,
                                         baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8160 + arm * 19 + static_cast<int>(i)), 1.1, 2.7),
                                         baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8170 + arm * 19 + static_cast<int>(i)), 0.7, 1.7),
                                         baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8180 + arm * 19 + static_cast<int>(i)), 1.1, 2.7),
                                         cellX, cellZ, static_cast<uint32_t>(819000 + arm * 211 + static_cast<int>(i) * 31), cfg);
                }
            }
            if (nodes.size() >= 3 && cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8200 + arm)) < 0.62) {
                int anchor = 1 + static_cast<int>(cellRand01(cellX, cellZ, cfg, static_cast<uint32_t>(8201 + arm)) * static_cast<double>(nodes.size() - 2));
                std::vector<CaveNode> side = buildCaveBranchNative(cfg, cellX, cellZ, 100 + arm, nodes[static_cast<size_t>(anchor)], static_cast<double>(targetY));

                for (size_t i = 0; i + 1 < side.size(); ++i) {
                    carveVariedSegmentNative(a, ids, blocks, cx, cz, inside, heights, floorY, side[i], side[i + 1],
                                             baseR * cellRandRange(cellX, cellZ, cfg, static_cast<uint32_t>(8210 + arm * 7 + static_cast<int>(i)), 0.48, 1.05),
                                             cellX, cellZ, static_cast<uint32_t>(821000 + arm * 101 + static_cast<int>(i) * 23), cfg);
                }
            }
        }
    });
}

}  // namespace mcwf::chunk
