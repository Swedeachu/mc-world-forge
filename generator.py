"""
Core world generation.

Produces a Minecraft Java Edition save folder:
  <out>/level.dat
  <out>/region/r.*.*.mca

Terrain is generated directly (numpy noise), so the world is finite, centred on
(0,0), walled in bedrock, with a bedrock floor above void and configurable ore
veins / caves / biomes. Blocks use the plain "1.12-style" palette (stone +
diamond_ore rather than deepslate) even below y=0, but the overall shape aims to
feel like natural vanilla generation: blended temperature/humidity biomes,
gentle river/lake banks with beaches, clay and gravel beds, stone variants
(diorite/granite/andesite), open ravines, underground fluids, and surface
decoration (grass, flowers, sugar cane, cactus).
"""
import os
import time
import multiprocessing as mp
import queue
import threading
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Callable, Optional

import nbt
import noise
from region import RegionWriter, build_chunk

# Fixed vertical layout (standard overworld dimension range).
MIN_Y = -64
WORLD_HEIGHT = 384          # -64 .. 319
MIN_SECTION = MIN_Y // 16   # -4
N_SECTIONS = WORLD_HEIGHT // 16  # 24


# ---------------------------------------------------------------------------
# Block registry
# ---------------------------------------------------------------------------

class Registry:
    def __init__(self):
        self.list = []
        self.index = {}

    def get(self, key):
        if key not in self.index:
            self.index[key] = len(self.list)
            self.list.append(key)
        return self.index[key]


def _bkey(name, props=None):
    return (name, tuple(sorted((props or {}).items())))


class Blocks:
    def __init__(self, data_version=3465):
        self.reg = Registry()
        g = self.reg.get
        # id 0 MUST be air (heightmap / preview logic relies on it)
        self.air = g(_bkey("minecraft:air"))
        self.bedrock = g(_bkey("minecraft:bedrock"))
        self.stone = g(_bkey("minecraft:stone"))
        self.dirt = g(_bkey("minecraft:dirt"))
        self.coarse_dirt = g(_bkey("minecraft:coarse_dirt"))
        self.grass = g(_bkey("minecraft:grass_block"))
        self.grass_snow = g(_bkey("minecraft:grass_block", {"snowy": "true"}))
        self.sand = g(_bkey("minecraft:sand"))
        self.sandstone = g(_bkey("minecraft:sandstone"))
        self.gravel = g(_bkey("minecraft:gravel"))
        self.clay = g(_bkey("minecraft:clay"))
        self.water = g(_bkey("minecraft:water"))
        self.lava = g(_bkey("minecraft:lava"))
        self.snow_layer = g(_bkey("minecraft:snow", {"layers": "1"}))
        self.snow_block = g(_bkey("minecraft:snow_block"))
        self.podzol = g(_bkey("minecraft:podzol"))
        self.diorite = g(_bkey("minecraft:diorite"))
        self.granite = g(_bkey("minecraft:granite"))
        self.andesite = g(_bkey("minecraft:andesite"))

        # plants -- short grass was renamed grass -> short_grass in 1.20.3 (3698)
        sg = "minecraft:short_grass" if data_version >= 3698 else "minecraft:grass"
        self.short_grass = g(_bkey(sg))
        self.fern = g(_bkey("minecraft:fern"))
        self.tall_grass_lo = g(_bkey("minecraft:tall_grass", {"half": "lower"}))
        self.tall_grass_hi = g(_bkey("minecraft:tall_grass", {"half": "upper"}))
        self.dead_bush = g(_bkey("minecraft:dead_bush"))
        self.cactus = g(_bkey("minecraft:cactus"))
        self.sugar_cane = g(_bkey("minecraft:sugar_cane"))
        self.flowers = [g(_bkey(f"minecraft:{n}")) for n in (
            "dandelion", "poppy", "cornflower", "azure_bluet", "oxeye_daisy")]

        self._logs = {}
        self._leaves = {}
        self.ore_ids = {}

    def log(self, kind):
        if kind not in self._logs:
            self._logs[kind] = self.reg.get(_bkey(f"minecraft:{kind}_log", {"axis": "y"}))
        return self._logs[kind]

    def leaves(self, kind):
        if kind not in self._leaves:
            self._leaves[kind] = self.reg.get(
                _bkey(f"minecraft:{kind}_leaves", {"persistent": "true"}))
        return self._leaves[kind]

    def ore(self, name):
        if name not in self.ore_ids:
            n = name if ":" in name else f"minecraft:{name}"
            self.ore_ids[name] = self.reg.get(_bkey(n))
        return self.ore_ids[name]

    def palette(self):
        return [(name, dict(props)) for (name, props) in self.reg.list]


