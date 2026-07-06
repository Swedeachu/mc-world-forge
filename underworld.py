"""
Underworld layers for the vanilla generator: below the overworld caverns the
world can continue down through a full Deep Dark band, so a player can mine/cave
straight down into wider sculk caverns.

This is an original implementation (not copied from Minecraft's source); it just
uses the same block ids so the result reads correctly in-game. All work happens
on the chunk block array that generator.generate_chunk already built, rewriting
everything at or below cfg.floor_y (which used to be a bedrock floor + void).

Layer stack from the overworld floor downward:
    [ overworld stone ]         (untouched, above cfg.floor_y)
    [ Deep Dark ]  deepslate / tuff / sculk, widened caverns   (optional)
    [ bedrock floor at MIN_Y ]

The Nether generator in this file is still used by generator.py, but only for
the separate horizontal Nether box. It is no longer stacked under the overworld.
"""
import numpy as np

import noise

MIN_Y = -64

NETHER_BIOMES = (
    "minecraft:nether_wastes",
    "minecraft:crimson_forest",
    "minecraft:warped_forest",
    "minecraft:soul_sand_valley",
    "minecraft:basalt_deltas",
)


def underworld_bottom(cfg):
    """Lowest overworld y that stays untouched is floor_y; below is underworld."""
    return cfg.floor_y


def _segments(cfg):
    """Return [(name, y_hi, y_lo)] inclusive bands, top-down."""
    top = cfg.floor_y
    segs = []
    if cfg.deep_dark:
        lo = MIN_Y + 1
        if lo <= top:
            segs.append(("deep_dark", top, lo))
    return segs


def layer_starts(cfg):
    """Top (entry) Y of each underworld band, or None if disabled."""
    out = {"deep_dark": None, "nether": None}
    for name, y_hi, y_lo in _segments(cfg):
        out[name] = int(y_hi)
    return out


def nether_biome_map(cfg, X, Z):
    """Per-column nether biome id (index into NETHER_BIOMES). Arrays are [z, x]."""
    s = cfg.seed
    sel = noise.fbm2d(X + 7000, Z - 4000, s + 501, octaves=3, freq=1 / 210.0)
    warm = noise.fbm2d(X - 3000, Z + 9000, s + 502, octaves=3, freq=1 / 260.0)
    out = np.zeros(X.shape, dtype=np.int16)          # 0 = nether_wastes
    out[(sel > 0.55) & (warm > 0.5)] = 1             # crimson_forest
    out[(sel > 0.55) & (warm <= 0.5)] = 2            # warped_forest
    out[(sel <= 0.35) & (warm < 0.42)] = 3           # soul_sand_valley
    out[(sel <= 0.30) & (warm >= 0.62)] = 4          # basalt_deltas
    return out


def apply_underworld(cfg, blocks, block_ids, X, Z, inside, on_wall, rng):
    """Rewrite the chunk's block column at/below cfg.floor_y into the layers."""
    segs = _segments(cfg)
    neth_biome = np.full(X.shape, -1, dtype=np.int16)
    if not segs:
        return neth_biome

    H, W = 16, 16
    world_h = block_ids.shape[0]
    y_world = np.arange(world_h) + MIN_Y                  # actual world y per index
    inside3 = inside[None, :, :]
    s = cfg.seed

    # Wipe the old bedrock floor + void from floor_y downward (inside only).
    wipe = inside3 & (y_world[:, None, None] <= cfg.floor_y)
    block_ids[wipe] = blocks.air

    # bedrock floor at the very bottom
    floor_row = (y_world == MIN_Y)
    block_ids[np.broadcast_to(inside3 & floor_row[:, None, None], block_ids.shape)] = blocks.bedrock

    # 3D coordinate grids for noise (only need x,z broadcast; y added per band)
    Xf = X.astype(np.float64)
    Zf = Z.astype(np.float64)

    for name, y_hi, y_lo in segs:
        yr = np.arange(y_lo, y_hi + 1)
        if yr.size == 0:
            continue
        band = slice(y_lo - MIN_Y, y_hi - MIN_Y + 1)      # index range into block_ids
        nb = yr.size

        # 3D noise density for this band -> caverns
        Yb = yr[:, None, None].astype(np.float64)
        X3 = np.broadcast_to(Xf[None, :, :], (nb, H, W))
        Z3 = np.broadcast_to(Zf[None, :, :], (nb, H, W))
        Y3 = np.broadcast_to(Yb, (nb, H, W))
        inside_b = np.broadcast_to(inside[None, :, :], (nb, H, W))

        if name == "deep_dark":
            _fill_deep_dark(cfg, blocks, block_ids, band, X3, Y3, Z3, inside_b, rng)

    _connect_caves(cfg, blocks, block_ids, X, Z, inside, segs)
    return neth_biome


