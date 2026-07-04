"""
Core world generation.

Produces a Minecraft Java Edition save folder:
  <out>/level.dat
  <out>/region/r.*.*.mca
  <out>/data/  (empty, created for completeness)

Terrain is generated directly (numpy noise), so the world is finite, centred on
(0,0), walled in bedrock, with a bedrock floor above void and configurable ore
veins / caves / biomes. Blocks use the plain "1.12-style" palette (stone +
diamond_ore rather than deepslate) even below y=0.
"""
import os
import math
import time
import random
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
# Block / biome registries (global ids shared across the whole world)
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
    def __init__(self):
        self.reg = Registry()
        # id 0 MUST be air (heightmap / preview logic relies on it)
        self.air = self.reg.get(_bkey("minecraft:air"))
        self.bedrock = self.reg.get(_bkey("minecraft:bedrock"))
        self.stone = self.reg.get(_bkey("minecraft:stone"))
        self.dirt = self.reg.get(_bkey("minecraft:dirt"))
        self.grass = self.reg.get(_bkey("minecraft:grass_block"))
        self.grass_snow = self.reg.get(_bkey("minecraft:grass_block", {"snowy": "true"}))
        self.sand = self.reg.get(_bkey("minecraft:sand"))
        self.gravel = self.reg.get(_bkey("minecraft:gravel"))
        self.water = self.reg.get(_bkey("minecraft:water"))
        self.snow_layer = self.reg.get(_bkey("minecraft:snow", {"layers": "1"}))
        self.snow_block = self.reg.get(_bkey("minecraft:snow_block"))
        self.podzol = self.reg.get(_bkey("minecraft:podzol"))
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
        # returns list of (name, props_dict)
        return [(name, dict(props)) for (name, props) in self.reg.list]


