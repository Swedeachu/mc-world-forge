"""
Underworld layers for the vanilla generator: below the overworld caverns the
world can continue down through a Deep Dark band and then a Nether band, so a
player can mine/cave straight down into "hell" (Terraria-style, but 3D).

This is an original implementation (not copied from Minecraft's source); it just
uses the same block ids so the result reads correctly in-game. All work happens
on the chunk block array that generator.generate_chunk already built, rewriting
everything at or below cfg.floor_y (which used to be a bedrock floor + void).

Layer stack (compact mode), from the overworld floor downward:
    [ overworld stone ]         (untouched, above cfg.floor_y)
    [ Deep Dark ]  deepslate / tuff / sculk, carved caverns   (optional)
    [ Nether ]     netherrack caverns, lava sea, nether biomes (optional)
    [ bedrock floor at MIN_Y ]

Nether biomes are chosen per-column from low-frequency noise and used to skin the
exposed netherrack (nylium, soul sand, basalt, ...) and scatter features.
"""
import numpy as np

from . import noise

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
    """Return [(name, y_hi, y_lo)] inclusive bands, top-down, plus bedrock at MIN_Y."""
    top = cfg.floor_y
    segs = []
    cur_hi = top
    if cfg.deep_dark:
        deep_dark_height = int(getattr(cfg, "deep_dark_height", 0) or (cur_hi - MIN_Y))
        lo = max(MIN_Y + 1, cur_hi - deep_dark_height + 1)
        if lo <= cur_hi:
            segs.append(("deep_dark", cur_hi, lo))
            cur_hi = lo - 1
    if cfg.nether:
        lo = MIN_Y + 1
        if lo <= cur_hi:
            segs.append(("nether", cur_hi, lo))
            cur_hi = lo - 1
    return segs


def layer_starts(cfg):
    """Top (entry) Y of each underworld band, or None if disabled."""
    out = {"deep_dark": None, "nether": None}
    for name, y_hi, y_lo in _segments(cfg):
        out[name] = int(y_hi)
    return out


def apply_deep_dark_palette(cfg, blocks, block_ids, X, Z, inside, rng):
    """Apply the deep-dark material style without changing cave geometry.

    The generator now carves deep-dark space with the exact same cavern pass used
    for the normal cave layer. This function only repaints the lower rocks and
    sculk floors, so the cavern layer above keeps its normal shape and flows
    naturally into the deeper palette instead of hitting a custom underworld
    shell.
    """
    world_h = block_ids.shape[0]
    y_world = np.arange(world_h) + MIN_Y
    inside3 = inside[None, :, :]
    y3 = y_world[:, None, None].astype(np.float64)
    X3 = X[None, :, :].astype(np.float64)
    Z3 = Z[None, :, :].astype(np.float64)

    deep_top = int(cfg.floor_y + 3)
    transition_top = int(cfg.floor_y + 10)
    transition = np.clip((transition_top - y3) / max(1.0, float(transition_top - deep_top + 7)), 0.0, 1.0)
    deep_zone = inside3 & (y3 <= transition_top)

    rock = ((block_ids == blocks.stone) | (block_ids == blocks.andesite) |
            (block_ids == blocks.diorite) | (block_ids == blocks.granite) |
            (block_ids == blocks.gravel))
    chance_noise = noise.fbm3d(X3 + 3100, y3 * 0.8, Z3 - 3100,
                               cfg.seed + 661, octaves=2, freq=1 / 24.0)
    convert = deep_zone & rock & ((y3 <= deep_top) | (chance_noise < transition))

    tuff = noise.fbm3d(X3, y3, Z3, cfg.seed + 662, octaves=3, freq=1 / 13.5)
    cob = noise.fbm3d(X3 - 900, y3 * 1.15, Z3 + 900, cfg.seed + 663, octaves=2, freq=1 / 9.5)
    block_ids[convert] = blocks.deepslate
    block_ids[convert & (tuff > 0.70)] = blocks.tuff
    block_ids[convert & (cob > 0.76)] = blocks.cobbled_deepslate

    # Sculk only marks natural cave floors. No sensors/shriekers/catalysts are
    # placed here, and no geometry is carved here.
    deep_blocks = ((block_ids == blocks.deepslate) | (block_ids == blocks.tuff) |
                   (block_ids == blocks.cobbled_deepslate) | (block_ids == blocks.stone))
    air = block_ids == blocks.air
    floor = np.zeros_like(air, dtype=bool)
    floor[:-1] = deep_blocks[:-1] & air[1:]
    sculk_noise = noise.fbm3d(X3 + 5300, y3 * 0.9, Z3 - 5300,
                              cfg.seed + 664, octaves=2, freq=1 / 19.0)
    sculk = floor & deep_zone & (y3 <= cfg.floor_y + 8) & (sculk_noise > 0.40) & (rng.random(block_ids.shape) < 0.68)
    block_ids[sculk] = blocks.sculk


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
    """Rewrite the chunk's block column at/below cfg.floor_y into the layers.

    Returns a per-column nether biome id map ([z,x], -1 where no nether)."""
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
        else:
            nbm = nether_biome_map(cfg, X, Z)
            neth_biome = np.where(inside, nbm, neth_biome)
            _fill_nether(cfg, blocks, block_ids, band, X3, Y3, Z3, inside_b,
                         yr, y_lo, y_hi, nbm, rng)

    _connect_caves(cfg, blocks, block_ids, inside, segs)
    return neth_biome