def _connect_caves(cfg, blocks, block_ids, X, Z, inside, segs):
    """Drill open connections so overworld caves flow into the deep dark.

    This pass intentionally looks for existing air just above the layer and then
    joins it to nearby deep-dark air. It is denser than before, but it still only
    connects to real caverns instead of blindly boring columns through solid
    deepslate."""
    if not segs:
        return
    top = segs[0][1]            # entry Y of the first underworld band (== floor_y)
    bottom = segs[-1][2]        # lowest fillable Y (just above bedrock floor)
    air = blocks.air
    stop = {blocks.bedrock, blocks.lava}
    zz, xx = np.nonzero(inside)
    for i in range(zz.size):
        z, x = int(zz[i]), int(xx[i])
        wx = int(X[z, x])
        wz = int(Z[z, x])

        n = (wx * 0x632BE59B) ^ (wz * 0x85157AF5) ^ (int(cfg.seed) * 0x9E3779B1) ^ 0x3D2D5D7B
        n &= 0xFFFFFFFF
        n ^= (n >> 16)
        n = (n * 0x7FEB352D) & 0xFFFFFFFF
        if (n / 0xFFFFFFFF) > 0.18:
            continue

        cave_y = None
        for y in range(top + 1, top + 22):
            if y < MIN_Y or y - MIN_Y >= block_ids.shape[0]:
                continue
            if int(block_ids[y - MIN_Y, z, x]) == air:
                cave_y = y
                break
        if cave_y is None:
            continue

        target = _find_nearby_deep_dark_air(block_ids, blocks, x, z, cave_y,
                                            bottom, search_radius=6, depth=72)
        if target is None:
            continue

        tx, ty, tz = target
        _carve_connector(block_ids, blocks, x, cave_y - 1, z, tx, ty, tz, stop)


def _find_nearby_deep_dark_air(block_ids, blocks, x, z, cave_y, bottom,
                               search_radius, depth):
    air = blocks.air
    best = None
    best_score = 1 << 30
    scan_lo = max(bottom + 1, cave_y - depth)
    for dz in range(-search_radius, search_radius + 1):
        z2 = z + dz
        if z2 < 0 or z2 >= 16:
            continue
        for dx in range(-search_radius, search_radius + 1):
            x2 = x + dx
            if x2 < 0 or x2 >= 16:
                continue
            dist = dx * dx + dz * dz
            if dist > search_radius * search_radius:
                continue
            for y in range(cave_y - 1, scan_lo - 1, -1):
                if int(block_ids[y - MIN_Y, z2, x2]) != air:
                    continue
                score = (cave_y - y) * 4 + dist * 7
                if score < best_score:
                    best_score = score
                    best = (x2, y, z2)
                break
    return best


def _carve_connector(block_ids, blocks, x0, y0, z0, x1, y1, z1, stop):
    steps = max(abs(x1 - x0), abs(z1 - z0), abs(y1 - y0), 1)
    for i in range(steps + 1):
        t = i / steps
        x = int(round(x0 + (x1 - x0) * t))
        y = int(round(y0 + (y1 - y0) * t))
        z = int(round(z0 + (z1 - z0) * t))
        for dz in (-1, 0, 1):
            z2 = z + dz
            if z2 < 0 or z2 >= 16:
                continue
            for dx in (-1, 0, 1):
                x2 = x + dx
                if x2 < 0 or x2 >= 16:
                    continue
                if dx * dx + dz * dz > 1:
                    continue
                if y < MIN_Y or y - MIN_Y >= block_ids.shape[0]:
                    continue
                b = int(block_ids[y - MIN_Y, z2, x2])
                if b not in stop:
                    block_ids[y - MIN_Y, z2, x2] = blocks.air


# ---------------------------------------------------------------------------
# Deep Dark
# ---------------------------------------------------------------------------