# Biome ids (strings). Colours used for the preview map.
BIOME_COLORS = {
    "minecraft:plains": (141, 179, 96),
    "minecraft:forest": (79, 130, 66),
    "minecraft:taiga": (86, 125, 112),
    "minecraft:jungle": (83, 160, 40),
    "minecraft:snowy_plains": (238, 246, 250),
    "minecraft:snowy_taiga": (200, 220, 210),
    "minecraft:snowy_slopes": (220, 230, 240),
    "minecraft:river": (60, 110, 180),
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class OreCfg:
    name: str = "diamond_ore"
    y_min: int = -19
    y_max: int = 5
    veins_per_chunk: float = 8.0
    vein_size: int = 5


@dataclass
class WorldConfig:
    name: str = "Forged World"
    size: int = 1000
    seed: int = 0
    data_version: int = 3465          # 1.20.1
    version_name: str = "1.20.1"

    floor_y: int = -20                # bedrock floor; void below
    water_level: int = 62
    base_height: int = 70
    hill_amp: float = 10.0
    mountain_amp: float = 44.0
    mountain_amount: float = 0.55     # 0..1, higher => more/higher mountains
    snowline_y: int = 90

    caves: bool = True
    cave_intensity: float = 1.0       # scales carving

    rivers: bool = True
    river_amount: float = 0.5         # 0..1
    lakes: bool = True

    trees: bool = True
    tree_density: float = 1.0

    wall: bool = True
    wall_min_y: int = MIN_Y
    wall_max_y: int = 319

    game_type: int = 0                # 0 survival, 1 creative
    allow_commands: bool = True

    ores: List[OreCfg] = field(default_factory=lambda: [
        OreCfg("diamond_ore", -19, 5, 8.0, 5),
        OreCfg("coal_ore", 5, 110, 11.0, 9),
        OreCfg("iron_ore", -16, 66, 9.0, 6),
    ])


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _coord_grids(cx, cz):
    """World x/z for the 16x16 columns of a chunk. Returns (X, Z) [z, x]."""
    xs = cx * 16 + np.arange(16)
    zs = cz * 16 + np.arange(16)
    X, Z = np.meshgrid(xs, zs)   # [z, x]
    return X.astype(np.float64), Z.astype(np.float64)


def _biome_and_height(cfg, blocks, X, Z):
    """Compute per-column surface height, biome id, water mask and top block."""
    seed = cfg.seed
    # --- height ---------------------------------------------------------
    hills = (noise.fbm2d(X, Z, seed + 1, octaves=4, freq=1 / 90.0) - 0.5) * 2.0
    mnt_mask = noise.fbm2d(X, Z, seed + 2, octaves=3, freq=1 / 260.0)
    # push mountains up where the mask exceeds a threshold set by mountain_amount
    thr = 1.0 - 0.55 * cfg.mountain_amount
    mnt = np.clip((mnt_mask - thr) / max(1e-6, (1.0 - thr)), 0.0, 1.0)
    mnt = mnt ** 1.6
    H = cfg.base_height + hills * cfg.hill_amp + mnt * cfg.mountain_amp
    H = H.astype(np.int32)

    # --- climate for biome ---------------------------------------------
    temp = noise.fbm2d(X + 1000, Z - 1000, seed + 3, octaves=3, freq=1 / 320.0)
    hum = noise.fbm2d(X - 2000, Z + 2000, seed + 4, octaves=3, freq=1 / 300.0)

    biome = np.empty(X.shape, dtype=object)
    is_snowy = np.zeros(X.shape, dtype=bool)

    cold = temp < 0.42
    hot = temp >= 0.60
    wet = hum >= 0.62
    dry = hum < 0.40

    biome[:] = "minecraft:plains"
    biome[cold & dry] = "minecraft:snowy_plains"
    biome[cold & ~dry] = "minecraft:snowy_taiga"
    biome[~cold & ~hot & (hum >= 0.4) & (hum < 0.7)] = "minecraft:forest"
    biome[~cold & ~hot & wet] = "minecraft:taiga"
    biome[hot & wet] = "minecraft:jungle"
    biome[hot & ~wet] = "minecraft:plains"
    is_snowy = cold.copy()

    # mountains override
    high = mnt > 0.35
    biome[high] = "minecraft:snowy_slopes"
    is_snowy[high & (H >= cfg.snowline_y)] = True

    # --- rivers ---------------------------------------------------------
    is_water = np.zeros(X.shape, dtype=bool)
    if cfg.rivers:
        rn = noise.fbm2d(X, Z, seed + 5, octaves=2, freq=1 / 200.0)
        width = 0.010 + 0.05 * cfg.river_amount
        river = np.abs(rn - 0.5) < width
        river &= ~high                      # rivers don't climb mountains
        H = np.where(river, np.minimum(H, cfg.water_level - 2), H)
        is_water |= river
        biome[river] = "minecraft:river"
    if cfg.lakes:
        ln = noise.fbm2d(X + 500, Z + 500, seed + 6, octaves=2, freq=1 / 70.0)
        lake = (ln < 0.20) & ~high
        H = np.where(lake, np.minimum(H, cfg.water_level - 2), H)
        is_water |= lake

    H = np.clip(H, cfg.floor_y + 3, MIN_Y + WORLD_HEIGHT - 40).astype(np.int32)

    # --- surface (top) block per column --------------------------------
    top = np.full(X.shape, blocks.grass, dtype=np.int32)
    top[is_snowy] = blocks.grass_snow
    top[biome == "minecraft:taiga"] = blocks.podzol
    top[biome == "minecraft:snowy_taiga"] = blocks.grass_snow
    top[biome == "minecraft:snowy_slopes"] = blocks.snow_block
    # water columns get a sand/gravel bed
    top[is_water] = blocks.sand

    biome_ids = biome
    return H, biome_ids, is_water, top, is_snowy


def generate_chunk(cfg, blocks, biome_reg, cx, cz, xmin, xmax, rng_seed):
    """Return (block_ids [384,16,16] int16, biome_ids [16,16] int16)."""
    X, Z = _coord_grids(cx, cz)             # [z, x]
    zz, xx = np.meshgrid(np.arange(16), np.arange(16), indexing="ij")  # [z,x]

    inside = (X >= xmin) & (X <= xmax) & (Z >= xmin) & (Z <= xmax)   # [z,x]
    on_wall = inside & ((X == xmin) | (X == xmax) | (Z == xmin) | (Z == xmax))

    H, biome_str, is_water, top, is_snowy = _biome_and_height(cfg, blocks, X, Z)

    block_ids = np.zeros((WORLD_HEIGHT, 16, 16), dtype=np.int16)  # [y, z, x]
    world_y = (np.arange(WORLD_HEIGHT) + MIN_Y).reshape(-1, 1, 1)

    inside3 = inside[None, :, :]
    Hs = H[None, :, :]
    floor = cfg.floor_y

    # bedrock floor + stone/dirt columns (only inside, only land part)
    block_ids[np.broadcast_to(inside3 & (world_y == floor), block_ids.shape)] = blocks.bedrock
    stone_mask = inside3 & (world_y > floor) & (world_y <= Hs - 4)
    block_ids[stone_mask] = blocks.stone
    dirt_mask = inside3 & (world_y >= Hs - 3) & (world_y <= Hs - 1)
    block_ids[dirt_mask] = blocks.dirt

    # --- caves ----------------------------------------------------------
    if cfg.caves:
        cave_lo = floor + 2
        cave_hi = int(H.max())
        if cave_hi > cave_lo:
            ys = np.arange(cave_lo, cave_hi)
            Xc = X[None, :, :]
            Zc = Z[None, :, :]
            Yc = ys.reshape(-1, 1, 1).astype(np.float64)
            n1 = noise.fbm3d(Xc, Yc, Zc, cfg.seed + 7, octaves=2, freq=1 / 34.0)
            n2 = noise.fbm3d(Xc + 111, Yc + 55, Zc - 77, cfg.seed + 8,
                             octaves=2, freq=1 / 34.0)
            t = 0.045 * cfg.cave_intensity
            carve = (np.abs(n1 - 0.5) < t) & (np.abs(n2 - 0.5) < t)
            # only below the surface and above the floor
            arr_lo = cave_lo - MIN_Y
            for k, yv in enumerate(ys):
                ay = yv - MIN_Y
                colmask = carve[k] & inside & (yv <= (H - 2)) & (yv > floor + 1)
                if colmask.any():
                    layer = block_ids[ay]
                    solid = (layer == blocks.stone) | (layer == blocks.dirt)
                    m = colmask & solid
                    layer[m] = blocks.air

    # --- surface top + dirt already placed; set the top block ----------
    land = inside & ~on_wall & ~is_water & (H > floor + 3)
    arr_surface = (H - MIN_Y)
    fx = xx[land]
    fz = zz[land]
    fy = arr_surface[land]
    block_ids[fy, fz, fx] = top[land].astype(np.int16)

    # water columns: fill water from bed+1 up to water_level, bed = sand
    if is_water.any():
        wcol = inside & ~on_wall & is_water
        # set bed top (H) to sand already via top? top set only for land; do here
        bfy = arr_surface[wcol]
        block_ids[bfy, zz[wcol], xx[wcol]] = blocks.sand
        wl = cfg.water_level
        water_fill = inside3 & is_water[None, :, :] & (world_y > Hs) & (world_y <= wl)
        # don't fill where it would exceed array / into walls
        block_ids[water_fill & (block_ids == 0)] = blocks.water

    # --- ores -----------------------------------------------------------
    rng = np.random.default_rng(rng_seed)
    for ore in cfg.ores:
        oid = blocks.ore(ore.name)
        n_veins = rng.poisson(max(0.0, ore.veins_per_chunk))
        y_lo = max(floor + 1, ore.y_min)
        y_hi = ore.y_max
        for _ in range(int(n_veins)):
            ox = rng.integers(0, 16)
            oz = rng.integers(0, 16)
            if not inside[oz, ox]:
                continue
            surf = int(H[oz, ox])
            hi = min(y_hi, surf - 2)
            if hi < y_lo:
                continue
            oy = rng.integers(y_lo, hi + 1)
            _place_vein(block_ids, blocks, ox, oy, oz, ore.vein_size, oid, rng, inside)

    # --- trees ----------------------------------------------------------
    if cfg.trees:
        _place_trees(cfg, blocks, block_ids, H, biome_str, is_water, is_snowy,
                     inside, on_wall, xx, zz, rng)

    # --- snow layer on top of snowy surfaces ---------------------------
    if is_snowy.any():
        snowy_cols = inside & ~on_wall & is_snowy & ~is_water & (H > floor + 3)
        sy = (H - MIN_Y) + 1
        valid = snowy_cols & (sy < WORLD_HEIGHT)
        syy = sy[valid]
        szz = zz[valid]
        sxx = xx[valid]
        cur = block_ids[syy, szz, sxx]
        place = cur == 0
        block_ids[syy[place], szz[place], sxx[place]] = blocks.snow_layer

    # --- bedrock wall (overrides the column) ---------------------------
    if cfg.wall and on_wall.any():
        wmin = max(MIN_Y, cfg.wall_min_y)
        wmax = min(MIN_Y + WORLD_HEIGHT - 1, cfg.wall_max_y)
        wl_cols = on_wall
        # clear column then fill bedrock in [wmin, wmax]
        block_ids[:, wl_cols] = blocks.air
        a_lo = wmin - MIN_Y
        a_hi = wmax - MIN_Y
        block_ids[a_lo:a_hi + 1, wl_cols] = blocks.bedrock

    # biome ids
    biome_ids = np.zeros((16, 16), dtype=np.int16)
    for iz in range(16):
        for ix in range(16):
            b = biome_str[iz, ix] if inside[iz, ix] else "minecraft:plains"
            biome_ids[iz, ix] = biome_reg.get(b)

    return block_ids, biome_ids, H, biome_str, inside, is_water


def _place_vein(block_ids, blocks, ox, oy, oz, size, oid, rng, inside):
    r = max(1, int(round(size ** (1 / 3))))
    n = size
    placed = 0
    attempts = 0
    while placed < n and attempts < n * 6:
        attempts += 1
        dx = rng.integers(-r, r + 1)
        dy = rng.integers(-r, r + 1)
        dz = rng.integers(-r, r + 1)
        x = ox + dx
        z = oz + dz
        y = oy + dy - MIN_Y
        if 0 <= x < 16 and 0 <= z < 16 and 0 <= y < WORLD_HEIGHT and inside[z, x]:
            if block_ids[y, z, x] == blocks.stone:
                block_ids[y, z, x] = oid
                placed += 1


_TREE_KIND = {
    "minecraft:forest": "oak",
    "minecraft:plains": "oak",
    "minecraft:jungle": "jungle",
    "minecraft:taiga": "spruce",
    "minecraft:snowy_taiga": "spruce",
    "minecraft:snowy_slopes": None,
    "minecraft:snowy_plains": None,
    "minecraft:river": None,
}
_TREE_DENSITY = {
    "minecraft:forest": 0.10,
    "minecraft:plains": 0.008,
    "minecraft:jungle": 0.14,
    "minecraft:taiga": 0.09,
    "minecraft:snowy_taiga": 0.06,
}


def _place_trees(cfg, blocks, block_ids, H, biome_str, is_water, is_snowy,
                 inside, on_wall, xx, zz, rng):
    for iz in range(1, 15):
        for ix in range(1, 15):
            if not inside[iz, ix] or on_wall[iz, ix] or is_water[iz, ix]:
                continue
            b = biome_str[iz, ix]
            kind = _TREE_KIND.get(b)
            if kind is None:
                continue
            dens = _TREE_DENSITY.get(b, 0.0) * cfg.tree_density
            if rng.random() > dens:
                continue
            surf = int(H[iz, ix])
            base = surf - MIN_Y + 1
            trunk_h = int(rng.integers(4, 7)) + (2 if kind == "jungle" else 0)
            log = blocks.log(kind)
            leaf = blocks.leaves(kind)
            top_y = base + trunk_h
            if top_y + 2 >= WORLD_HEIGHT:
                continue
            # leaves canopy (clamped inside chunk)
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
            # trunk
            for k in range(trunk_h):
                block_ids[base + k, iz, ix] = log


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

    # datapacks
    dp = nbt.Compound()
    dp["Enabled"] = nbt.List(nbt.String.tag_id, [nbt.String("vanilla")])
    dp["Disabled"] = nbt.List(0, [])
    Data["DataPacks"] = dp

    # world gen: flat *void* overworld so any chunk beyond our walls is empty.
    # (The player is sealed inside the bedrock box; vanilla dims get re-added.)
    flat_settings = nbt.Compound()
    flat_settings["biome"] = nbt.String("minecraft:plains")
    flat_settings["lakes"] = nbt.Byte(0)
    flat_settings["features"] = nbt.Byte(0)
    flat_settings["layers"] = nbt.List(nbt.Compound.tag_id, [])  # empty => void
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

    # sensible default gamerules
    gr = nbt.Compound()
    for k, v in {
        "doMobSpawning": "true", "keepInventory": "false",
        "doDaylightCycle": "true", "doWeatherCycle": "true",
        "spawnRadius": "2",
    }.items():
        gr[k] = nbt.String(v)
    Data["GameRules"] = gr

    root = nbt.Compound()
    root["Data"] = Data
    return nbt.to_gzip("", root)


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------

def generate_world(cfg: WorldConfig, out_dir: str,
                   progress: Optional[Callable[[float, str], None]] = None):
    def report(p, msg):
        if progress:
            progress(p, msg)

    t0 = time.time()
    os.makedirs(os.path.join(out_dir, "region"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)

    xmin = -(cfg.size // 2)
    xmax = xmin + cfg.size - 1
    cxmin, cxmax = xmin >> 4, xmax >> 4
    czmin, czmax = xmin >> 4, xmax >> 4
    total_chunks = (cxmax - cxmin + 1) * (czmax - czmin + 1)

    blocks = Blocks()
    biome_reg = Registry()

    # preview map (downsampled)
    pscale = max(1, cfg.size // 768)
    P = (cfg.size + pscale - 1) // pscale
    preview = np.zeros((P, P, 3), dtype=np.uint8)

    regions: Dict[tuple, RegionWriter] = {}
    done = 0
    report(0.0, "Starting generation...")

    for cz in range(czmin, czmax + 1):
        for cx in range(cxmin, cxmax + 1):
            rng_seed = (cfg.seed & 0xFFFFFFFF) ^ ((cx & 0xFFFF) << 8) ^ ((cz & 0xFFFF) << 20) ^ 0x9E3779B1
            block_ids, biome_ids, H, biome_str, inside, is_water = generate_chunk(
                cfg, blocks, biome_reg, cx, cz, xmin, xmax, rng_seed)

            rx, rz = cx >> 5, cz >> 5
            rw = regions.get((rx, rz))
            if rw is None:
                rw = RegionWriter(rx, rz)
                regions[(rx, rz)] = rw

            root = build_chunk(cx, cz, block_ids, biome_ids, blocks.palette(),
                               biome_reg.list, cfg.data_version, MIN_SECTION, N_SECTIONS)
            rw.add_chunk(cx, cz, root)

            _fill_preview(preview, cfg, cx, cz, xmin, pscale, H, biome_str,
                          inside, is_water)

            done += 1
            if progress and (done % 16 == 0 or done == total_chunks):
                report(0.05 + 0.85 * done / total_chunks,
                       f"Generated {done}/{total_chunks} chunks")

    report(0.92, "Writing region files...")
    for (rx, rz), rw in regions.items():
        with open(os.path.join(out_dir, "region", rw.filename), "wb") as f:
            f.write(rw.to_bytes())

    # spawn on the surface at world centre
    Xc = np.array([[0.0]])
    Zc = np.array([[0.0]])
    Hc, *_ = _biome_and_height(cfg, blocks, Xc, Zc)
    spawn_y = int(Hc[0, 0]) + 1
    report(0.96, "Writing level.dat...")
    with open(os.path.join(out_dir, "level.dat"), "wb") as f:
        f.write(_build_level_dat(cfg, spawn_y))

    # session lock + icon-less
    with open(os.path.join(out_dir, "session.lock"), "wb") as f:
        f.write(b"\xe2\x98\x83")

    report(1.0, f"Done in {time.time()-t0:.1f}s")
    return {
        "chunks": total_chunks,
        "regions": len(regions),
        "spawn": (0, spawn_y, 0),
        "bounds": (xmin, xmax),
        "seconds": round(time.time() - t0, 1),
        "preview": preview,
    }


def _fill_preview(preview, cfg, cx, cz, xmin, pscale, H, biome_str, inside, is_water):
    P = preview.shape[0]
    for iz in range(16):
        for ix in range(16):
            if not inside[iz, ix]:
                continue
            wx = cx * 16 + ix
            wz = cz * 16 + iz
            px = (wx - xmin) // pscale
            pz = (wz - xmin) // pscale
            if not (0 <= px < P and 0 <= pz < P):
                continue
            b = biome_str[iz, ix]
            col = BIOME_COLORS.get(b, (150, 150, 150))
            if is_water[iz, ix]:
                col = BIOME_COLORS["minecraft:river"]
            # crude hillshade from height
            shade = np.clip((int(H[iz, ix]) - cfg.water_level) / 60.0, -0.4, 0.6)
            r = int(np.clip(col[0] * (1 + shade * 0.5), 0, 255))
            g = int(np.clip(col[1] * (1 + shade * 0.5), 0, 255))
            bl = int(np.clip(col[2] * (1 + shade * 0.5), 0, 255))
            preview[pz, px] = (r, g, bl)