# Biome ids (strings). Colours used for the preview map.
BIOME_COLORS = {
    "minecraft:plains": (141, 179, 96),
    "minecraft:forest": (75, 125, 58),
    "minecraft:taiga": (81, 122, 100),
    "minecraft:jungle": (89, 158, 42),
    "minecraft:desert": (219, 205, 141),
    "minecraft:beach": (224, 214, 166),
    "minecraft:snowy_plains": (236, 244, 250),
    "minecraft:snowy_taiga": (198, 216, 208),
    "minecraft:snowy_slopes": (223, 231, 240),
    "minecraft:river": (72, 118, 178),
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class OreCfg:
    name: str = "diamond_ore"
    y_min: int = -19
    y_max: int = 5
    veins_per_chunk: float = 7.0
    vein_size: int = 5
    bias: str = "uniform"      # low | mid | high | uniform (depth clustering)


def _default_ores():
    # Grounded in 1.20 vanilla y-levels, adapted to this shallow world.
    return [
        OreCfg("coal_ore", 5, 120, 11.0, 12, "high"),
        OreCfg("iron_ore", -19, 80, 9.0, 8, "mid"),
        OreCfg("copper_ore", 0, 96, 6.0, 8, "mid"),
        OreCfg("gold_ore", -19, 32, 3.0, 6, "low"),
        OreCfg("redstone_ore", -19, 15, 7.0, 7, "low"),
        OreCfg("lapis_ore", -19, 32, 2.5, 6, "mid"),
        OreCfg("diamond_ore", -19, 8, 7.0, 5, "low"),
    ]


@dataclass
class WorldConfig:
    name: str = "Forged World"
    size: int = 1000
    seed: int = 0
    data_version: int = 3465          # 1.20.1
    version_name: str = "1.20.1"

    floor_y: int = -20                # bedrock floor; void below
    water_level: int = 62
    base_height: int = 71
    hill_amp: float = 12.0
    mountain_amp: float = 70.0
    mountain_amount: float = 0.5       # 0..1, higher => more/higher mountains
    snowline_y: int = 95

    caves: bool = True
    cave_intensity: float = 1.0
    ravines: bool = True
    fluids: bool = True                # underground lava/water pockets

    rivers: bool = True
    river_amount: float = 0.4          # 0..1
    lakes: bool = True
    lake_amount: float = 0.4           # 0..1

    trees: bool = True
    tree_density: float = 1.0
    decorations: bool = True           # grass, flowers, sugar cane, cactus
    stone_variants: bool = True        # diorite / granite / andesite / dirt / gravel

    wall: bool = True
    wall_min_y: int = MIN_Y
    wall_max_y: int = 319

    game_type: int = 0                # 0 survival, 1 creative
    allow_commands: bool = True

    ores: List[OreCfg] = field(default_factory=_default_ores)


# ---------------------------------------------------------------------------
# Palette preparation / worker planning
# ---------------------------------------------------------------------------

def _prepare_palettes(cfg: WorldConfig):
    """Create stable block and biome palettes before workers generate chunks."""
    blocks = Blocks(cfg.data_version)

    # These are normally registered lazily by tree placement. Pre-registering
    # makes worker output independent from generation order.
    for kind in ("oak", "spruce", "jungle"):
        blocks.log(kind)
        blocks.leaves(kind)

    # Ores are user-configurable, so register the active set in config order.
    for ore in cfg.ores:
        blocks.ore(ore.name)

    biome_reg = Registry()
    for biome in BIOME_COLORS:
        biome_reg.get(biome)
    biome_reg.get("minecraft:plains")

    return blocks, biome_reg


def _chunk_rng_seed(seed, cx, cz):
    return (seed & 0xFFFFFFFF) ^ ((cx & 0xFFFF) << 8) ^ \
           ((cz & 0xFFFF) << 20) ^ 0x9E3779B1


def _resolve_worker_count(requested, total_regions, total_chunks):
    if requested is None:
        requested = min(os.cpu_count() or 1, 12)
    requested = max(1, int(requested))

    # Tiny worlds are faster without process startup / pickle overhead.
    if total_chunks < 256 or total_regions <= 1:
        return 1
    return max(1, min(requested, total_regions))


def _region_jobs(cxmin, cxmax, czmin, czmax):
    rxmin, rxmax = cxmin >> 5, cxmax >> 5
    rzmin, rzmax = czmin >> 5, czmax >> 5
    for rz in range(rzmin, rzmax + 1):
        for rx in range(rxmin, rxmax + 1):
            jcxmin = max(cxmin, rx << 5)
            jcxmax = min(cxmax, (rx << 5) + 31)
            jczmin = max(czmin, rz << 5)
            jczmax = min(czmax, (rz << 5) + 31)
            if jcxmin <= jcxmax and jczmin <= jczmax:
                yield rx, rz, jcxmin, jcxmax, jczmin, jczmax


def _preview_patch_rect(cfg, xmin, xmax, pscale, preview_size, cxmin, cxmax, czmin, czmax):
    wx0 = max(xmin, cxmin * 16)
    wx1 = min(xmax, cxmax * 16 + 15)
    wz0 = max(xmin, czmin * 16)
    wz1 = min(xmax, czmax * 16 + 15)

    px0 = max(0, min(preview_size - 1, (wx0 - xmin) // pscale))
    px1 = max(0, min(preview_size - 1, (wx1 - xmin) // pscale))
    pz0 = max(0, min(preview_size - 1, (wz0 - xmin) // pscale))
    pz1 = max(0, min(preview_size - 1, (wz1 - xmin) // pscale))

    return px0, pz0, px1 - px0 + 1, pz1 - pz0 + 1


# ---------------------------------------------------------------------------
# Height / biome / water fields (per column)
# ---------------------------------------------------------------------------

def _coord_grids(cx, cz):
    xs = cx * 16 + np.arange(16)
    zs = cz * 16 + np.arange(16)
    X, Z = np.meshgrid(xs, zs)   # [z, x]
    return X.astype(np.float64), Z.astype(np.float64)


def _fields(cfg, blocks, X, Z):
    """Return everything the column needs. All arrays are [z, x]."""
    s = cfg.seed
    wl = cfg.water_level

    # --- base terrain height -------------------------------------------
    hills = (noise.fbm2d(X, Z, s + 1, octaves=4, freq=1 / 70.0) - 0.5) * 2.0
    detail = (noise.fbm2d(X, Z, s + 11, octaves=3, freq=1 / 22.0) - 0.5) * 2.0
    mnt_mask = noise.fbm2d(X, Z, s + 2, octaves=4, freq=1 / 320.0)
    # sharp remap so the rare high-mask areas become genuine tall peaks
    lo = 0.66 - 0.14 * cfg.mountain_amount
    mnt = np.clip((mnt_mask - lo) / (0.83 - lo), 0.0, 1.0) ** 1.8
    ridge = 1.0 - np.abs(noise.fbm2d(X, Z, s + 12, octaves=3, freq=1 / 110.0) - 0.5) * 2.0
    Hf = (cfg.base_height
          + hills * cfg.hill_amp
          + detail * 3.0
          + mnt * cfg.mountain_amp
          + (mnt ** 1.3) * ridge * cfg.mountain_amp * 0.5)

    # --- climate --------------------------------------------------------
    # Widened around 0.5 so the tails (frozen / hot desert) actually appear,
    # and frequent enough that a ~1000-wide world spans several biome zones.
    tw = noise.fbm2d(X + 1000, Z - 1000, s + 3, octaves=3, freq=1 / 340.0)
    tw = 0.5 + (tw - 0.5) * 1.9
    tw += (noise.fbm2d(X, Z, s + 31, octaves=2, freq=1 / 90.0) - 0.5) * 0.18
    hw = noise.fbm2d(X - 2000, Z + 2000, s + 4, octaves=3, freq=1 / 300.0)
    hw = 0.5 + (hw - 0.5) * 1.8
    hw += (noise.fbm2d(X, Z, s + 41, octaves=2, freq=1 / 85.0) - 0.5) * 0.18
    # cooler at altitude
    temp = np.clip(tw, 0.0, 1.0) - np.clip((Hf - wl) / 420.0, 0.0, 0.22)
    hum = np.clip(hw, 0.0, 1.0)

    frozen = temp < 0.25
    cold = (temp >= 0.25) & (temp < 0.45)
    temperate = (temp >= 0.45) & (temp < 0.67)
    warm = temp >= 0.67

    biome = np.full(X.shape, "minecraft:plains", dtype=object)
    biome[frozen & (hum < 0.45)] = "minecraft:snowy_plains"
    biome[frozen & (hum >= 0.45)] = "minecraft:snowy_taiga"
    biome[cold & (hum >= 0.55)] = "minecraft:taiga"
    biome[cold & (hum >= 0.45) & (hum < 0.55)] = "minecraft:forest"
    biome[cold & (hum < 0.45)] = "minecraft:plains"
    # temperate: roughly half plains / half forest by humidity median
    biome[temperate & (hum >= 0.45)] = "minecraft:forest"
    biome[temperate & (hum < 0.45)] = "minecraft:plains"
    # warm: dry deserts, only the very wettest go jungle, rest is plains
    biome[warm] = "minecraft:plains"
    biome[warm & (hum < 0.45)] = "minecraft:desert"
    biome[warm & (hum >= 0.74)] = "minecraft:jungle"

    is_desert = biome == "minecraft:desert"

    # --- mountains: snowy slopes only on genuinely high / cold peaks ----
    high = mnt > 0.45
    snowy_peak = high & ((Hf >= cfg.snowline_y) | frozen) & ~is_desert
    biome[snowy_peak] = "minecraft:snowy_slopes"

    # --- rivers (winding, gated so they don't span everything) ----------
    is_water = np.zeros(X.shape, dtype=bool)
    is_river = np.zeros(X.shape, dtype=bool)
    depress = np.zeros(X.shape, dtype=np.float64)  # 0..1 downward carve factor

    if cfg.rivers:
        rn = noise.fbm2d(X, Z, s + 5, octaves=2, freq=1 / 240.0)
        gate = noise.fbm2d(X + 4000, Z + 4000, s + 51, octaves=2, freq=1 / 520.0)
        halfw = 0.018 + 0.020 * cfg.river_amount
        d = np.abs(rn - 0.5)
        rf = np.clip(1.0 - d / halfw, 0.0, 1.0)          # 1 at centre
        rf = rf * np.clip((gate - 0.40) / 0.30, 0.0, 1.0)  # only in some regions
        rf[high] = 0.0                                     # no rivers up mountains
        rf = rf ** 0.6
        depress = np.maximum(depress, rf)
        is_river = rf > 0.15

    if cfg.lakes:
        # small ponds: higher frequency = smaller features, sparse gate
        ln = noise.fbm2d(X + 500, Z + 500, s + 6, octaves=2, freq=1 / 46.0)
        lgate = noise.fbm2d(X - 700, Z + 900, s + 61, octaves=2, freq=1 / 220.0)
        cut = 0.12 + 0.05 * cfg.lake_amount
        lf = np.clip((cut - ln) / 0.10, 0.0, 1.0)
        lf = lf * np.clip((lgate - 0.45) / 0.20, 0.0, 1.0)
        lf[high] = 0.0
        depress = np.maximum(depress, lf)

    # carve terrain smoothly toward a bed (gentle banks, no cliffs)
    bed = wl - (2.0 + 3.0 * depress)
    lowered = Hf * (1.0 - depress) + bed * depress
    Hf = np.minimum(Hf, lowered)

    H = np.clip(Hf, cfg.floor_y + 3, MIN_Y + WORLD_HEIGHT - 40).astype(np.int32)

    # water wherever the surface sits below sea level
    is_water = H <= wl - 1
    is_river = is_river & is_water
    biome[is_river] = "minecraft:river"

    # beaches: shoreline band on land next to water (sand ring)
    is_beach = (~is_water) & (H <= wl + 1) & (H >= wl - 2) & ~is_desert
    biome[is_beach & (biome != "minecraft:snowy_plains") &
          (biome != "minecraft:snowy_taiga") & (biome != "minecraft:snowy_slopes")] = "minecraft:beach"

    # --- snow ----------------------------------------------------------
    is_snowy = (~is_desert) & (~is_water) & ((H >= cfg.snowline_y) | frozen)

    # --- top + subsurface block per column -----------------------------
    top = np.full(X.shape, blocks.grass, dtype=np.int32)
    sub = np.full(X.shape, blocks.dirt, dtype=np.int32)

    top[biome == "minecraft:taiga"] = blocks.podzol
    top[biome == "minecraft:snowy_taiga"] = blocks.grass_snow
    top[(biome == "minecraft:snowy_plains")] = blocks.grass_snow
    top[biome == "minecraft:snowy_slopes"] = blocks.snow_block
    sub[biome == "minecraft:snowy_slopes"] = blocks.stone
    top[is_snowy & (top == blocks.grass)] = blocks.grass_snow

    top[is_desert] = blocks.sand
    sub[is_desert] = blocks.sandstone
    top[is_beach] = blocks.sand
    sub[is_beach] = blocks.sandstone

    return {
        "H": H, "biome": biome, "top": top, "sub": sub,
        "is_water": is_water, "is_river": is_river, "is_beach": is_beach,
        "is_desert": is_desert, "is_snowy": is_snowy, "mnt": mnt,
    }


# ---------------------------------------------------------------------------
# Chunk generation
# ---------------------------------------------------------------------------

def generate_chunk(cfg, blocks, biome_reg, cx, cz, xmin, xmax, rng_seed):
    X, Z = _coord_grids(cx, cz)                    # [z, x]
    zz, xx = np.meshgrid(np.arange(16), np.arange(16), indexing="ij")

    inside = (X >= xmin) & (X <= xmax) & (Z >= xmin) & (Z <= xmax)
    on_wall = inside & ((X == xmin) | (X == xmax) | (Z == xmin) | (Z == xmax))

    f = _fields(cfg, blocks, X, Z)
    H = f["H"]; biome_str = f["biome"]; top = f["top"]; sub = f["sub"]
    is_water = f["is_water"]; is_river = f["is_river"]; is_beach = f["is_beach"]
    is_desert = f["is_desert"]; is_snowy = f["is_snowy"]

    block_ids = np.zeros((WORLD_HEIGHT, 16, 16), dtype=np.int16)  # [y, z, x]
    world_y = (np.arange(WORLD_HEIGHT) + MIN_Y).reshape(-1, 1, 1)
    inside3 = inside[None, :, :]
    Hs = H[None, :, :]
    floor = cfg.floor_y
    rng = np.random.default_rng(rng_seed)

    # --- columns: bedrock floor, stone body, subsurface, top -----------
    block_ids[np.broadcast_to(inside3 & (world_y == floor), block_ids.shape)] = blocks.bedrock
    block_ids[inside3 & (world_y > floor) & (world_y <= Hs - 4)] = blocks.stone
    sub3 = np.broadcast_to(sub[None, :, :], block_ids.shape)
    dirt_band = inside3 & (world_y >= Hs - 3) & (world_y <= Hs)
    block_ids[dirt_band] = sub3[dirt_band]

    # --- stone variants + dirt/gravel pods (replace stone) -------------
    if cfg.stone_variants:
        variant_specs = [
            (blocks.andesite, 3, 30), (blocks.diorite, 2, 30),
            (blocks.granite, 2, 30), (blocks.dirt, 2, 18), (blocks.gravel, 2, 14),
        ]
        for vid, cnt, size in variant_specs:
            for _ in range(int(rng.poisson(cnt))):
                ox, oz = int(rng.integers(0, 16)), int(rng.integers(0, 16))
                if not inside[oz, ox]:
                    continue
                surf = int(H[oz, ox])
                lo = floor + 1
                hi = min(surf - 2, cfg.snowline_y)
                if hi <= lo:
                    continue
                oy = int(rng.integers(lo, hi + 1))
                _place_blob(block_ids, ox, oy, oz, size, vid, rng, inside,
                            (blocks.stone,))

    # --- caves ----------------------------------------------------------
    if cfg.caves:
        _carve_caves(cfg, blocks, block_ids, X, Z, H, inside, floor)
    if cfg.ravines:
        _carve_ravines(cfg, blocks, block_ids, X, Z, H, inside, on_wall,
                       world_y, floor)

    # --- surface top block (land) --------------------------------------
    land = inside & ~on_wall & ~is_water & (H > floor + 3)
    arr_surf = (H - MIN_Y)
    fx, fz, fy = xx[land], zz[land], arr_surf[land]
    # only set if the cave/ravine didn't open the surface to sky
    cur = block_ids[fy, fz, fx]
    keep = cur != blocks.air
    block_ids[fy[keep], fz[keep], fx[keep]] = top[land][keep].astype(np.int16)

    # --- water bodies: beds (sand/gravel/clay) + fill ------------------
    if is_water.any():
        gnoise = noise.fbm2d(X + 20, Z - 20, cfg.seed + 71, octaves=2, freq=1 / 14.0)
        cnoise = noise.fbm2d(X - 30, Z + 30, cfg.seed + 72, octaves=2, freq=1 / 12.0)
        bed_id = np.full(X.shape, blocks.sand, dtype=np.int16)
        bed_id[gnoise > 0.60] = blocks.gravel
        bed_id[cnoise > 0.60] = blocks.clay
        wcol = inside & ~on_wall & is_water
        by, bz, bx = arr_surf[wcol], zz[wcol], xx[wcol]
        block_ids[by, bz, bx] = bed_id[wcol]
        # clay/sand a couple blocks thick under the bed
        for dd in (1, 2):
            yb = arr_surf - dd
            m = wcol & (yb >= 0)
            yy2 = yb[m]
            block_ids[yy2, zz[m], xx[m]] = np.where(
                bed_id[m] == blocks.clay, blocks.clay, block_ids[yy2, zz[m], xx[m]])
        wl = cfg.water_level
        water_fill = inside3 & is_water[None, :, :] & (world_y > Hs) & (world_y <= wl)
        block_ids[water_fill & (block_ids == 0)] = blocks.water

    # --- underground fluids (lava aquifer + water pockets) -------------
    if cfg.fluids:
        _place_fluids(cfg, blocks, block_ids, inside, world_y, floor, rng)

    # --- ores (replace stone or a stone variant) -----------------------
    replaceable = (blocks.stone, blocks.andesite, blocks.diorite,
                   blocks.granite)
    for ore in cfg.ores:
        oid = blocks.ore(ore.name)
        y_lo = max(floor + 1, ore.y_min)
        y_hi = ore.y_max
        for _ in range(int(rng.poisson(max(0.0, ore.veins_per_chunk)))):
            ox, oz = int(rng.integers(0, 16)), int(rng.integers(0, 16))
            if not inside[oz, ox]:
                continue
            hi = min(y_hi, int(H[oz, ox]) - 2)
            if hi < y_lo:
                continue
            oy = _sample_y(rng, y_lo, hi, ore.bias)
            _place_blob(block_ids, ox, oy, oz, ore.vein_size, oid, rng, inside,
                        replaceable)

    # --- trees ----------------------------------------------------------
    if cfg.trees:
        _place_trees(cfg, blocks, block_ids, H, biome_str, is_water, inside,
                     on_wall, rng)

    # --- surface decoration (grass, flowers, sugar cane, cactus) -------
    if cfg.decorations:
        _decorate(cfg, blocks, block_ids, H, biome_str, is_water, is_desert,
                  is_snowy, inside, on_wall, rng)

    # --- snow layer -----------------------------------------------------
    if is_snowy.any():
        cols = inside & ~on_wall & is_snowy & ~is_water & (H > floor + 3)
        sy = (H - MIN_Y) + 1
        valid = cols & (sy < WORLD_HEIGHT)
        syy, szz, sxx = sy[valid], zz[valid], xx[valid]
        below = block_ids[syy - 1, szz, sxx]
        ok = (block_ids[syy, szz, sxx] == 0) & (below != 0) & (below != blocks.water)
        block_ids[syy[ok], szz[ok], sxx[ok]] = blocks.snow_layer

    # --- bedrock wall ---------------------------------------------------
    if cfg.wall and on_wall.any():
        wmin = max(MIN_Y, cfg.wall_min_y)
        wmax = min(MIN_Y + WORLD_HEIGHT - 1, cfg.wall_max_y)
        block_ids[:, on_wall] = blocks.air
        block_ids[wmin - MIN_Y: wmax - MIN_Y + 1, on_wall] = blocks.bedrock

    # --- biome ids ------------------------------------------------------
    biome_ids = np.zeros((16, 16), dtype=np.int16)
    for iz in range(16):
        for ix in range(16):
            b = biome_str[iz, ix] if inside[iz, ix] else "minecraft:plains"
            biome_ids[iz, ix] = biome_reg.get(b)

    return block_ids, biome_ids, H, biome_str, inside, is_water


# ---------------------------------------------------------------------------
# Carving / feature helpers
# ---------------------------------------------------------------------------

def _carve_caves(cfg, blocks, block_ids, X, Z, H, inside, floor):
    cave_lo = floor + 2
    cave_hi = int(H.max())
    if cave_hi <= cave_lo:
        return
    ys = np.arange(cave_lo, cave_hi)
    Xc, Zc = X[None, :, :], Z[None, :, :]
    Yc = ys.reshape(-1, 1, 1).astype(np.float64)
    n1 = noise.fbm3d(Xc, Yc, Zc, cfg.seed + 7, octaves=2, freq=1 / 34.0)
    n2 = noise.fbm3d(Xc + 111, Yc + 55, Zc - 77, cfg.seed + 8, octaves=2, freq=1 / 34.0)
    t = 0.045 * cfg.cave_intensity
    carve = (np.abs(n1 - 0.5) < t) & (np.abs(n2 - 0.5) < t)
    # sparse surface openings (cave mouths / skylights)
    ent = noise.fbm2d(X, Z, cfg.seed + 21, octaves=2, freq=1 / 60.0)
    open_top = ent > 0.70
    solid = {blocks.stone, blocks.dirt, blocks.andesite, blocks.diorite,
             blocks.granite, blocks.gravel}
    solid_arr = None
    for k, yv in enumerate(ys):
        ay = yv - MIN_Y
        roof = np.where(open_top, H, H - 4)     # normally keep a roof
        colmask = carve[k] & inside & (yv <= roof) & (yv > floor + 1)
        if not colmask.any():
            continue
        layer = block_ids[ay]
        is_solid = (layer == blocks.stone) | (layer == blocks.dirt) | \
                   (layer == blocks.andesite) | (layer == blocks.diorite) | \
                   (layer == blocks.granite) | (layer == blocks.gravel)
        m = colmask & is_solid
        layer[m] = blocks.air


def _carve_ravines(cfg, blocks, block_ids, X, Z, H, inside, on_wall, world_y, floor):
    rn = noise.fbm2d(X, Z, cfg.seed + 9, octaves=2, freq=1 / 170.0)
    gate = noise.fbm2d(X + 3000, Z - 3000, cfg.seed + 10, octaves=2, freq=1 / 640.0)
    gsel = np.clip((gate - 0.58) / 0.14, 0.0, 1.0)      # sparse regions only
    width = 0.012 * gsel
    d = np.abs(rn - 0.5)
    rav = (d < width) & inside & ~on_wall
    if not rav.any():
        return
    Hs = H[None, :, :]
    y_bot = floor + 3
    # open from surface down to near the floor
    carve = rav[None, :, :] & (world_y >= y_bot) & (world_y <= Hs)
    solid = ((block_ids == blocks.stone) | (block_ids == blocks.dirt) |
             (block_ids == blocks.grass) | (block_ids == blocks.grass_snow) |
             (block_ids == blocks.sand) | (block_ids == blocks.sandstone) |
             (block_ids == blocks.andesite) | (block_ids == blocks.diorite) |
             (block_ids == blocks.granite) | (block_ids == blocks.gravel) |
             (block_ids == blocks.podzol) | (block_ids == blocks.snow_block))
    block_ids[carve & solid] = blocks.air
    # a little lava in the very bottom of the canyon
    lava_top = y_bot + 1
    pool = rav[None, :, :] & (world_y >= y_bot) & (world_y <= lava_top)
    block_ids[pool & (block_ids == blocks.air)] = blocks.lava


def _place_fluids(cfg, blocks, block_ids, inside, world_y, floor, rng):
    # thin lava aquifer at the very bottom of open caves
    lava_level = floor + 4
    below = np.empty_like(block_ids)
    below[1:] = block_ids[:-1]
    below[0] = blocks.bedrock
    inside3 = inside[None, :, :]
    lava = (inside3 & (world_y <= lava_level) & (block_ids == blocks.air) &
            (below != blocks.air) & (below != blocks.water))
    block_ids[lava] = blocks.lava
    # a few small water pockets higher up
    for _ in range(int(rng.poisson(1.2))):
        ox, oz = int(rng.integers(1, 15)), int(rng.integers(1, 15))
        if not inside[oz, ox]:
            continue
        oy = int(rng.integers(floor + 8, max(floor + 9, cfg.water_level - 6)))
        ay = oy - MIN_Y
        if 1 <= ay < WORLD_HEIGHT - 1 and block_ids[ay, oz, ox] == blocks.stone:
            for dz in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    zz2, xx2 = oz + dz, ox + dx
                    if 0 <= zz2 < 16 and 0 <= xx2 < 16 and inside[zz2, xx2]:
                        if block_ids[ay, zz2, xx2] == blocks.stone:
                            block_ids[ay, zz2, xx2] = blocks.water


def _sample_y(rng, lo, hi, bias):
    if hi <= lo:
        return lo
    if bias == "low":
        u = min(rng.random(), rng.random())
    elif bias == "high":
        u = max(rng.random(), rng.random())
    elif bias == "mid":
        u = (rng.random() + rng.random()) * 0.5
    else:
        u = rng.random()
    return lo + int(u * (hi - lo + 1e-9))


def _place_blob(block_ids, ox, oy, oz, size, target_id, rng, inside, replace_ids):
    r = max(1, int(round(size ** (1 / 3))))
    placed, attempts = 0, 0
    while placed < size and attempts < size * 6:
        attempts += 1
        x = ox + int(rng.integers(-r, r + 1))
        z = oz + int(rng.integers(-r, r + 1))
        y = oy + int(rng.integers(-r, r + 1)) - MIN_Y
        if 0 <= x < 16 and 0 <= z < 16 and 0 <= y < WORLD_HEIGHT and inside[z, x]:
            if block_ids[y, z, x] in replace_ids:
                block_ids[y, z, x] = target_id
                placed += 1


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------

_TREE_KIND = {
    "minecraft:forest": "oak", "minecraft:plains": "oak",
    "minecraft:jungle": "jungle", "minecraft:taiga": "spruce",
    "minecraft:snowy_taiga": "spruce",
}
_TREE_DENSITY = {
    "minecraft:forest": 0.06, "minecraft:plains": 0.004,
    "minecraft:jungle": 0.10, "minecraft:taiga": 0.055,
    "minecraft:snowy_taiga": 0.04,
}


def _place_trees(cfg, blocks, block_ids, H, biome_str, is_water, inside, on_wall, rng):
    for iz in range(1, 15):
        for ix in range(1, 15):
            if not inside[iz, ix] or on_wall[iz, ix] or is_water[iz, ix]:
                continue
            kind = _TREE_KIND.get(biome_str[iz, ix])
            if kind is None:
                continue
            if rng.random() > _TREE_DENSITY.get(biome_str[iz, ix], 0.0) * cfg.tree_density:
                continue
            surf = int(H[iz, ix])
            base = surf - MIN_Y + 1
            if block_ids[base - 1, iz, ix] not in (
                    blocks.grass, blocks.grass_snow, blocks.dirt, blocks.podzol):
                continue
            trunk_h = int(rng.integers(4, 7)) + (2 if kind == "jungle" else 0)
            log, leaf = blocks.log(kind), blocks.leaves(kind)
            top_y = base + trunk_h
            if top_y + 2 >= WORLD_HEIGHT:
                continue
            for ly in range(top_y - 2, top_y + 2):
                rad = 2 if ly < top_y else 1
                for dz in range(-rad, rad + 1):
                    for dx in range(-rad, rad + 1):
                        if abs(dz) == rad and abs(dx) == rad:
                            continue
                        lz, lx = iz + dz, ix + dx
                        if 0 <= lz < 16 and 0 <= lx < 16 and ly < WORLD_HEIGHT:
                            if block_ids[ly, lz, lx] == 0:
                                block_ids[ly, lz, lx] = leaf
            for k in range(trunk_h):
                block_ids[base + k, iz, ix] = log


# ---------------------------------------------------------------------------
# Surface decoration
# ---------------------------------------------------------------------------

_GRASSY = None  # set lazily


def _decorate(cfg, blocks, block_ids, H, biome_str, is_water, is_desert,
              is_snowy, inside, on_wall, rng):
    soil = (blocks.grass, blocks.grass_snow, blocks.podzol)
    for iz in range(16):
        for ix in range(16):
            if not inside[iz, ix] or on_wall[iz, ix] or is_water[iz, ix]:
                continue
            surf = int(H[iz, ix])
            base = surf - MIN_Y
            ay = base + 1
            if ay >= WORLD_HEIGHT - 3 or block_ids[ay, iz, ix] != 0:
                continue
            ground = block_ids[base, iz, ix]
            b = biome_str[iz, ix]

            # sugar cane next to water on soil/sand
            if ground in (blocks.grass, blocks.grass_snow, blocks.dirt,
                          blocks.podzol, blocks.sand):
                near_water = False
                for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    z2, x2 = iz + dz, ix + dx
                    if 0 <= z2 < 16 and 0 <= x2 < 16 and is_water[z2, x2]:
                        near_water = True
                        break
                if near_water and rng.random() < 0.22:
                    h = int(rng.integers(1, 4))
                    for k in range(h):
                        if ay + k < WORLD_HEIGHT:
                            block_ids[ay + k, iz, ix] = blocks.sugar_cane
                    continue

            if is_desert[iz, ix] and ground == blocks.sand:
                rv = rng.random()
                if rv < 0.006 and _cactus_ok(block_ids, iz, ix, ay):
                    h = int(rng.integers(1, 4))
                    for k in range(h):
                        if ay + k < WORLD_HEIGHT:
                            block_ids[ay + k, iz, ix] = blocks.cactus
                elif rv < 0.03:
                    block_ids[ay, iz, ix] = blocks.dead_bush
                continue

            if is_snowy[iz, ix]:
                continue  # snow layer instead of plants
            if ground not in soil:
                continue

            rv = rng.random()
            if b == "minecraft:jungle":
                if rv < 0.34:
                    block_ids[ay, iz, ix] = blocks.short_grass
                elif rv < 0.40:
                    _place_tall_grass(block_ids, blocks, iz, ix, ay)
            elif b in ("minecraft:taiga",):
                if rv < 0.16:
                    block_ids[ay, iz, ix] = blocks.fern
            else:  # plains / forest / beach-adjacent grass
                if rv < 0.18:
                    block_ids[ay, iz, ix] = blocks.short_grass
                elif rv < 0.22:
                    _place_tall_grass(block_ids, blocks, iz, ix, ay)
                elif rv < 0.245:
                    block_ids[ay, iz, ix] = blocks.flowers[
                        int(rng.integers(0, len(blocks.flowers)))]


def _place_tall_grass(block_ids, blocks, iz, ix, ay):
    if ay + 1 < WORLD_HEIGHT and block_ids[ay + 1, iz, ix] == 0:
        block_ids[ay, iz, ix] = blocks.tall_grass_lo
        block_ids[ay + 1, iz, ix] = blocks.tall_grass_hi


def _cactus_ok(block_ids, iz, ix, ay):
    for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        z2, x2 = iz + dz, ix + dx
        if 0 <= z2 < 16 and 0 <= x2 < 16:
            if block_ids[ay, z2, x2] != 0:
                return False
    return True


# ---------------------------------------------------------------------------
# level.dat
# ---------------------------------------------------------------------------

def _build_level_dat(cfg, spawn_y):
    Data = nbt.Compound()
    Data["LevelName"] = nbt.String(cfg.name)
    Data["version"] = nbt.Int(19133)
    Data["DataVersion"] = nbt.Int(cfg.data_version)

    ver = nbt.Compound()
    ver["Id"] = nbt.Int(cfg.data_version)
    ver["Name"] = nbt.String(cfg.version_name)
    ver["Series"] = nbt.String("main")
    ver["Snapshot"] = nbt.Byte(0)
    Data["Version"] = ver

    Data["GameType"] = nbt.Int(cfg.game_type)
    Data["Difficulty"] = nbt.Byte(2)
    Data["hardcore"] = nbt.Byte(0)
    Data["allowCommands"] = nbt.Byte(1 if cfg.allow_commands else 0)
    Data["initialized"] = nbt.Byte(1)
    Data["DifficultyLocked"] = nbt.Byte(0)
    Data["Time"] = nbt.Long(0)
    Data["DayTime"] = nbt.Long(1000)
    Data["clearWeatherTime"] = nbt.Int(0)
    Data["rainTime"] = nbt.Int(10000)
    Data["thunderTime"] = nbt.Int(50000)
    Data["raining"] = nbt.Byte(0)
    Data["thundering"] = nbt.Byte(0)
    Data["WanderingTraderSpawnChance"] = nbt.Int(25)
    Data["WanderingTraderSpawnDelay"] = nbt.Int(24000)
    Data["WasModded"] = nbt.Byte(0)

    Data["SpawnX"] = nbt.Int(0)
    Data["SpawnY"] = nbt.Int(int(spawn_y))
    Data["SpawnZ"] = nbt.Int(0)
    Data["SpawnAngle"] = nbt.Float(0.0)
    Data["BorderCenterX"] = nbt.Double(0.0)
    Data["BorderCenterZ"] = nbt.Double(0.0)
    Data["BorderSize"] = nbt.Double(float(cfg.size + 32))

    dp = nbt.Compound()
    dp["Enabled"] = nbt.List(nbt.String.tag_id, [nbt.String("vanilla")])
    dp["Disabled"] = nbt.List(0, [])
    Data["DataPacks"] = dp

    flat_settings = nbt.Compound()
    flat_settings["biome"] = nbt.String("minecraft:plains")
    flat_settings["lakes"] = nbt.Byte(0)
    flat_settings["features"] = nbt.Byte(0)
    flat_settings["layers"] = nbt.List(nbt.Compound.tag_id, [])
    flat_settings["structure_overrides"] = nbt.List(nbt.String.tag_id, [])

    generator = nbt.Compound()
    generator["type"] = nbt.String("minecraft:flat")
    generator["settings"] = flat_settings

    overworld = nbt.Compound()
    overworld["type"] = nbt.String("minecraft:overworld")
    overworld["generator"] = generator

    dims = nbt.Compound()
    dims["minecraft:overworld"] = overworld

    wgs = nbt.Compound()
    wgs["seed"] = nbt.Long(cfg.seed)
    wgs["generate_features"] = nbt.Byte(0)
    wgs["bonus_chest"] = nbt.Byte(0)
    wgs["dimensions"] = dims
    Data["WorldGenSettings"] = wgs

    gr = nbt.Compound()
    for k, v in {
        "doMobSpawning": "true", "keepInventory": "false",
        "doDaylightCycle": "true", "doWeatherCycle": "true", "spawnRadius": "2",
    }.items():
        gr[k] = nbt.String(v)
    Data["GameRules"] = gr

    root = nbt.Compound()
    root["Data"] = Data
    return nbt.to_gzip("", root)


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------

def _generate_region_worker(args):
    (job_index, cfg, xmin, xmax, pscale, preview_size,
     rx, rz, cxmin, cxmax, czmin, czmax, progress_queue, progress_interval) = args

    blocks, biome_reg = _prepare_palettes(cfg)
    block_palette = blocks.palette()
    biome_palette = list(biome_reg.list)
    rw = RegionWriter(rx, rz)

    patch_x, patch_z, patch_w, patch_h = _preview_patch_rect(
        cfg, xmin, xmax, pscale, preview_size, cxmin, cxmax, czmin, czmax)
    preview_patch = np.zeros((patch_h, patch_w, 3), dtype=np.uint8)

    chunks = 0
    last_reported_chunks = 0
    for cz in range(czmin, czmax + 1):
        for cx in range(cxmin, cxmax + 1):
            rng_seed = _chunk_rng_seed(cfg.seed, cx, cz)
            block_ids, biome_ids, H, biome_str, inside, is_water = generate_chunk(
                cfg, blocks, biome_reg, cx, cz, xmin, xmax, rng_seed)

            root = build_chunk(cx, cz, block_ids, biome_ids, block_palette,
                               biome_palette, cfg.data_version, MIN_SECTION, N_SECTIONS)
            rw.add_chunk(cx, cz, root)

            _fill_preview_patch(preview_patch, cfg, cx, cz, xmin, pscale,
                                patch_x, patch_z, H, biome_str, inside, is_water)
            chunks += 1
            if progress_queue is not None and chunks - last_reported_chunks >= progress_interval:
                progress_queue.put((job_index, chunks))
                last_reported_chunks = chunks

    if progress_queue is not None and chunks != last_reported_chunks:
        progress_queue.put((job_index, chunks))

    return {
        "job_index": job_index,
        "rx": rx,
        "rz": rz,
        "filename": rw.filename,
        "region": rw.to_bytes(),
        "patch_x": patch_x,
        "patch_z": patch_z,
        "patch": preview_patch,
        "chunks": chunks,
    }


def generate_world(cfg: WorldConfig, out_dir: str,
                   progress: Optional[Callable[[float, str], None]] = None,
                   workers: Optional[int] = None):
    def report(p, msg):
        if progress:
            progress(p, msg)

    t0 = time.time()
    region_dir = os.path.join(out_dir, "region")
    os.makedirs(region_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    for fn in os.listdir(region_dir):
        if fn.startswith("r.") and fn.endswith(".mca"):
            os.remove(os.path.join(region_dir, fn))

    xmin = -(cfg.size // 2)
    xmax = xmin + cfg.size - 1
    cxmin, cxmax = xmin >> 4, xmax >> 4
    czmin, czmax = xmin >> 4, xmax >> 4
    total_chunks = (cxmax - cxmin + 1) * (czmax - czmin + 1)

    region_jobs = list(_region_jobs(cxmin, cxmax, czmin, czmax))

    pscale = max(1, cfg.size // 768)
    preview_size = (cfg.size + pscale - 1) // pscale

    def make_jobs(progress_queue=None, progress_interval=8):
        return [
            (job_index, cfg, xmin, xmax, pscale, preview_size,
             rx, rz, jcxmin, jcxmax, jczmin, jczmax, progress_queue, progress_interval)
            for job_index, (rx, rz, jcxmin, jcxmax, jczmin, jczmax)
            in enumerate(region_jobs)
        ]

    preview = np.zeros((preview_size, preview_size, 3), dtype=np.uint8)

    worker_count = _resolve_worker_count(workers, len(region_jobs), total_chunks)
    progress_counts = [0] * len(region_jobs)
    done_chunks = 0
    preview_patches = {}

    if worker_count == 1:
        report(0.0, "Starting generation...")
        for job in make_jobs():
            result = _generate_region_worker(job)
            _write_region_result(region_dir, result)
            preview_patches[(result["rz"], result["rx"])] = (
                result["patch_x"], result["patch_z"], result["patch"])
            done_chunks += result["chunks"]
            if progress:
                report(0.05 + 0.85 * done_chunks / total_chunks,
                       f"Generated {done_chunks}/{total_chunks} chunks")
    else:
        report(0.0, f"Starting generation on {worker_count} workers...")
        mp_context = mp.get_context("spawn") if threading.current_thread() is not threading.main_thread() else mp.get_context()
        progress_interval = max(1, min(16, total_chunks // max(1, worker_count * 64)))

        def drain_progress(progress_queue):
            nonlocal done_chunks
            updated = False
            while True:
                try:
                    job_index, chunks = progress_queue.get_nowait()
                except queue.Empty:
                    break
                if 0 <= job_index < len(progress_counts) and chunks > progress_counts[job_index]:
                    progress_counts[job_index] = chunks
                    updated = True
            if updated:
                done_chunks = sum(progress_counts)
                report(0.05 + 0.85 * done_chunks / total_chunks,
                       f"Generated {done_chunks}/{total_chunks} chunks")

        with mp_context.Manager() as manager:
            progress_queue = manager.Queue()
            with ProcessPoolExecutor(max_workers=worker_count, mp_context=mp_context) as pool:
                futures = [pool.submit(_generate_region_worker, job)
                           for job in make_jobs(progress_queue, progress_interval)]
                pending = set(futures)
                while pending:
                    drain_progress(progress_queue)
                    done_now = [fut for fut in pending if fut.done()]
                    if not done_now:
                        try:
                            job_index, chunks = progress_queue.get(timeout=0.25)
                            if 0 <= job_index < len(progress_counts) and chunks > progress_counts[job_index]:
                                progress_counts[job_index] = chunks
                                done_chunks = sum(progress_counts)
                                report(0.05 + 0.85 * done_chunks / total_chunks,
                                       f"Generated {done_chunks}/{total_chunks} chunks")
                        except queue.Empty:
                            pass
                        continue

                    for fut in done_now:
                        pending.remove(fut)
                        result = fut.result()
                        _write_region_result(region_dir, result)
                        preview_patches[(result["rz"], result["rx"])] = (
                            result["patch_x"], result["patch_z"], result["patch"])
                        if result["chunks"] > progress_counts[result["job_index"]]:
                            progress_counts[result["job_index"]] = result["chunks"]
                            done_chunks = sum(progress_counts)
                            report(0.05 + 0.85 * done_chunks / total_chunks,
                                   f"Generated {done_chunks}/{total_chunks} chunks")
                drain_progress(progress_queue)

    report(0.92, "Compositing preview...")
    for key in sorted(preview_patches):
        px, pz, patch = preview_patches[key]
        sub = preview[pz:pz + patch.shape[0], px:px + patch.shape[1]]
        mask = patch.any(axis=2)
        sub[mask] = patch[mask]

    blocks, _ = _prepare_palettes(cfg)
    Xc, Zc = np.array([[0.0]]), np.array([[0.0]])
    spawn_y = int(_fields(cfg, blocks, Xc, Zc)["H"][0, 0]) + 1
    report(0.96, "Writing level.dat...")
    with open(os.path.join(out_dir, "level.dat"), "wb") as fpo:
        fpo.write(_build_level_dat(cfg, spawn_y))
    with open(os.path.join(out_dir, "session.lock"), "wb") as fpo:
        fpo.write(b"\xe2\x98\x83")

    report(1.0, f"Done in {time.time()-t0:.1f}s")
    return {
        "chunks": total_chunks, "regions": len(region_jobs), "workers": worker_count,
        "spawn": (0, spawn_y, 0), "bounds": (xmin, xmax),
        "seconds": round(time.time() - t0, 1), "preview": preview,
    }


def _write_region_result(region_dir, result):
    with open(os.path.join(region_dir, result["filename"]), "wb") as fpo:
        fpo.write(result["region"])


def _fill_preview_patch(preview, cfg, cx, cz, xmin, pscale, patch_x, patch_z,
                        H, biome_str, inside, is_water):
    Hsize, Wsize = preview.shape[:2]
    for iz in range(16):
        for ix in range(16):
            if not inside[iz, ix]:
                continue
            wx, wz = cx * 16 + ix, cz * 16 + iz
            px, pz = (wx - xmin) // pscale - patch_x, (wz - xmin) // pscale - patch_z
            if not (0 <= px < Wsize and 0 <= pz < Hsize):
                continue
            if is_water[iz, ix]:
                col = BIOME_COLORS["minecraft:river"]
                depth = np.clip((cfg.water_level - int(H[iz, ix])) / 8.0, 0.0, 1.0)
                col = (int(col[0] * (1 - depth * 0.35)),
                       int(col[1] * (1 - depth * 0.3)),
                       int(col[2] * (1 - depth * 0.15)))
                preview[pz, px] = col
                continue
            col = BIOME_COLORS.get(biome_str[iz, ix], (150, 150, 150))
            shade = np.clip((int(H[iz, ix]) - cfg.water_level) / 70.0, -0.4, 0.7)
            preview[pz, px] = (
                int(np.clip(col[0] * (1 + shade * 0.45), 0, 255)),
                int(np.clip(col[1] * (1 + shade * 0.45), 0, 255)),
                int(np.clip(col[2] * (1 + shade * 0.45), 0, 255)))


def _fill_preview(preview, cfg, cx, cz, xmin, pscale, H, biome_str, inside, is_water):
    _fill_preview_patch(preview, cfg, cx, cz, xmin, pscale, 0, 0,
                        H, biome_str, inside, is_water)