def _smoothstep01(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _fill_deep_dark(cfg, blocks, block_ids, band, X3, Y3, Z3, inside_b, rng):
    s = cfg.seed
    sub = block_ids[band]

    # Solid deepslate everywhere first.
    sub[inside_b] = blocks.deepslate

    # Tuff veins and cobbled patches.
    tuff = noise.fbm3d(X3 * 1.0, Y3 * 1.0, Z3 * 1.0, s + 611, octaves=3, freq=1 / 12.0)
    sub[inside_b & (tuff > 0.68)] = blocks.tuff
    cob = noise.fbm3d(X3, Y3, Z3, s + 612, octaves=2, freq=1 / 9.0)
    sub[inside_b & (cob > 0.72)] = blocks.cobbled_deepslate

    # Blend the top of the band into the overworld stone (no hard seam): the
    # upper rows are a probabilistic stone/deepslate mix that fades to pure
    # deepslate a few blocks down, matching vanilla's deepslate transition.
    nb = sub.shape[0]
    depth_from_top = (nb - 1) - np.arange(nb)
    p_stone = np.clip(1.0 - depth_from_top / 6.0, 0.0, 1.0)[:, None, None]
    rs = rng.random(sub.shape)
    mix = inside_b & (sub == blocks.deepslate) & (rs < p_stone * 0.7)
    sub[mix] = blocks.stone

    # Match the cavern layer above more closely: mostly 3D tunnel fields and
    # connected cave graphs, scaled wider for the deep dark. Cheese/room masks are
    # only accents now, otherwise the layer turns into a flat sculk hall.
    rows = np.arange(nb)[:, None, None].astype(np.float64)
    top01 = rows / max(1.0, float(nb - 1))             # 0 bottom .. 1 top
    deep01 = 1.0 - _smoothstep01(top01)                # wider lower down

    warp_x = (noise.fbm3d(X3 + 1400, Y3 * 0.36, Z3 - 900, s + 613, octaves=2, freq=1 / 128.0) - 0.5) * 14.0
    warp_z = (noise.fbm3d(X3 - 700, Y3 * 0.36, Z3 + 1200, s + 614, octaves=2, freq=1 / 128.0) - 0.5) * 14.0
    WX = X3 + warp_x
    WZ = Z3 + warp_z

    province = noise.fbm2d(X3[0] - 2600, Z3[0] + 3600, s + 618, octaves=3, freq=1 / 240.0)[None, :, :]
    province = _smoothstep01((province - 0.22) / 0.34)
    local_gate = noise.fbm3d(WX - 3100, Y3 * 0.42, WZ + 2200, s + 619, octaves=2, freq=1 / 92.0)
    local_gate = _smoothstep01((local_gate - 0.28) / 0.34)
    gate = np.maximum(province * (0.54 + 0.66 * local_gate), local_gate * 0.20)

    cheese = noise.fbm3d(WX, Y3 * 0.92, WZ, s + 615, octaves=3, freq=1 / 58.0)
    cheese2 = noise.fbm3d(WX - 900, Y3 * 0.74, WZ + 900, s + 616, octaves=2, freq=1 / 86.0)
    # Cheese caves are intentionally secondary. The deep dark should read like
    # the deep cavern pass above it with wider tunnels, not one giant flat hall.
    cheese_cut = 0.718 - 0.024 * deep01 - 0.024 * gate
    cheese_open = (cheese > cheese_cut) & (cheese2 > 0.532 - 0.024 * deep01)

    # Same idea as the normal deep cavern pass: two intersecting 3D contour
    # fields make organic paths, only wider than the overworld caves.
    spag_a = noise.fbm3d(WX + 7800, Y3 * 1.06, WZ - 7800, s + 617, octaves=2, freq=1 / 43.0)
    spag_b = noise.fbm3d(WX - 4200, Y3 * 0.98, WZ + 4100, s + 620, octaves=2, freq=1 / 50.0)
    spag_w = (0.053 + 0.034 * deep01) * (0.78 + 0.88 * gate)
    spaghetti_open = (np.abs(spag_a - 0.5) < spag_w) & (np.abs(spag_b - 0.5) < spag_w * 1.92)

    noodle_a = noise.fbm3d(WX - 12000, Y3 * 1.28, WZ + 12000, s + 623, octaves=1, freq=1 / 27.0)
    noodle_b = noise.fbm3d(WX + 5300, Y3 * 1.20, WZ - 5300, s + 624, octaves=1, freq=1 / 32.0)
    noodle_w = (0.027 + 0.017 * deep01) * (0.82 + 0.72 * gate)
    noodle_open = (np.abs(noodle_a - 0.5) < noodle_w) & (np.abs(noodle_b - 0.5) < noodle_w * 2.18)

    # Very rare large rooms. The feature-cell cave graph below supplies most of
    # the openness; this only makes occasional ancient-city-sized pockets.
    room = noise.fbm3d(WX + 18000, Y3 * 0.48, WZ - 18000, s + 625, octaves=2, freq=1 / 122.0)
    room_gate = noise.fbm2d(X3[0] + 9100, Z3[0] - 9100, s + 626, octaves=2, freq=1 / 340.0)[None, :, :]
    room_center = 0.30 + 0.20 * noise.fbm2d(X3[0] - 11100, Z3[0] + 11100, s + 627, octaves=2, freq=1 / 300.0)[None, :, :]
    vertical = np.exp(-((top01 - room_center) ** 2) / 0.018)
    room_open = (room > 0.694 - 0.018 * deep01) & (room_gate > 0.620) & (vertical > 0.43)

    lace_a = noise.fbm3d(WX + 27100, Y3 * 1.02, WZ - 27100, s + 629, octaves=2, freq=1 / 39.0)
    lace_b = noise.fbm3d(WX - 21300, Y3 * 1.14, WZ + 21300, s + 630, octaves=2, freq=1 / 45.0)
    lace_w = (0.031 + 0.021 * deep01) * (0.82 + 0.70 * local_gate)
    lace_open = (np.abs(lace_a - 0.5) < lace_w) & (np.abs(lace_b - 0.5) < lace_w * 1.78)

    web_a = noise.fbm3d(WX + 33000, Y3 * 1.04, WZ - 17000, s + 631, octaves=2, freq=1 / 34.0)
    web_b = noise.fbm3d(WX - 19000, Y3 * 1.16, WZ + 29000, s + 632, octaves=2, freq=1 / 38.0)
    web_c = noise.fbm3d(WX + 8000, Y3 * 0.90, WZ + 26000, s + 633, octaves=2, freq=1 / 54.0)
    web_w = 0.034 + 0.021 * deep01
    web_open = ((np.abs(web_a - 0.5) < web_w) & (np.abs(web_b - 0.5) < web_w * 1.72)) | \
               ((np.abs(web_b - 0.5) < web_w * 0.82) & (np.abs(web_c - 0.5) < web_w * 1.52))

    rescue_a = noise.fbm3d(WX - 41000, Y3 * 0.94, WZ + 15000, s + 634, octaves=2, freq=1 / 35.0)
    rescue_b = noise.fbm3d(WX + 12500, Y3 * 1.06, WZ - 36500, s + 635, octaves=2, freq=1 / 41.0)
    rescue_c = noise.fbm3d(WX - 28000, Y3 * 0.84, WZ - 12000, s + 636, octaves=2, freq=1 / 59.0)
    rescue_w = 0.036 + 0.022 * deep01
    rescue_open = (gate < 0.32) & (((np.abs(rescue_a - 0.5) < rescue_w) & (np.abs(rescue_b - 0.5) < rescue_w * 1.76)) | \
                                  ((np.abs(rescue_b - 0.5) < rescue_w * 0.88) & (np.abs(rescue_c - 0.5) < rescue_w * 1.48)))

    near_top = _smoothstep01((top01 - 0.84) / 0.14)
    near_bedrock = _smoothstep01(rows / 5.0)
    open_allowed = inside_b & (near_bedrock > 0.05)

    air_mask = open_allowed & (((gate > 0.085) & (cheese_open | spaghetti_open | noodle_open | lace_open | room_open)) | web_open | rescue_open)

    # Keep the entry rows closer to normal overworld cavern density so the layer
    # transitions instead of immediately exploding into void. Existing overworld
    # cave connections can still punch through via _connect_caves().
    top_throttle = near_top > 0.0
    if np.any(top_throttle):
        top_noise = noise.fbm3d(WX + 2300, Y3 * 1.10, WZ - 2300, s + 628, octaves=2, freq=1 / 36.0)
        air_mask &= (~top_throttle) | (top_noise > 0.13)

    sub[air_mask] = blocks.air
    _carve_deep_dark_networks(cfg, blocks, sub, X3, Y3, Z3, inside_b)
    _solidify_deep_dark_ribs(cfg, blocks, sub, X3, Y3, Z3, inside_b)
    _roughen_deep_dark_floors(cfg, blocks, sub, X3, Y3, Z3, inside_b, rng)

    block_ids[band] = sub
    _deep_dark_stalactites(cfg, blocks, block_ids, band, inside_b, rng)
    _deep_dark_ores(cfg, blocks, block_ids, band, inside_b, rng)

    # Sculk crust on cavern floors + sparse shriekers/sensors/catalysts.
    _sculk_pass(cfg, blocks, block_ids, band, inside_b, rng)


def _mix32(v):
    v = int(v) & 0xFFFFFFFF
    v ^= (v >> 16)
    v = (v * 0x7FEB352D) & 0xFFFFFFFF
    v ^= (v >> 15)
    v = (v * 0x846CA68B) & 0xFFFFFFFF
    v ^= (v >> 16)
    return v & 0xFFFFFFFF


def _hash2i(x, z, seed, salt):
    return _mix32(int(x) * 374761393 + int(z) * 668265263 + int(seed) * 1442695041 + int(salt) * 2246822519)


def _rand01(x, z, seed, salt):
    return _hash2i(x, z, seed, salt) / 4294967296.0


def _rand_range(x, z, seed, salt, lo, hi):
    return lo + (hi - lo) * _rand01(x, z, seed, salt)


def _iter_feature_cells(X, Z, spacing, reach):
    min_x = int(np.floor(float(X.min())))
    max_x = int(np.ceil(float(X.max())))
    min_z = int(np.floor(float(Z.min())))
    max_z = int(np.ceil(float(Z.max())))
    cx0 = (min_x - reach) // spacing - 1
    cx1 = (max_x + reach) // spacing + 1
    cz0 = (min_z - reach) // spacing - 1
    cz1 = (max_z + reach) // spacing + 1
    for cell_z in range(cz0, cz1 + 1):
        for cell_x in range(cx0, cx1 + 1):
            yield cell_x, cell_z


def _solidify_deep_dark_ribs(cfg, blocks, sub, X3, Y3, Z3, inside_b):
    # Re-introduce sparse solid ribs and pillar curtains after cave carving. Keep
    # this light: the deep dark should share the openness of the cavern layer,
    # not turn into noisy sponge walls.
    air = sub == blocks.air
    if not air.any():
        return

    top01 = np.linspace(0.0, 1.0, sub.shape[0])[:, None, None]
    mid_gate = _smoothstep01((top01 - 0.06) / 0.18) * (1.0 - _smoothstep01((top01 - 0.92) / 0.08))

    rib_a = noise.fbm2d(X3[0] + 51000, Z3[0] - 22000, cfg.seed + 91201, octaves=3, freq=1 / 76.0)
    rib_b = noise.fbm2d(X3[0] - 32000, Z3[0] + 47000, cfg.seed + 91202, octaves=3, freq=1 / 83.0)
    rib_line = ((np.abs(rib_a - 0.5) < 0.038) & (rib_b > 0.36)) | \
               ((np.abs(rib_b - 0.5) < 0.034) & (rib_a > 0.42))

    pillar_field = noise.fbm2d(X3[0] + 18000, Z3[0] + 18000, cfg.seed + 91203, octaves=2, freq=1 / 31.0)
    pillar_gate = noise.fbm2d(X3[0] - 27000, Z3[0] - 9000, cfg.seed + 91204, octaves=2, freq=1 / 118.0)
    pillar = (pillar_field > 0.745) & (pillar_gate > 0.42)

    rough = noise.fbm3d(X3 + 61000, Y3 * 0.80, Z3 - 61000, cfg.seed + 91205, octaves=2, freq=1 / 24.0)
    fill = air & inside_b & (mid_gate > 0.16) & (((rib_line[None, :, :]) & (rough > 0.38)) |
                                                 ((pillar[None, :, :]) & (rough > 0.42)))
    sub[fill] = np.where(rough[fill] > 0.62, blocks.tuff, blocks.deepslate)


def _roughen_deep_dark_floors(cfg, blocks, sub, X3, Y3, Z3, inside_b, rng):
    # Shape floors like normal caverns: broad shelves and gentle mounds that are
    # coherent across X/Z. Avoid the old per-block 3D noise floor, which made the
    # deep dark look like random gravel chaos instead of carved cave terrain.
    nb, h, w = sub.shape
    air = sub == blocks.air
    solid = sub != blocks.air
    floor = np.zeros_like(air)
    floor[:-1] = solid[:-1] & air[1:]
    floor &= inside_b
    if not floor.any():
        return

    X2 = X3[0]
    Z2 = Z3[0]
    broad2 = noise.fbm2d(X2 + 71000, Z2 - 71000, cfg.seed + 91301, octaves=3, freq=1 / 74.0)
    shelf2 = noise.fbm2d(X2 - 52000, Z2 + 52000, cfg.seed + 91302, octaves=2, freq=1 / 118.0)
    detail2 = noise.fbm2d(X2 + 18000, Z2 + 18000, cfg.seed + 91303, octaves=2, freq=1 / 42.0)
    mound_float2 = _smoothstep01((broad2 - 0.34) / 0.42) * 2.7 + _smoothstep01((shelf2 - 0.56) / 0.28) * 1.9
    mound_float2 += np.clip((detail2 - 0.60) / 0.30, 0.0, 1.0) * 0.55
    mound_height2 = np.floor(mound_float2).astype(np.int16)

    ys, zs, xs = np.nonzero(floor)
    for i in range(ys.size):
        y = int(ys[i])
        z = int(zs[i])
        x = int(xs[i])
        height = int(mound_height2[z, x])
        if height <= 0:
            continue
        bid = blocks.tuff if broad2[z, x] > 0.68 else blocks.deepslate
        for k in range(1, min(height, 4) + 1):
            yy = y + k
            if yy >= nb or sub[yy, z, x] != blocks.air:
                break
            sub[yy, z, x] = bid
            if k <= height - 1 and height >= 3:
                for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    z2 = z + dz
                    x2 = x + dx
                    if 0 <= z2 < h and 0 <= x2 < w and sub[yy, z2, x2] == blocks.air:
                        if mound_height2[z2, x2] >= k + 1 and detail2[z2, x2] > 0.50:
                            sub[yy, z2, x2] = bid


def _carve_deep_dark_networks(cfg, blocks, sub, X3, Y3, Z3, inside_b):
    # Deterministic feature-cell tunnels guarantee that every deep-dark region
    # has real pathing density. Noise caves provide organic pockets; these
    # segments provide the connected cave graph, matching how the normal cavern
    # layer above has many routes instead of isolated pockets.
    spacing = 46
    reach = 184
    y_min = float(Y3.min() + 5.0)
    y_max = float(Y3.max() - 3.0)
    if y_max <= y_min:
        return

    for cell_x, cell_z in _iter_feature_cells(X3[0], Z3[0], spacing, reach):
        if _rand01(cell_x, cell_z, cfg.seed, 91001) > 0.95:
            continue

        ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 91002, 8.0, spacing - 8.0)
        oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 91003, 8.0, spacing - 8.0)
        oy = _rand_range(cell_x, cell_z, cfg.seed, 91004, y_min, y_max)
        arms = 3 + int(_rand01(cell_x, cell_z, cfg.seed, 91005) * 4.0)
        base_angle = _rand_range(cell_x, cell_z, cfg.seed, 91006, 0.0, np.pi * 2.0)
        base_radius = _rand_range(cell_x, cell_z, cfg.seed, 91007, 5.40, 10.20)

        _carve_deep_room(blocks, sub, X3, Y3, Z3, inside_b, ox, oy, oz,
                         base_radius * 1.42, base_radius * 0.92, base_radius * 1.42)

        ends = []
        for arm in range(arms):
            angle = base_angle + (np.pi * 2.0 * arm / max(1, arms)) + _rand_range(cell_x, cell_z, cfg.seed, 91020 + arm, -0.55, 0.55)
            length = _rand_range(cell_x, cell_z, cfg.seed, 91030 + arm, 58.0, 136.0)
            mid_len = length * _rand_range(cell_x, cell_z, cfg.seed, 91040 + arm, 0.38, 0.66)
            ex = ox + np.cos(angle) * length
            ez = oz + np.sin(angle) * length
            ey = np.clip(oy + _rand_range(cell_x, cell_z, cfg.seed, 91050 + arm, -13.0, 13.0), y_min, y_max)
            mx = ox + np.cos(angle + _rand_range(cell_x, cell_z, cfg.seed, 91060 + arm, -0.45, 0.45)) * mid_len
            mz = oz + np.sin(angle + _rand_range(cell_x, cell_z, cfg.seed, 91070 + arm, -0.45, 0.45)) * mid_len
            my = np.clip((oy + ey) * 0.5 + _rand_range(cell_x, cell_z, cfg.seed, 91080 + arm, -5.0, 5.0), y_min, y_max)
            r = base_radius * _rand_range(cell_x, cell_z, cfg.seed, 91090 + arm, 0.78, 1.46)
            _carve_deep_segment(blocks, sub, X3, Y3, Z3, inside_b, ox, oy, oz, mx, my, mz, r)
            _carve_deep_segment(blocks, sub, X3, Y3, Z3, inside_b, mx, my, mz, ex, ey, ez, r * 0.88)
            if _rand01(cell_x, cell_z, cfg.seed, 91100 + arm) < 0.58:
                _carve_deep_room(blocks, sub, X3, Y3, Z3, inside_b, mx, my, mz, r * 1.38, r * 0.82, r * 1.38)
            ends.append((ex, ey, ez))

        if len(ends) >= 3:
            for link in range(2):
                a = ends[int(_rand01(cell_x, cell_z, cfg.seed, 91120 + link) * len(ends))]
                b = ends[int(_rand01(cell_x, cell_z, cfg.seed, 91130 + link) * len(ends))]
                if a == b:
                    continue
                r = base_radius * _rand_range(cell_x, cell_z, cfg.seed, 91140 + link, 0.42, 0.68)
                _carve_deep_segment(blocks, sub, X3, Y3, Z3, inside_b, a[0], a[1], a[2], b[0], b[1], b[2], r)


