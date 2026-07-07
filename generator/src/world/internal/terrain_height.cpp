#include "internal/terrain_height.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <unordered_map>

#include "mcwf/constants.hpp"
#include "mcwf/noise.hpp"
#include "mcwf/util.hpp"

namespace mcwf::terrain {

namespace {

using Grid9 = std::array<std::array<double, 9>, 9>;

Grid9 blur9(const Grid9& g) {
    Grid9 out{};
    auto at = [&](int z, int x) -> double {
        z = clampInt(z, 0, 8);
        x = clampInt(x, 0, 8);

        return g[static_cast<size_t>(z)][static_cast<size_t>(x)];
    };

    for (int z = 0; z < 9; ++z) {
        for (int x = 0; x < 9; ++x) {
            out[static_cast<size_t>(z)][static_cast<size_t>(x)] =
                (at(z, x) * 0.28 +
                 (at(z - 1, x) + at(z + 1, x) + at(z, x - 1) + at(z, x + 1)) * 0.13 +
                 (at(z - 1, x - 1) + at(z - 1, x + 1) + at(z + 1, x - 1) + at(z + 1, x + 1)) * 0.045 +
                 (at(z - 2, x) + at(z + 2, x) + at(z, x - 2) + at(z, x + 2)) * 0.025) / 1.08;
        }
    }

    return out;
}

Grid9 relax9(const Grid9& in, double maxStep) {
    Grid9 out{};
    auto at = [&](int z, int x) -> double {
        z = clampInt(z, 0, 8);
        x = clampInt(x, 0, 8);

        return in[static_cast<size_t>(z)][static_cast<size_t>(x)];
    };

    for (int z = 0; z < 9; ++z) {
        for (int x = 0; x < 9; ++x) {
            std::array<double, 8> n = {
                at(z - 1, x), at(z + 1, x), at(z, x - 1), at(z, x + 1),
                at(z - 1, x - 1), at(z - 1, x + 1), at(z + 1, x - 1), at(z + 1, x + 1)
            };
            std::sort(n.begin(), n.end());
            double med = (n[3] + n[4]) * 0.5;
            double avg = (at(z - 1, x) + at(z + 1, x) + at(z, x - 1) + at(z, x + 1)) * 0.25;
            double v = in[static_cast<size_t>(z)][static_cast<size_t>(x)];
            v = std::min(v, med + maxStep);
            v = std::max(v, med - maxStep);
            out[static_cast<size_t>(z)][static_cast<size_t>(x)] = v * 0.82 + avg * 0.18;
        }
    }

    return out;
}

RawTerrain rawTerrain(const Config& cfg, double x, double z) {
    double hills = (fbm2d(x, z, cfg.seed + 1, 4, 1.0 / 112.0) - 0.5) * 2.0;
    double detail = (fbm2d(x, z, cfg.seed + 11, 3, 1.0 / 74.0) - 0.5) * 2.0;

    double amount = clampDouble(cfg.mountainAmount, 0.0, 1.0);
    double extremeAmount = clampDouble(cfg.extremeMountainAmount, 0.0, 1.0);

    double mntMask = fbm2d(x, z, cfg.seed + 2, 4, 1.0 / 610.0);
    double province = fbm2d(x - 2900.0, z + 1700.0, cfg.seed + 16, 3, 1.0 / 760.0);
    double provinceGate = smoothstep01((province - (0.39 - amount * 0.050)) / 0.34);
    double lo = 0.530 - 0.115 * amount;
    double mnt = std::pow(smoothstep01((mntMask - lo) / std::max(0.001, 0.90 - lo)), 1.32);
    mnt *= 0.58 + provinceGate * 0.42;

    double ridgeRaw = 1.0 - std::abs(fbm2d(x, z, cfg.seed + 12, 3, 1.0 / 250.0) - 0.5) * 2.0;
    double ridge = std::pow(smoothstep01((ridgeRaw - 0.30) / 0.62), 1.18);

    double rangeNoise = fbm2d(x - 1700.0, z + 2600.0, cfg.seed + 14, 4, 1.0 / 980.0);
    double rangeGate = smoothstep01((rangeNoise - (0.625 - extremeAmount * 0.070)) / 0.30);
    double rangeRidge = 1.0 - std::abs(fbm2d(x + 6400.0, z - 3100.0, cfg.seed + 15, 4, 1.0 / 300.0) - 0.5) * 2.0;
    rangeRidge = std::pow(smoothstep01((rangeRidge - 0.25) / 0.66), 1.48);
    double extreme = rangeGate * rangeRidge;

    double peakNoise = fbm2d(x + 11200.0, z - 8800.0, cfg.seed + 18, 4, 1.0 / 540.0);
    double peakGate = smoothstep01((peakNoise - 0.655) / 0.245);
    double peakRidge = 1.0 - std::abs(fbm2d(x - 14400.0, z + 5300.0, cfg.seed + 19, 4, 1.0 / 190.0) - 0.5) * 2.0;
    peakRidge = std::pow(smoothstep01((peakRidge - 0.22) / 0.60), 1.34);
    double alpinePeak = peakGate * peakRidge * (0.45 + 0.55 * provinceGate);

    double mntRough = (fbm2d(x + 4100.0, z - 2700.0, cfg.seed + 21, 3, 1.0 / 92.0) - 0.5) * 2.0;
    double mntMedium = (fbm2d(x - 6200.0, z + 1500.0, cfg.seed + 22, 3, 1.0 / 150.0) - 0.5) * 2.0;
    double mntShape = smoothstep01(std::max({mnt, extreme * 0.78, alpinePeak * 0.88}));

    double h = cfg.baseHeight +
               hills * cfg.hillAmp * 0.44 +
               detail * 0.22 +
               mnt * cfg.mountainAmp * 0.62 +
               std::pow(mnt, 1.18) * ridge * cfg.mountainAmp * 0.26 +
               extreme * cfg.mountainAmp * 0.50 +
               alpinePeak * cfg.mountainAmp * 1.26 +
               mntShape * (mntMedium * cfg.mountainAmp * 0.055 +
                           mntRough * cfg.mountainAmp * 0.028);
    return {h, mntShape};
}

}  // namespace

RawTerrain terrainHeight(const Config& cfg, double x, double z) {
    Grid9 h{};
    Grid9 m{};

    for (int dz = -4; dz <= 4; ++dz) {
        for (int dx = -4; dx <= 4; ++dx) {
            RawTerrain r = rawTerrain(cfg, x + dx, z + dz);
            h[static_cast<size_t>(dz + 4)][static_cast<size_t>(dx + 4)] = r.h;
            m[static_cast<size_t>(dz + 4)][static_cast<size_t>(dx + 4)] = r.mountain;
        }
    }
    Grid9 b1 = blur9(h);
    Grid9 b2 = blur9(b1);
    Grid9 mixed{};

    for (int zi = 0; zi < 9; ++zi) {
        for (int xi = 0; xi < 9; ++xi) {
            mixed[static_cast<size_t>(zi)][static_cast<size_t>(xi)] =
                h[static_cast<size_t>(zi)][static_cast<size_t>(xi)] * 0.68 +
                b1[static_cast<size_t>(zi)][static_cast<size_t>(xi)] * 0.24 +
                b2[static_cast<size_t>(zi)][static_cast<size_t>(xi)] * 0.08;
        }
    }
    Grid9 relaxed = relax9(mixed, 8.0);
    Grid9 mb = blur9(m);

    return {relaxed[4][4], mb[4][4]};
}

void terrainHeightGrid(const Config& cfg, int x0, int z0, int w, int h, GridD& outH, GridD& outM) {
    constexpr int margin = 4;
    GridD rawH(w + margin * 2, h + margin * 2);
    GridD rawM(w + margin * 2, h + margin * 2);

    for (int gz = 0; gz < rawH.h; ++gz) {
        for (int gx = 0; gx < rawH.w; ++gx) {
            RawTerrain r = rawTerrain(cfg, static_cast<double>(x0 + gx - margin), static_cast<double>(z0 + gz - margin));
            rawH.at(gz, gx) = r.h;
            rawM.at(gz, gx) = r.mountain;
        }
    }

    GridD b1 = blurGrid(rawH);
    GridD b2 = blurGrid(b1);
    GridD mixed(rawH.w, rawH.h);

    for (int z = 0; z < rawH.h; ++z) {
        for (int x = 0; x < rawH.w; ++x) {
            mixed.at(z, x) = rawH.at(z, x) * 0.68 + b1.at(z, x) * 0.24 + b2.at(z, x) * 0.08;
        }
    }
    GridD relaxed = relaxGrid(mixed, 8.0, 1);
    GridD mountain = blurGrid(rawM);

    outH = GridD(w, h);
    outM = GridD(w, h);

    for (int z = 0; z < h; ++z) {
        for (int x = 0; x < w; ++x) {
            outH.at(z, x) = relaxed.at(z + margin, x + margin);
            outM.at(z, x) = mountain.at(z + margin, x + margin);
        }
    }
}

double fastSurfaceHeight(const Config& cfg, double x, double z) {
    // Python caches _surface_height_at heavily because river/lake/cave feature
    // cells ask for the same surface probes from many neighboring columns. The
    // native path needs the same cache, otherwise the local water-body planner
    // spends most of its time recomputing 9x9 smoothed terrain probes.
    struct CacheEntry {
        int seed = 0;
        int floorY = 0;
        int baseHeight16 = 0;
        int hillAmp16 = 0;
        int mountainAmp16 = 0;
        int mountainAmount4096 = 0;
        int x16 = 0;
        int z16 = 0;
        bool operator==(const CacheEntry& o) const {
            return seed == o.seed && floorY == o.floorY && baseHeight16 == o.baseHeight16 &&
                   hillAmp16 == o.hillAmp16 && mountainAmp16 == o.mountainAmp16 &&
                   mountainAmount4096 == o.mountainAmount4096 && x16 == o.x16 && z16 == o.z16;
        }
    };
    struct Hash {
        size_t operator()(const CacheEntry& e) const {
            uint32_t h = mix32(static_cast<uint32_t>(e.seed));
            h ^= mix32(static_cast<uint32_t>(e.floorY) + 0x9e3779b9u);
            h ^= mix32(static_cast<uint32_t>(e.baseHeight16) + 0x85ebca6bu);
            h ^= mix32(static_cast<uint32_t>(e.hillAmp16) + 0xc2b2ae35u);
            h ^= mix32(static_cast<uint32_t>(e.mountainAmp16) + 0x27d4eb2du);
            h ^= mix32(static_cast<uint32_t>(e.mountainAmount4096) + 0x165667b1u);
            h ^= mix32(static_cast<uint32_t>(e.x16) + 0xd3a2646cu);
            h ^= mix32(static_cast<uint32_t>(e.z16) + 0xfd7046c5u);

            return static_cast<size_t>(h);
        }
    };
    thread_local std::unordered_map<CacheEntry, int, Hash> cache;
    if (cache.size() > 200000) {
        cache.clear();
    }
    CacheEntry key;
    key.seed = cfg.seed;
    key.floorY = cfg.floorY;
    key.baseHeight16 = static_cast<int>(std::round(cfg.baseHeight * 16.0));
    key.hillAmp16 = static_cast<int>(std::round(cfg.hillAmp * 16.0));
    key.mountainAmp16 = static_cast<int>(std::round(cfg.mountainAmp * 16.0));
    key.mountainAmount4096 = static_cast<int>(std::round(cfg.mountainAmount * 4096.0));
    key.x16 = static_cast<int>(std::round(x * 16.0));
    key.z16 = static_cast<int>(std::round(z * 16.0));
    auto it = cache.find(key);
    if (it != cache.end()) {
        return static_cast<double>(it->second);
    }
    RawTerrain t = terrainHeight(cfg, x, z);
    int value = static_cast<int>(std::round(clampDouble(t.h, cfg.floorY + 3, MIN_Y + WORLD_HEIGHT - 40)));
    cache[key] = value;

    return static_cast<double>(value);
}

}  // namespace mcwf::terrain