def _connect_caves(cfg, blocks, block_ids, inside, segs):
    """Drill open connections so overworld caves flow straight into the deep dark
    (and the deep dark into the nether) instead of dead-ending on a solid floor.

    Overworld caves bottom out a couple blocks above floor_y, leaving a thin solid
    seal. For every column with a cave near the floor we punch from that cave air
    down through the seal until we meet an open chamber in the layer below."""
    if not segs:
        return
    top = segs[0][1]            # entry Y of the first underworld band (== floor_y)
    bottom = segs[-1][2]        # lowest fillable Y (just above bedrock floor)
    air = blocks.air
    stop = {blocks.bedrock, blocks.lava}
    zz, xx = np.nonzero(inside)
    for i in range(zz.size):
        z, x = int(zz[i]), int(xx[i])
        # lowest overworld cave air just above the layer (floor_y+1 .. floor_y+8)
        cave_y = None
        for y in range(top + 1, top + 9):
            if int(block_ids[y - MIN_Y, z, x]) == air:
                cave_y = y
                break
        if cave_y is None:
            continue
        # carve down from the cave through the seal until we reach a chamber
        y = cave_y - 1
        drilled = 0
        while y > bottom and drilled < 30:
            b = int(block_ids[y - MIN_Y, z, x])
            if b == air:
                break                       # reached an existing cavern -> connected
            if b in stop:
                break
            block_ids[y - MIN_Y, z, x] = air
            y -= 1
            drilled += 1


# ---------------------------------------------------------------------------
# Deep Dark
# ---------------------------------------------------------------------------