def _carve_deep_room(blocks, sub, X3, Y3, Z3, inside_b, cx, cy, cz, rx, ry, rz):
    d = ((X3 - cx) / max(0.1, rx)) ** 2 + ((Y3 - cy) / max(0.1, ry)) ** 2 + ((Z3 - cz) / max(0.1, rz)) ** 2
    solid = (sub != blocks.bedrock) & (sub != blocks.lava)
    sub[(d <= 1.0) & inside_b & solid] = blocks.air


def _carve_deep_segment(blocks, sub, X3, Y3, Z3, inside_b, ax, ay, az, bx, by, bz, radius):
    vx = bx - ax
    vy = by - ay
    vz = bz - az
    length2 = vx * vx + vy * vy + vz * vz
    if length2 <= 0.001:
        return
    t = ((X3 - ax) * vx + (Y3 - ay) * vy + (Z3 - az) * vz) / length2
    t = np.clip(t, 0.0, 1.0)
    px = ax + vx * t
    py = ay + vy * t
    pz = az + vz * t
    taper = np.sin(np.pi * t)
    r = radius * (0.45 + 0.55 * taper)
    d = (X3 - px) ** 2 + ((Y3 - py) * 1.04) ** 2 + (Z3 - pz) ** 2
    solid = (sub != blocks.bedrock) & (sub != blocks.lava)
    sub[(d <= r * r) & inside_b & solid] = blocks.air


