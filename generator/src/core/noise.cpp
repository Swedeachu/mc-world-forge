#include "mcwf/common.hpp"

uint32_t mix32(uint32_t n) {
    n ^= (n >> 16);
    n = static_cast<uint32_t>(n * 0x7feb352du);
    n ^= (n >> 15);
    n = static_cast<uint32_t>(n * 0x846ca68bu);
    n ^= (n >> 16);
    return n;
}

uint32_t hashInts(int64_t x, int64_t y, int64_t z, int seed, uint32_t salt) {
    // Match server/forge/noise.py exactly for lattice noise. The previous C++
    // implementation used a different mix and extra octave salts, which made
    // terrain, climate, caves, and preview diverge hard from the Python world.
    uint32_t n = 0;
    n += static_cast<uint32_t>(x * 374761393ll);
    n += static_cast<uint32_t>(y * 668265263ll);
    n += static_cast<uint32_t>(z * 2147483647ll);
    n += static_cast<uint32_t>((static_cast<int64_t>(seed) + static_cast<int64_t>(salt)) * 1013904223ll);
    n ^= (n >> 13);
    n = static_cast<uint32_t>(n * 1274126177u);
    n ^= (n >> 16);
    return n;
}

double hash01(int64_t x, int64_t y, int64_t z, int seed, uint32_t salt) {
    return static_cast<double>(hashInts(x, y, z, seed, salt)) / static_cast<double>(0xffffffffu);
}

double smooth(double t) {
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
}

double lerp(double a, double b, double t) {
    return a + (b - a) * t;
}

double value2d(double x, double z, int seed, uint32_t salt) {
    int64_t x0 = static_cast<int64_t>(std::floor(x));
    int64_t z0 = static_cast<int64_t>(std::floor(z));
    double fx = smooth(x - static_cast<double>(x0));
    double fz = smooth(z - static_cast<double>(z0));
    double c00 = hash01(x0, 0, z0, seed, salt);
    double c10 = hash01(x0 + 1, 0, z0, seed, salt);
    double c01 = hash01(x0, 0, z0 + 1, seed, salt);
    double c11 = hash01(x0 + 1, 0, z0 + 1, seed, salt);
    return lerp(lerp(c00, c10, fx), lerp(c01, c11, fx), fz);
}

double value3d(double x, double y, double z, int seed, uint32_t salt) {
    int64_t x0 = static_cast<int64_t>(std::floor(x));
    int64_t y0 = static_cast<int64_t>(std::floor(y));
    int64_t z0 = static_cast<int64_t>(std::floor(z));
    double fx = smooth(x - static_cast<double>(x0));
    double fy = smooth(y - static_cast<double>(y0));
    double fz = smooth(z - static_cast<double>(z0));

    auto h = [&](int64_t ax, int64_t ay, int64_t az) {
        return hash01(ax, ay, az, seed, salt);
    };

    double c000 = h(x0, y0, z0);
    double c100 = h(x0 + 1, y0, z0);
    double c010 = h(x0, y0 + 1, z0);
    double c110 = h(x0 + 1, y0 + 1, z0);
    double c001 = h(x0, y0, z0 + 1);
    double c101 = h(x0 + 1, y0, z0 + 1);
    double c011 = h(x0, y0 + 1, z0 + 1);
    double c111 = h(x0 + 1, y0 + 1, z0 + 1);
    double x00 = lerp(c000, c100, fx);
    double x10 = lerp(c010, c110, fx);
    double x01 = lerp(c001, c101, fx);
    double x11 = lerp(c011, c111, fx);
    double y0i = lerp(x00, x10, fy);
    double y1i = lerp(x01, x11, fy);
    return lerp(y0i, y1i, fz);
}

double fbm2d(double x, double z, int seed, int octaves, double freq, double gain) {
    double total = 0.0;
    double amp = 1.0;
    double norm = 0.0;
    double f = freq;
    for (int i = 0; i < octaves; ++i) {
        total += amp * value2d(x * f, z * f, seed + i * 1013);
        norm += amp;
        amp *= gain;
        f *= 2.0;
    }
    return total / std::max(0.000001, norm);
}

double fbm3d(double x, double y, double z, int seed, int octaves, double freq, double gain) {
    double total = 0.0;
    double amp = 1.0;
    double norm = 0.0;
    double f = freq;
    for (int i = 0; i < octaves; ++i) {
        total += amp * value3d(x * f, y * f, z * f, seed + i * 7919);
        norm += amp;
        amp *= gain;
        f *= 2.0;
    }
    return total / std::max(0.000001, norm);
}

double rand01(int x, int y, int z, const Config& cfg, uint32_t salt) {
    uint32_t n = (static_cast<uint32_t>(x) * 0x632be59bu) ^
                 (static_cast<uint32_t>(z) * 0x85157af5u) ^
                 (static_cast<uint32_t>(cfg.seed) * 0x9e3779b9u) ^
                 (salt * 0x165667b1u) ^
                 (static_cast<uint32_t>(y) * 0x27d4eb2du);
    return static_cast<double>(mix32(n)) / 4294967296.0;
}

double smoothstep01(double t) {
    t = clampDouble(t, 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}
