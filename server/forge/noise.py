"""Seeded, numpy-vectorized value noise with fractal (fBm) octaves.

No external noise dependency. Deterministic for a given integer seed, so the
same config + seed always rebuilds the same world.
"""
import numpy as np

_UINT = 0xFFFFFFFF


def _hash01(ix, iy, iz, seed):
    """Integer lattice hash -> float in [0, 1). Inputs are int64 ndarrays."""
    n = (ix.astype(np.int64) * 374761393
         + iy.astype(np.int64) * 668265263
         + iz.astype(np.int64) * 2147483647
         + np.int64(seed) * 1013904223)
    n &= _UINT
    n = (n ^ (n >> 13)) & _UINT
    n = (n * 1274126177) & _UINT
    n = (n ^ (n >> 16)) & _UINT
    return n.astype(np.float64) / float(_UINT)


def _smooth(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)  # quintic smoothstep


def _lerp(a, b, t):
    return a + (b - a) * t


def value2d(x, z, seed):
    """Value noise in [0,1] at coordinates x, z (float ndarrays)."""
    x0 = np.floor(x).astype(np.int64)
    z0 = np.floor(z).astype(np.int64)
    x1, z1 = x0 + 1, z0 + 1
    fx = _smooth(x - x0)
    fz = _smooth(z - z0)
    zero = np.zeros_like(x0)
    c00 = _hash01(x0, zero, z0, seed)
    c10 = _hash01(x1, zero, z0, seed)
    c01 = _hash01(x0, zero, z1, seed)
    c11 = _hash01(x1, zero, z1, seed)
    return _lerp(_lerp(c00, c10, fx), _lerp(c01, c11, fx), fz)


def value3d(x, y, z, seed):
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    z0 = np.floor(z).astype(np.int64)
    x1, y1, z1 = x0 + 1, y0 + 1, z0 + 1
    fx = _smooth(x - x0)
    fy = _smooth(y - y0)
    fz = _smooth(z - z0)

    def h(ax, ay, az):
        return _hash01(ax, ay, az, seed)

    c000 = h(x0, y0, z0); c100 = h(x1, y0, z0)
    c010 = h(x0, y1, z0); c110 = h(x1, y1, z0)
    c001 = h(x0, y0, z1); c101 = h(x1, y0, z1)
    c011 = h(x0, y1, z1); c111 = h(x1, y1, z1)
    x00 = _lerp(c000, c100, fx); x10 = _lerp(c010, c110, fx)
    x01 = _lerp(c001, c101, fx); x11 = _lerp(c011, c111, fx)
    y0i = _lerp(x00, x10, fy); y1i = _lerp(x01, x11, fy)
    return _lerp(y0i, y1i, fz)


def fbm2d(x, z, seed, octaves=4, freq=1.0, lacunarity=2.0, gain=0.5):
    total = 0.0
    amp = 1.0
    f = freq
    norm = 0.0
    for o in range(octaves):
        total += amp * value2d(x * f, z * f, seed + o * 1013)
        norm += amp
        amp *= gain
        f *= lacunarity
    return total / norm


def fbm3d(x, y, z, seed, octaves=3, freq=1.0, lacunarity=2.0, gain=0.5):
    total = 0.0
    amp = 1.0
    f = freq
    norm = 0.0
    for o in range(octaves):
        total += amp * value3d(x * f, y * f, z * f, seed + o * 7919)
        norm += amp
        amp *= gain
        f *= lacunarity
    return total / norm