def _sample_ore_y(rng, lo, hi, bias):
    if hi <= lo:
        return int(lo)
    t = rng.random()
    if bias == "low":
        t = t * t
    elif bias == "high":
        t = 1.0 - (1.0 - t) * (1.0 - t)
    elif bias == "mid":
        t = (rng.random() + rng.random()) * 0.5
    return int(round(lo + (hi - lo) * t))


def _place_ore_blob(sub, x, y, z, size, oid, rng, replaceable):
    nb, h, w = sub.shape
    count = max(1, int(size))
    cx = float(x)
    cy = float(y)
    cz = float(z)
    for _ in range(count):
        ix = int(round(cx))
        iy = int(round(cy))
        iz = int(round(cz))
        radius = 1 if rng.random() < 0.72 else 2
        for dz in range(-radius, radius + 1):
            zz = iz + dz
            if zz < 0 or zz >= h:
                continue
            for dx in range(-radius, radius + 1):
                xx = ix + dx
                if xx < 0 or xx >= w:
                    continue
                for dy in range(-radius, radius + 1):
                    yy = iy + dy
                    if yy < 0 or yy >= nb:
                        continue
                    if dx * dx + dy * dy + dz * dz > radius * radius + 0.35:
                        continue
                    if sub[yy, zz, xx] in replaceable and rng.random() < 0.72:
                        sub[yy, zz, xx] = oid
        cx += rng.normal(0.0, 0.78)
        cy += rng.normal(0.0, 0.54)
        cz += rng.normal(0.0, 0.78)