def _fill_deep_dark(cfg, blocks, block_ids, band, X3, Y3, Z3, inside_b, rng):
    s = cfg.seed
    sub = block_ids[band]
    nb = sub.shape[0]

    # Solid deepslate everywhere first.
    sub[inside_b] = blocks.deepslate

    # Tuff veins and cobbled patches.
    tuff = noise.fbm3d(X3 * 1.0, Y3 * 1.0, Z3 * 1.0, s + 611, octaves=3, freq=1 / 12.0)
    sub[inside_b & (tuff > 0.68)] = blocks.tuff
    cob = noise.fbm3d(X3, Y3, Z3, s + 612, octaves=2, freq=1 / 9.0)
    sub[inside_b & (cob > 0.72)] = blocks.cobbled_deepslate

    # Blend the top of the band into overworld stone without leaving the old
    # floor as a perfectly flat cave ceiling. The top few rows remain mostly
    # solid and get cut by noisy caves/connectors below, so the visible roof is
    # a natural density surface instead of the previous layer's flat boundary.
    rows = np.arange(nb)[:, None, None]
    depth_from_bottom = rows.astype(np.float64)
    depth_from_top = ((nb - 1) - rows).astype(np.float64)
    p_stone = np.clip(1.0 - depth_from_top / 7.0, 0.0, 1.0)
    rs = rng.random(sub.shape)
    mix = inside_b & (sub == blocks.deepslate) & (rs < p_stone * 0.62)
    sub[mix] = blocks.stone

    # Modern-style cave mix: a broad cheese/room field plus spaghetti/noodle
    # tunnels, but with noisy top and bottom caps. The caps are important: they
    # stop the old overworld floor from becoming a flat ceiling and stop caverns
    # from exposing the bedrock row as a walkable floor.
    cheese = noise.fbm3d(X3, Y3 * 0.82, Z3, s + 613, octaves=4, freq=1 / 42.0)
    room = noise.fbm3d(X3 - 900, Y3 * 0.72, Z3 + 900, s + 614, octaves=3, freq=1 / 74.0)
    spaghetti_a = 1.0 - np.abs(noise.fbm3d(X3 + 1400, Y3 * 1.10, Z3 - 1400, s + 615, octaves=3, freq=1 / 33.0) - 0.5) * 2.0
    spaghetti_b = 1.0 - np.abs(noise.fbm3d(X3 - 2600, Y3 * 1.05, Z3 + 2600, s + 616, octaves=3, freq=1 / 29.0) - 0.5) * 2.0
    noodle = 1.0 - np.abs(noise.fbm3d(X3 + 4100, Y3 * 1.35, Z3 + 1700, s + 617, octaves=2, freq=1 / 18.0) - 0.5) * 2.0
    vault = noise.fbm3d(X3 - 5800, Y3 * 0.55, Z3 - 2300, s + 618, octaves=3, freq=1 / 96.0)

    cave_room = (cheese > 0.535) & (room > 0.375)
    cave_vault = (vault > 0.59) & (cheese > 0.455)
    cave_tunnel = ((spaghetti_a > 0.79) & (room > 0.285)) | ((spaghetti_b > 0.805) & (cheese > 0.355))
    cave_noodle = (noodle > 0.855) & ((cheese > 0.335) | (room > 0.335))
    open_raw = cave_room | cave_vault | cave_tunnel | cave_noodle

    roof_noise = noise.fbm3d(X3 + 8100, Y3 * 0.75, Z3 - 8100, s + 619, octaves=3, freq=1 / 38.0)
    floor_noise = noise.fbm3d(X3 - 7300, Y3 * 0.85, Z3 + 7300, s + 620, octaves=3, freq=1 / 34.0)
    roof_cap = 3.5 + roof_noise * 8.5
    floor_cap = 7.0 + floor_noise * 6.0
    roof_fade = np.clip((depth_from_top - roof_cap) / 12.0, 0.0, 1.0)
    floor_fade = np.clip((depth_from_bottom - floor_cap) / 9.0, 0.0, 1.0)
    edge_fade = roof_fade * floor_fade

    air_mask = inside_b & open_raw & (edge_fade > 0.10)
    sub[air_mask] = blocks.air

    # A few broad lower shelves keep caves from degenerating into bedrock-floor
    # stadiums while still leaving the layer open and cavern-like.
    shelf_noise = noise.fbm3d(X3 + 9900, Y3 * 0.55, Z3 - 4700, s + 624, octaves=3, freq=1 / 68.0)
    shelf = inside_b & (sub == blocks.air) & (depth_from_bottom < 18.0 + shelf_noise * 8.0) & (shelf_noise > 0.42)
    sub[shelf] = blocks.deepslate

    block_ids[band] = sub

    # Sculk crust on cavern floors only. Do not place sensors, shriekers or
    # catalysts in generated deep dark caves; they looked noisy and are not
    # wanted for this world style.
    _sculk_pass(cfg, blocks, block_ids, band, inside_b, rng)


def _sculk_pass(cfg, blocks, block_ids, band, inside_b, rng):
    sub = block_ids[band]
    air = sub == blocks.air
    solid = (sub != blocks.air)

    # floor cells: solid with air directly above (within the band)
    floor = np.zeros_like(air)
    floor[:-1] = solid[:-1] & air[1:]
    floor &= inside_b

    crust = floor & (rng.random(sub.shape) < 0.58)
    sub[crust] = blocks.sculk
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