def _deep_dark_ores(cfg, blocks, block_ids, band, inside_b, rng):
    sub = block_ids[band]
    nb = sub.shape[0]
    if nb <= 0 or not getattr(cfg, "ores", None):
        return

    # The overworld ore pass happens before the deep dark rewrite and only
    # targets the normal cavern stone. Re-run the same ore config inside the
    # deep-dark rock so this lower cave layer is not barren. Use a strong count
    # multiplier because the visible cave-wall density should match the cavern
    # layer above even though this vertical band is shorter.
    replaceable = {blocks.deepslate, blocks.tuff, blocks.cobbled_deepslate, blocks.stone}
    rock = ((sub == blocks.deepslate) | (sub == blocks.tuff) |
            (sub == blocks.cobbled_deepslate) | (sub == blocks.stone)) & inside_b
    if not rock.any():
        return

    band_lo = 0
    band_hi = nb - 1
    world_lo = band.start + MIN_Y
    world_hi = band.stop - 1 + MIN_Y

    for ore in cfg.ores:
        oid = blocks.ore(ore.name)
        y_lo_world = max(world_lo, int(getattr(ore, "y_min", world_lo)))
        y_hi_world = min(world_hi, int(getattr(ore, "y_max", world_hi)))

        # If the vanilla-ish configured range lives above this band, remap it
        # into the deep dark instead of skipping it. High-biased ores sit in the
        # upper half; low-biased ores sit lower; mid/uniform ores use the full
        # band.
        if y_hi_world < y_lo_world:
            bias = getattr(ore, "bias", "uniform")
            if bias == "high":
                y_lo_world = world_lo + int((world_hi - world_lo) * 0.46)
                y_hi_world = world_hi
            elif bias == "low":
                y_lo_world = world_lo
                y_hi_world = world_lo + int((world_hi - world_lo) * 0.58)
            else:
                y_lo_world = world_lo
                y_hi_world = world_hi

        y_lo = max(band_lo, y_lo_world - world_lo)
        y_hi = min(band_hi, y_hi_world - world_lo)
        if y_hi < y_lo:
            continue

        veins = int(rng.poisson(max(0.0, float(getattr(ore, "veins_per_chunk", 0.0))) * 0.90))
        for _ in range(veins):
            for attempt in range(12):
                x = int(rng.integers(0, 16))
                z = int(rng.integers(0, 16))
                if inside_b[0, z, x]:
                    break
            else:
                continue
            y = _sample_ore_y(rng, y_lo, y_hi, getattr(ore, "bias", "uniform"))
            _place_ore_blob(sub, x, y, z, getattr(ore, "vein_size", 5), oid, rng, replaceable)

    block_ids[band] = sub

def _deep_dark_stalactites(cfg, blocks, block_ids, band, inside_b, rng):
    sub = block_ids[band]
    nb = sub.shape[0]
    air = sub == blocks.air
    solid = sub != blocks.air
    roof = np.zeros_like(air)
    roof[1:] = solid[1:] & air[:-1]
    roof &= inside_b

    ys, zs, xs = np.nonzero(roof)
    for i in range(ys.size):
        yy, zz, xx = int(ys[i]), int(zs[i]), int(xs[i])
        # Sparse but chunky. Hash through rng/noise instead of placing every roof
        # cell, otherwise the deep dark becomes a curtain.
        if rng.random() > 0.035:
            continue
        length = int(rng.integers(2, 9))
        radius = 1 if length >= 5 and rng.random() < 0.40 else 0
        bid = blocks.tuff if rng.random() < 0.22 else blocks.deepslate
        for k in range(1, length + 1):
            y = yy - k
            if y < 0 or sub[y, zz, xx] != blocks.air:
                break
            sub[y, zz, xx] = bid
            if radius and k <= length // 2:
                for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    z2, x2 = zz + dz, xx + dx
                    if 0 <= z2 < 16 and 0 <= x2 < 16 and sub[y, z2, x2] == blocks.air and rng.random() < 0.42:
                        sub[y, z2, x2] = bid
    block_ids[band] = sub


def _sculk_pass(cfg, blocks, block_ids, band, inside_b, rng):
    s = cfg.seed
    sub = block_ids[band]
    nb = sub.shape[0]
    air = sub == blocks.air
    solid = (sub != blocks.air)
    # floor cells: solid with air directly above (within the band)
    floor = np.zeros_like(air)
    floor[:-1] = solid[:-1] & air[1:]
    floor &= inside_b

    # crust sculk on ~55% of floor cells, with sparse shriekers/sensors/catalysts
    fz, fzz, fxx = np.nonzero(floor)
    for i in range(fz.size):
        yy, zz, xx = fz[i], fzz[i], fxx[i]
        r = rng.random()
        if r < 0.55:
            sub[yy, zz, xx] = blocks.sculk
            above = yy + 1
            if above < nb and sub[above, zz, xx] == blocks.air:
                rr = rng.random()
                if rr < 0.008:
                    sub[above, zz, xx] = blocks.sculk_shrieker
                elif rr < 0.02:
                    sub[above, zz, xx] = blocks.sculk_sensor
                elif rr < 0.035:
                    sub[above, zz, xx] = blocks.sculk_catalyst
    block_ids[band] = sub


# ---------------------------------------------------------------------------
# Nether
# ---------------------------------------------------------------------------

def _fill_nether(cfg, blocks, block_ids, band, X3, Y3, Z3, inside_b,
                 yr, y_lo, y_hi, nbm, rng):
    s = cfg.seed
    sub = block_ids[band]
    nb = sub.shape[0]

    # Netherrack terrain: solid where 3D noise is dense; open caverns otherwise.
    d1 = noise.fbm3d(X3, Y3 * 1.6, Z3, s + 621, octaves=4, freq=1 / 34.0)
    d2 = noise.fbm3d(X3 - 800, Y3 * 1.6, Z3 + 800, s + 622, octaves=3, freq=1 / 34.0)
    dens = np.maximum(d1, d2)

    # Bias solid toward top/bottom so the layer has a roof and floor.
    ty = (Y3 - y_lo) / max(1, (y_hi - y_lo))             # 0 bottom .. 1 top
    edge = np.minimum(ty, 1.0 - ty)                       # 0 at edges, 0.5 middle
    thresh = 0.46 + 0.30 * (0.5 - edge) * 2.0             # higher near edges
    solid = inside_b & (dens > thresh)

    sub[:] = blocks.air
    sub[solid] = blocks.netherrack

    # Lava sea near the bottom.
    lava_top = y_lo + 4
    lava = inside_b & (~solid) & (Y3 <= lava_top)
    sub[lava] = blocks.lava

    # Ceiling cap: a couple netherrack rows at the very top so it reads enclosed.
    cap = inside_b & (Y3 >= y_hi - 1)
    sub[cap] = blocks.netherrack

    block_ids[band] = sub

    # Ore scatter (quartz, gold, ancient debris) in netherrack.
    _nether_ores(cfg, blocks, block_ids, band, inside_b, yr, y_lo, rng)

    # Biome skin on exposed surfaces + features.
    _nether_surface(cfg, blocks, block_ids, band, inside_b, nbm, rng)


def _nether_ores(cfg, blocks, block_ids, band, inside_b, yr, y_lo, rng):
    sub = block_ids[band]
    nb = sub.shape[0]
    rock = (sub == blocks.netherrack) & inside_b
    n = int(rock.sum())
    if n == 0:
        return
    roll = rng.random(sub.shape)
    sub[rock & (roll < 0.010)] = blocks.nether_quartz_ore
    sub[rock & (roll >= 0.010) & (roll < 0.016)] = blocks.nether_gold_ore
    # ancient debris only in the lower half, very rare
    low = np.zeros_like(rock)
    half = nb // 2
    low[:half] = True
    sub[rock & low & (roll >= 0.016) & (roll < 0.0168)] = blocks.ancient_debris
    block_ids[band] = sub


def _nether_surface(cfg, blocks, block_ids, band, inside_b, nbm, rng):
    """Skin exposed top surfaces per nether biome and scatter light features."""
    sub = block_ids[band]
    nb = sub.shape[0]
    air = sub == blocks.air
    solid = (sub != blocks.air) & (sub != blocks.lava)
    # top surface: solid with air above
    top = np.zeros_like(air)
    top[:-1] = solid[:-1] & air[1:]
    top &= inside_b

    tz, tzz, txx = np.nonzero(top)
    for i in range(tz.size):
        yy, zz, xx = tz[i], tzz[i], txx[i]
        if sub[yy, zz, xx] != blocks.netherrack:
            continue
        b = nbm[zz, xx]
        above = yy + 1
        if b == 1:      # crimson
            sub[yy, zz, xx] = blocks.crimson_nylium
            _maybe_stem(blocks, sub, yy, zz, xx, nb, rng, blocks.crimson_stem,
                        blocks.nether_wart_block, blocks.shroomlight, 0.03)
        elif b == 2:    # warped
            sub[yy, zz, xx] = blocks.warped_nylium
            _maybe_stem(blocks, sub, yy, zz, xx, nb, rng, blocks.warped_stem,
                        blocks.warped_wart_block, blocks.shroomlight, 0.03)
        elif b == 3:    # soul sand valley
            sub[yy, zz, xx] = blocks.soul_sand if rng.random() < 0.6 else blocks.soul_soil
        elif b == 4:    # basalt deltas
            r = rng.random()
            sub[yy, zz, xx] = (blocks.basalt if r < 0.5 else
                               blocks.blackstone if r < 0.85 else blocks.magma)
        else:           # nether wastes: occasional glowstone/magma accents
            if rng.random() < 0.01 and above < nb:
                sub[above, zz, xx] = blocks.glowstone
    block_ids[band] = sub


def _maybe_stem(blocks, sub, yy, zz, xx, nb, rng, stem_id, cap_id, light_id, chance):
    if rng.random() >= chance:
        return
    h = rng.integers(3, 9)
    for k in range(1, h + 1):
        y = yy + k
        if y >= nb:
            break
        if sub[y, zz, xx] == blocks.air:
            sub[y, zz, xx] = stem_id
    capy = min(nb - 1, yy + h)
    # small cap of wart/shroomlight
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            z2, x2 = zz + dz, xx + dx
            if 0 <= z2 < 16 and 0 <= x2 < 16 and capy < nb and sub[capy, z2, x2] == blocks.air:
                sub[capy, z2, x2] = light_id if (dz == 0 and dx == 0) else cap_id
