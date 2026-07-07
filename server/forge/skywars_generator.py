"""
Skywars map generator (separate from generator.py, which stays vanilla-only).

Builds a *void* Minecraft Java world populated with one or more procedurally
generated skywars island clusters. A cluster is:

  * one large central island (ender chests scattered on top),
  * a ring of medium "middle" islands around it (regular chests),
  * an outer ring of spawn islands (regular chests) -- this count is the
    configurable "map size" (default 12, scales up to 64+).

Islands are flat-topped for PvP, taper to a spiky underside, carry biome-tinted
surfaces, occasional trees / rocks / foliage, and ores both on the surface and
buried inside the stone (each ore's frequency is configurable).

Many clusters can be generated into one world in a batch (e.g. 10 clusters of
24 spawn islands + 10 of 12 + 10 of 32). They are laid out on a padded grid with
generous spacing, and every cluster writes its own labelled positions JSON
(spawn point per spawn island + the central island's middle point).

Only chunks that an island actually touches are written; the rest of the world
is genuine void.
"""
import json
import math
import os
import time
import multiprocessing as mp
import threading
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import nbt
from .region import RegionWriter, build_chunk

# Vertical layout matches the overworld dimension range used by region.build_chunk.
MIN_Y = -64
WORLD_HEIGHT = 384                 # -64 .. 319
MIN_SECTION = MIN_Y // 16          # -4
N_SECTIONS = WORLD_HEIGHT // 16    # 24

# Islands only occupy a vertical band, so we store chunk columns at that band
# resolution (cheap) and paste them into a full-height chunk at write time.
BAND_LO = -40
BAND_HI = 200
BAND_H = BAND_HI - BAND_LO + 1

AIR = 0                            # id 0 MUST be air (heightmap logic relies on it)


# ---------------------------------------------------------------------------
# Deterministic hashing / RNG
# ---------------------------------------------------------------------------

_UINT32 = 0xFFFFFFFF


def _mix32(n):
    n &= _UINT32
    n ^= (n >> 16)
    n = (n * 0x7FEB352D) & _UINT32
    n ^= (n >> 15)
    n = (n * 0x846CA68B) & _UINT32
    n ^= (n >> 16)
    return n & _UINT32


def _hash(*vals):
    n = 0x9E3779B9
    for v in vals:
        n = _mix32(n ^ (int(v) & _UINT32))
        n = (n * 0x85157AF5) & _UINT32
    return _mix32(n)


class Rng:
    """Small deterministic PRNG so a seed reproduces a world exactly."""

    def __init__(self, *seed_parts):
        self.state = _hash(*seed_parts) or 0x1234567

    def next(self):
        self.state = _mix32(self.state * 0x9E3779B1 + 0x632BE5AB)
        return self.state

    def rand(self):
        return self.next() / 4294967296.0

    def uniform(self, lo, hi):
        return lo + (hi - lo) * self.rand()

    def randint(self, lo, hi):        # inclusive
        return lo + int(self.rand() * (hi - lo + 1))

    def chance(self, p):
        return self.rand() < p

    def choice(self, seq):
        return seq[int(self.rand() * len(seq)) % len(seq)]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class SkyOre:
    name: str = "diamond_ore"
    ratio: float = 5.0                # relative frequency weight (0 disables)


def _default_ores() -> List[SkyOre]:
    return [
        SkyOre("iron_ore", 26.0),
        SkyOre("redstone_ore", 16.0),
        SkyOre("gold_ore", 12.0),
        SkyOre("lapis_ore", 11.0),
        SkyOre("diamond_ore", 9.0),
        SkyOre("emerald_ore", 4.0),
    ]


@dataclass
class BatchEntry:
    """`count` clusters, each built for `spawn_islands` spawn islands."""
    spawn_islands: int = 12
    count: int = 1


@dataclass
class SkywarsConfig:
    name: str = "Skywars"
    seed: int = 0
    data_version: int = 3465
    version_name: str = "1.20.1"
    game_type: int = 0                # 0 survival, 2 adventure typical for skywars
    allow_commands: bool = True

    # Batch of cluster groups.
    batches: List[BatchEntry] = field(default_factory=lambda: [BatchEntry(12, 1)])

    # Island sizing (radii in blocks).
    main_size: int = 52
    middle_size: int = 21             # ~80% of spawn size
    spawn_size: int = 26              # max spawn radius; shrinks to keep gaps tight
    island_gap: int = 12              # target void gap (blocks) between island edges
    middle_ratio: float = 1.0         # how densely the middle ring is packed (0..1)

    base_y: int = 90                  # exact surface height of islands (see height_jitter)
    height_jitter: int = 0            # per-island surface height variation (0 = all level)
    surface_relief: float = 1.0       # gentle top undulation (0 = perfectly flat)
    taper_ratio: float = 0.62         # underside depth as a fraction of island radius
    edge_thickness: int = 5           # minimum solid thickness at the island rim

    cluster_spacing: int = 240        # >= 200 padding between clusters on the grid

    # Surface / decoration.
    varied_biomes: bool = True
    tree_density: float = 1.0
    rock_density: float = 1.0
    foliage_density: float = 1.0

    # Ores.
    ore_density: float = 1.0          # overall multiplier
    surface_ore: float = 1.0          # how much ore pokes to the surface
    ores: List[SkyOre] = field(default_factory=_default_ores)

    # Chests.
    chests_per_spawn: int = 5
    chests_per_middle: int = 5
    ender_chests_main: int = 10


# ---------------------------------------------------------------------------
# Biomes
# ---------------------------------------------------------------------------

# (biome id, preview colour, surface kind). surface kind drives block choice and
# which trees/decorations get placed.
BIOMES = [
    ("minecraft:plains", (127, 178, 56), "grass"),
    ("minecraft:forest", (89, 145, 40), "grass"),
    ("minecraft:birch_forest", (120, 163, 75), "grass"),
    ("minecraft:dark_forest", (60, 92, 34), "grass"),
    ("minecraft:taiga", (86, 116, 82), "taiga"),
    ("minecraft:jungle", (72, 156, 40), "jungle"),
    ("minecraft:savanna", (169, 174, 82), "savanna"),
    ("minecraft:snowy_plains", (240, 244, 250), "snowy"),
    ("minecraft:snowy_taiga", (200, 218, 210), "snowy"),
    ("minecraft:desert", (219, 205, 141), "desert"),
    ("minecraft:badlands", (197, 111, 54), "badlands"),
]

VOID_BIOME = "minecraft:the_void"

# Which log/leaf kinds a surface style uses for its trees.
SURFACE_TREES = {
    "grass": ["oak", "oak", "birch"],
    "taiga": ["spruce"],
    "snowy": ["spruce"],
    "badlands": [],
    "jungle": ["jungle", "jungle", "oak"],
    "savanna": ["oak"],
    "desert": [],
}


# ---------------------------------------------------------------------------
# Block palette
# ---------------------------------------------------------------------------

def _bkey(name, props=None):
    return (name, tuple(sorted((props or {}).items())))


class SkyBlocks:
    """Stable block palette for a worker. id 0 is air."""

    def __init__(self, data_version=3465):
        self._list: List[Tuple[str, dict]] = []
        self._index: Dict[tuple, int] = {}
        self.data_version = data_version

        self.air = self.get("minecraft:air")
        self.stone = self.get("minecraft:stone")
        self.dirt = self.get("minecraft:dirt")
        self.coarse_dirt = self.get("minecraft:coarse_dirt")
        self.podzol = self.get("minecraft:podzol")
        self.grass = self.get("minecraft:grass_block")
        self.grass_snow = self.get("minecraft:grass_block", {"snowy": "true"})
        self.sand = self.get("minecraft:sand")
        self.sandstone = self.get("minecraft:sandstone")
        self.red_sand = self.get("minecraft:red_sand")
        self.red_sandstone = self.get("minecraft:red_sandstone")
        self.terracotta = self.get("minecraft:terracotta")
        self.orange_terracotta = self.get("minecraft:orange_terracotta")
        self.yellow_terracotta = self.get("minecraft:yellow_terracotta")
        self.red_terracotta = self.get("minecraft:red_terracotta")
        self.brown_terracotta = self.get("minecraft:brown_terracotta")
        self.gravel = self.get("minecraft:gravel")
        self.cobble = self.get("minecraft:cobblestone")
        self.mossy_cobble = self.get("minecraft:mossy_cobblestone")
        self.andesite = self.get("minecraft:andesite")
        self.snow_block = self.get("minecraft:snow_block")
        self.snow_layer = self.get("minecraft:snow", {"layers": "1"})
        self.water = self.get("minecraft:water")

        sg = "minecraft:short_grass" if data_version >= 3698 else "minecraft:grass"
        self.short_grass = self.get(sg)
        self.fern = self.get("minecraft:fern")
        self.dead_bush = self.get("minecraft:dead_bush")
        self.cactus = self.get("minecraft:cactus")
        self.flowers = [self.get(f"minecraft:{n}") for n in (
            "dandelion", "poppy", "cornflower", "azure_bluet", "oxeye_daisy")]

        self.chest = self.get("minecraft:chest",
                              {"facing": "north", "type": "single", "waterlogged": "false"})
        self.ender_chest = self.get("minecraft:ender_chest",
                                    {"facing": "north", "waterlogged": "false"})

        self._logs: Dict[str, int] = {}
        self._leaves: Dict[str, int] = {}
        for kind in ("oak", "spruce", "birch", "jungle", "dark_oak"):
            self.log(kind)
            self.leaf(kind)
        self._ores: Dict[str, int] = {}

    def get(self, name, props=None):
        key = _bkey(name, props)
        idx = self._index.get(key)
        if idx is None:
            idx = len(self._list)
            self._index[key] = idx
            self._list.append((name, dict(props or {})))
        return idx

    def log(self, kind):
        if kind not in self._logs:
            self._logs[kind] = self.get(f"minecraft:{kind}_log", {"axis": "y"})
        return self._logs[kind]

    def leaf(self, kind):
        if kind not in self._leaves:
            self._leaves[kind] = self.get(f"minecraft:{kind}_leaves",
                                          {"persistent": "true"})
        return self._leaves[kind]

    def ore(self, name):
        if name not in self._ores:
            n = name if ":" in name else f"minecraft:{name}"
            self._ores[name] = self.get(n)
        return self._ores[name]

    def palette(self):
        return [(name, dict(props)) for (name, props) in self._list]


# ---------------------------------------------------------------------------
# Sparse chunk store (per worker / cluster)
# ---------------------------------------------------------------------------

class ChunkStore:
    """Sparse map of (cx, cz) -> band block array + biome map + block entities."""

    def __init__(self, biome_void_id):
        self.blocks: Dict[Tuple[int, int], np.ndarray] = {}
        self.biomes: Dict[Tuple[int, int], np.ndarray] = {}
        self.entities: Dict[Tuple[int, int], list] = {}
        self._void = biome_void_id

    def chunk(self, cx, cz):
        key = (cx, cz)
        arr = self.blocks.get(key)
        if arr is None:
            arr = np.zeros((BAND_H, 16, 16), dtype=np.int16)   # [y, z, x], air
            self.blocks[key] = arr
            self.biomes[key] = np.full((16, 16), self._void, dtype=np.int16)
            self.entities[key] = []
        return arr

    def add_entity(self, wx, wy, wz, comp):
        cx, cz = wx >> 4, wz >> 4
        self.chunk(cx, cz)
        self.entities[(cx, cz)].append(comp)


# ---------------------------------------------------------------------------
# Island stamping
# ---------------------------------------------------------------------------

@dataclass
class Island:
    wx: int
    wz: int
    radius: float
    surface_y: int
    depth: int
    biome_idx: int
    kind: str        # "main" | "middle" | "spawn"
    _biome_pid: int = 0   # biome palette id (filled in build_cluster)


def _value_noise2d(xs, zs, seed):
    """Cheap smooth value noise in [0,1] over integer-ish coords."""
    x0 = np.floor(xs).astype(np.int64)
    z0 = np.floor(zs).astype(np.int64)
    fx = xs - x0
    fz = zs - z0
    fx = fx * fx * (3 - 2 * fx)
    fz = fz * fz * (3 - 2 * fz)

    def h(ix, iz):
        n = (ix * 374761393 + iz * 668265263 + int(seed) * 1013904223) & _UINT32
        n = (n ^ (n >> 13)) & _UINT32
        n = (n * 1274126177) & _UINT32
        return ((n ^ (n >> 16)) & _UINT32) / float(_UINT32)

    c00 = h(x0, z0); c10 = h(x0 + 1, z0)
    c01 = h(x0, z0 + 1); c11 = h(x0 + 1, z0 + 1)
    a = c00 + (c10 - c00) * fx
    b = c01 + (c11 - c01) * fx
    return a + (b - a) * fz


def _surface_spec(blocks: SkyBlocks, style):
    """Per-style surface blocks. `rim` is used on the outer edge so gravity
    blocks (sand / red_sand) never form thin overhanging lips."""
    if style == "desert":
        return {"top": blocks.sand, "rim": blocks.sandstone,
                "sub": blocks.sandstone, "deep": blocks.sandstone}
    if style == "badlands":
        return {"top": blocks.red_sand, "rim": blocks.red_sandstone,
                "sub": blocks.terracotta, "deep": blocks.terracotta, "bands": True}
    if style == "snowy":
        # Solid snow blocks (no thin snow layer that hides ore / floats items).
        return {"top": blocks.snow_block, "rim": blocks.snow_block,
                "sub": blocks.dirt, "deep": blocks.dirt}
    return {"top": blocks.grass, "rim": blocks.grass,
            "sub": blocks.dirt, "deep": blocks.dirt}


def _badlands_bands(blocks):
    return [
        blocks.terracotta, blocks.terracotta, blocks.orange_terracotta,
        blocks.terracotta, blocks.yellow_terracotta, blocks.brown_terracotta,
        blocks.terracotta, blocks.red_terracotta,
    ]


def stamp_island(store: ChunkStore, blocks: SkyBlocks, cfg: SkywarsConfig,
                 isl: Island, rng: Rng):
    R = isl.radius
    style = BIOMES[isl.biome_idx][2]
    biome_pid = isl._biome_pid  # set by caller (id into the void+biome palette)
    spec = _surface_spec(blocks, style)
    bands = _badlands_bands(blocks) if spec.get("bands") else None
    dirt_depth = 4                # solid surface material below the top block

    edge_seed = _hash(isl.wx, isl.wz, cfg.seed, 7)
    spike_seed = _hash(isl.wx, isl.wz, cfg.seed, 19)
    relief_seed = _hash(isl.wx, isl.wz, cfg.seed, 41)
    relief_amp = float(cfg.surface_relief) * 1.25

    cx0 = (isl.wx - int(R) - 2) >> 4
    cx1 = (isl.wx + int(R) + 2) >> 4
    cz0 = (isl.wz - int(R) - 2) >> 4
    cz1 = (isl.wz + int(R) + 2) >> 4

    # Precompute ore placement probabilities per stone voxel.
    ore_ids, ore_probs = _ore_probs(blocks, cfg)

    yidx = np.arange(BAND_LO, BAND_HI + 1)[:, None, None]   # (BAND_H,1,1) world y

    surface_cols = []   # (wx, wz, surf_y) for decoration/chest passes

    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            arr = store.chunk(cx, cz)
            bio = store.biomes[(cx, cz)]
            xs = cx * 16 + np.arange(16)
            zs = cz * 16 + np.arange(16)
            X = np.broadcast_to(xs[None, :], (16, 16)).astype(np.float64)   # [z,x]
            Z = np.broadcast_to(zs[:, None], (16, 16)).astype(np.float64)
            dx = X - isl.wx
            dz = Z - isl.wz
            dist = np.sqrt(dx * dx + dz * dz)

            # Wobbly edge so islands aren't perfect circles.
            wob = _value_noise2d(X * 0.15, Z * 0.15, edge_seed)
            edge = R * (0.82 + 0.30 * wob)
            mask = dist <= edge
            if not mask.any():
                continue

            t = np.clip(dist / np.maximum(edge, 1e-6), 0.0, 1.0)
            # Mostly-flat PvP top with gentle low-frequency relief. The noise is
            # broad and rounded so the surface forms flat terraces rather than
            # jagged bumps -- natural looking but still fightable.
            flat_y = int(np.clip(isl.surface_y, BAND_LO + 3, BAND_HI - 3))
            if relief_amp > 0:
                # gentle small-scale undulation: a mid frequency so the surface
                # gets scattered low bumps rather than one big ramping staircase
                relief = _value_noise2d(X * 0.07, Z * 0.07, relief_seed)
                surf = flat_y + np.round((relief - 0.5) * 2.0 * relief_amp).astype(np.int64)
            else:
                surf = np.full((16, 16), flat_y, dtype=np.int64)
            surf = np.where(mask, surf, -9999)

            # Spiky underside. Every column keeps at least `edge_thickness` of
            # solid so the rim is meaty (no one-block dirt overhangs); the centre
            # spikes far down with feathered noise.
            spike = _value_noise2d(X * 0.22, Z * 0.22, spike_seed)
            thickness = (cfg.edge_thickness
                         + isl.depth * (1.0 - t) ** 1.55
                         + isl.depth * 0.4 * spike * (1.0 - t) ** 0.8)
            bottom = np.round(surf - thickness).astype(np.int64)
            bottom = np.clip(bottom, BAND_LO + 1, BAND_HI - 2)
            bottom = np.where(mask, bottom, 9999)

            # Solid column fill (stone) across the whole vertical band.
            ysolid = (yidx >= bottom[None]) & (yidx <= surf[None]) & mask[None]
            arr[ysolid] = blocks.stone

            # Scatter buried ores through the stone.
            if ore_ids:
                _scatter_ores(arr, ysolid, ore_ids, ore_probs,
                              _hash(cx, cz, cfg.seed, 101))

            # Surface + subsurface layering.
            zz, xx = np.nonzero(mask)
            for i in range(zz.size):
                z_i = zz[i]; x_i = xx[i]
                s = int(surf[z_i, x_i])
                b = int(bottom[z_i, x_i])
                si = s - BAND_LO
                if si < 0 or si >= BAND_H:
                    continue
                is_rim = t[z_i, x_i] > 0.80
                arr[si, z_i, x_i] = spec["rim"] if is_rim else spec["top"]
                # dirt / sub material below the top block
                for d in range(1, dirt_depth + 1):
                    yi = si - d
                    if yi >= 0 and (s - d) >= b:
                        if bands is not None:
                            arr[yi, z_i, x_i] = bands[(s - d) % len(bands)]
                        else:
                            arr[yi, z_i, x_i] = spec["sub"] if d < dirt_depth else spec["deep"]
                bio[z_i, x_i] = biome_pid
                surface_cols.append((int(X[z_i, x_i]), int(Z[z_i, x_i]), s))

            # Surface-poking ores (redstone/coal on the grass, like real maps).
            if ore_ids and cfg.surface_ore > 0:
                _surface_ore_poke(arr, mask, surf, ore_ids, ore_probs, cfg,
                                  _hash(cx, cz, cfg.seed, 202))

    return surface_cols


def _ore_probs(blocks: SkyBlocks, cfg: SkywarsConfig):
    active = [o for o in cfg.ores if o.ratio > 0 and o.name.strip()]
    if not active:
        return [], []
    ids = [blocks.ore(o.name) for o in active]
    total = sum(o.ratio for o in active)
    # Roughly ~9% of interior stone becomes ore at density 1.0, split by ratio.
    budget = 0.09 * cfg.ore_density
    probs = [budget * (o.ratio / total) for o in active]
    return ids, probs


def _scatter_ores(arr, stone_mask, ore_ids, ore_probs, seed):
    n = stone_mask.sum()
    if n == 0:
        return
    rs = np.random.RandomState(seed & 0x7FFFFFFF)
    roll = rs.random_sample(arr.shape)
    cursor = np.zeros(arr.shape, dtype=bool)  # already-claimed voxels
    lo = 0.0
    for oid, p in zip(ore_ids, ore_probs):
        hi = lo + p
        sel = stone_mask & (roll >= lo) & (roll < hi) & (~cursor)
        arr[sel] = oid
        cursor |= sel
        lo = hi


def _surface_ore_poke(arr, mask, surf, ore_ids, ore_probs, cfg, seed):
    rs = np.random.RandomState(seed & 0x7FFFFFFF)
    zz, xx = np.nonzero(mask)
    total_p = sum(ore_probs) * 0.7 * cfg.surface_ore
    if total_p <= 0:
        return
    for i in range(zz.size):
        if rs.random_sample() >= total_p:
            continue
        z_i = zz[i]; x_i = xx[i]
        si = int(surf[z_i, x_i]) - BAND_LO
        if si < 0 or si >= BAND_H:
            continue
        r = rs.random_sample() * sum(ore_probs)
        acc = 0.0
        for oid, p in zip(ore_ids, ore_probs):
            acc += p
            if r < acc:
                arr[si, z_i, x_i] = oid
                break


# ---------------------------------------------------------------------------
# Decoration: trees, rocks, foliage
# ---------------------------------------------------------------------------

def _set(store, blocks, wx, wy, wz, bid, replace_air_only=False):
    if wy < BAND_LO or wy > BAND_HI:
        return
    cx, cz = wx >> 4, wz >> 4
    arr = store.blocks.get((cx, cz))
    if arr is None:
        arr = store.chunk(cx, cz)
    lx, lz = wx & 15, wz & 15
    yi = wy - BAND_LO
    if replace_air_only and arr[yi, lz, lx] != AIR:
        return
    arr[yi, lz, lx] = bid


def _block_at(store, wx, wy, wz):
    if wy < BAND_LO or wy > BAND_HI:
        return AIR
    arr = store.blocks.get((wx >> 4, wz >> 4))
    if arr is None:
        return AIR
    return int(arr[wy - BAND_LO, wz & 15, wx & 15])


def _tree(store, blocks, wx, wz, base_y, kind, rng):
    trunk = rng.randint(4, 6) if kind != "jungle" else rng.randint(5, 8)
    log = blocks.log(kind)
    leaf = blocks.leaf(kind)
    for i in range(trunk):
        _set(store, blocks, wx, base_y + i, wz, log, True)
    top = base_y + trunk
    if kind == "spruce":
        # conical spruce
        r = 2
        for ly in range(top - 3, top + 1):
            rad = max(1, r)
            for dz in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    if abs(dx) + abs(dz) <= rad:
                        _set(store, blocks, wx + dx, ly, wz + dz, leaf, True)
            r -= 1
        _set(store, blocks, wx, top + 1, wz, leaf, True)
    else:
        for ly in (top - 1, top):
            rad = 2
            for dz in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    if dx * dx + dz * dz <= rad * rad + 1:
                        if abs(dx) == rad and abs(dz) == rad and rng.chance(0.5):
                            continue
                        _set(store, blocks, wx + dx, ly, wz + dz, leaf, True)
        for dz in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if abs(dx) + abs(dz) <= 1:
                    _set(store, blocks, wx + dx, top + 1, wz + dz, leaf, True)


def _rock(store, blocks, wx, wz, base_y, rng):
    mats = [blocks.cobble, blocks.mossy_cobble, blocks.andesite, blocks.stone]
    size = rng.randint(1, 2)
    for dz in range(-size, size + 1):
        for dx in range(-size, size + 1):
            if dx * dx + dz * dz > size * size + 1:
                continue
            h = rng.randint(1, 2)
            for i in range(h):
                _set(store, blocks, wx + dx, base_y + i, wz + dz,
                     rng.choice(mats), True)


def decorate_island(store, blocks, cfg, isl, surface_cols, rng):
    if not surface_cols:
        return
    style = BIOMES[isl.biome_idx][2]
    tree_kinds = SURFACE_TREES.get(style, [])
    area = math.pi * isl.radius * isl.radius

    # Ground blocks that plants / trees may sit on (never ore, stone, snow, etc.).
    soil_ids = {blocks.grass, blocks.grass_snow, blocks.dirt,
                blocks.coarse_dirt, blocks.podzol}
    sand_ids = {blocks.sand, blocks.red_sand}
    tree_ground = soil_ids | {blocks.snow_block}   # spruce is fine on snow

    def ground(wx, wz, sy):
        return _block_at(store, wx, sy, wz)

    # Trees (sparse by default -- roughly a third of the old density).
    n_trees = 0
    if tree_kinds and cfg.tree_density > 0:
        base = {"main": 0.0033, "middle": 0.0040, "spawn": 0.0033}[isl.kind]
        n_trees = int(area * base * cfg.tree_density * rng.uniform(0.6, 1.2))
        n_trees = min(n_trees, max(0, len(surface_cols) // 20))
    for _ in range(n_trees):
        wx, wz, sy = rng.choice(surface_cols)
        if ground(wx, wz, sy) in tree_ground:
            _tree(store, blocks, wx, wz, sy + 1, rng.choice(tree_kinds), rng)

    # Rocks / boulders (sparse) -- fine on any solid ground.
    n_rocks = int(area * 0.00133 * cfg.rock_density * rng.uniform(0.5, 1.2))
    for _ in range(n_rocks):
        wx, wz, sy = rng.choice(surface_cols)
        _rock(store, blocks, wx, wz, sy + 1, rng)

    # Foliage -- only ever on natural ground blocks (grass/dirt for plants,
    # sand for cactus), never on exposed ore or stone.
    if cfg.foliage_density > 0:
        n_foliage = int(area * 0.05 * cfg.foliage_density)
        for _ in range(n_foliage):
            wx, wz, sy = rng.choice(surface_cols)
            gid = ground(wx, wz, sy)
            top = sy + 1
            if style == "desert":
                if gid in sand_ids and rng.chance(0.25):
                    h = rng.randint(1, 3)
                    for i in range(h):
                        _set(store, blocks, wx, top + i, wz, blocks.cactus, True)
                elif gid in sand_ids and rng.chance(0.4):
                    _set(store, blocks, wx, top, wz, blocks.dead_bush, True)
            elif style == "badlands":
                if gid in (sand_ids | soil_ids) and rng.chance(0.35):
                    _set(store, blocks, wx, top, wz, blocks.dead_bush, True)
            elif style == "snowy":
                pass   # snow surface: keep it clean (no floating grass)
            elif gid in soil_ids:
                r = rng.rand()
                if r < 0.6:
                    _set(store, blocks, wx, top, wz, blocks.short_grass, True)
                elif r < 0.85:
                    _set(store, blocks, wx, top, wz, rng.choice(blocks.flowers), True)
                else:
                    _set(store, blocks, wx, top, wz, blocks.fern, True)


# ---------------------------------------------------------------------------
# Chests
# ---------------------------------------------------------------------------

def _chest_entity(kind, wx, wy, wz):
    comp = nbt.Compound()
    comp["id"] = nbt.String(f"minecraft:{kind}")
    comp["x"] = nbt.Int(int(wx))
    comp["y"] = nbt.Int(int(wy))
    comp["z"] = nbt.Int(int(wz))
    comp["keepPacked"] = nbt.Byte(0)
    if kind == "chest":
        comp["Items"] = nbt.List(nbt.Compound.tag_id, [])
    return comp


def _place_chest(store, blocks, kind, wx, wy, wz):
    bid = blocks.chest if kind == "chest" else blocks.ender_chest
    _set(store, blocks, wx, wy, wz, bid)
    store.add_entity(wx, wy, wz, _chest_entity(kind, wx, wy, wz))


def _surface_y_at(store, wx, wz):
    """Highest solid (non-air) y at a column, or None."""
    cx, cz = wx >> 4, wz >> 4
    arr = store.blocks.get((cx, cz))
    if arr is None:
        return None
    col = arr[:, wz & 15, wx & 15]
    nz = np.nonzero(col != AIR)[0]
    if nz.size == 0:
        return None
    return int(nz[-1]) + BAND_LO


def place_island_chests(store, blocks, cfg, isl, rng):
    """Evenly scatter chests across the island top. Returns chest positions."""
    positions = []
    if isl.kind == "main":
        count = cfg.ender_chests_main
        kind = "ender_chest"
    elif isl.kind == "middle":
        count = cfg.chests_per_middle
        kind = "chest"
    else:
        count = cfg.chests_per_spawn
        kind = "chest"
    if count <= 0:
        return positions

    max_r = isl.radius * 0.82
    # Spawn islands keep a small clear disc at the centre (the player spawn).
    keep_clear = 3.0 if isl.kind == "spawn" else 0.0
    # Target separation for an even (blue-noise-ish) spread over the disc.
    min_sep = max(2.0, 0.9 * max_r / math.sqrt(max(1, count)))

    placed_pts = []
    tries = 0
    max_tries = count * 40
    while len(placed_pts) < count and tries < max_tries:
        tries += 1
        # uniform over the disc: r = R*sqrt(u)
        rr = max_r * math.sqrt(rng.rand())
        if rr < keep_clear:
            continue
        ang = rng.uniform(0, 2 * math.pi)
        wx = int(round(isl.wx + math.cos(ang) * rr))
        wz = int(round(isl.wz + math.sin(ang) * rr))
        # relax the separation requirement if we are running out of tries
        sep = min_sep * (1.0 if tries < max_tries * 0.6 else 0.5)
        if any((wx - px) ** 2 + (wz - pz) ** 2 < sep * sep for px, pz in placed_pts):
            continue
        sy = _surface_y_at(store, wx, wz)
        if sy is None:
            continue
        _place_chest(store, blocks, kind, wx, sy + 1, wz)
        positions.append((wx, sy + 1, wz))
        placed_pts.append((wx, wz))
    return positions


# ---------------------------------------------------------------------------
# Cluster geometry
# ---------------------------------------------------------------------------

def _ring_radius(count, item_r, gap):
    if count <= 0:
        return 0.0
    circ = count * (2 * item_r + gap)
    return circ / (2 * math.pi)


def plan_cluster(cfg: SkywarsConfig, spawn_count: int, cx: int, cz: int,
                 cluster_seed: int) -> Tuple[List[Island], dict]:
    """Return islands (positioned at world coords) + a positions dict."""
    rng = Rng(cluster_seed, 0xC1057E5)
    Rmain = float(cfg.main_size)
    Rm = float(cfg.middle_size)
    Rs_cfg = float(cfg.spawn_size)
    gap = float(max(4, cfg.island_gap))   # target void gap between island edges
    Rs_min = 9.0                          # smallest a spawn island may shrink to
    two_pi = 2 * math.pi

    # Exactly ONE ring of middle islands. Ideally it hugs the main island with a
    # `gap` void, and the spawn ring hugs it with a `gap` void -- giving the
    # consistent spawn -> middle -> main spacing we want. As the spawn count
    # grows we first shrink the spawn islands so the ring stays tight; once they
    # hit the minimum size we let the ring grow and slide the middle ring outward
    # so both gaps stay equal (proportional) instead of one ballooning.
    Rmid_hug = Rmain + Rm + gap
    B = Rmid_hug + Rm + gap               # spawn-ring radius if gaps are both `gap`
    denom = 2 * spawn_count - two_pi
    Rs = Rs_cfg
    if denom > 1e-6:
        # spawn radius that fits `spawn_count` with tangential AND radial gap == gap
        Rs = min(Rs_cfg, (two_pi * B - spawn_count * gap) / denom)

    if Rs >= Rs_min:
        Rmid = Rmid_hug
        Rring = Rmid + Rm + gap + Rs
    else:
        Rs = Rs_min
        Rring = max(Rmid_hug + Rm + gap + Rs, _ring_radius(spawn_count, Rs, gap))
        Rmid = max(Rmid_hug, 0.5 * (Rring - Rs + Rmain))   # balance both gaps

    # Dense single middle ring.
    n_mid = max(4, int(math.floor(two_pi * Rmid / (2 * Rm + gap))))
    n_mid = max(4, min(n_mid, int(round(n_mid * cfg.middle_ratio))))

    islands: List[Island] = []

    def pick_biome(salt):
        if not cfg.varied_biomes:
            return 0
        return rng.randint(0, len(BIOMES) - 1)

    def surf_y():
        if cfg.height_jitter <= 0:
            return cfg.base_y
        return cfg.base_y + rng.randint(-cfg.height_jitter, cfg.height_jitter)

    def depth_for(radius):
        return max(cfg.edge_thickness + 6, int(round(radius * cfg.taper_ratio)))

    # Main.
    main_y = surf_y()
    islands.append(Island(cx, cz, Rmain, main_y, depth_for(Rmain),
                          pick_biome(1), "main"))

    # Single middle ring, evenly spaced.
    salt = 10
    a0 = rng.uniform(0, 2 * math.pi)
    for i in range(n_mid):
        ang = a0 + 2 * math.pi * i / n_mid
        wx = int(round(cx + math.cos(ang) * Rmid))
        wz = int(round(cz + math.sin(ang) * Rmid))
        rad = Rm * rng.uniform(0.99, 1.01)
        islands.append(Island(wx, wz, rad, surf_y(), depth_for(rad),
                              pick_biome(salt), "middle"))
        salt += 1

    # Spawn ring, aligned to the middle ring so spawns sit near a middle island.
    spawn_islands: List[Island] = []
    b0 = a0                                # share the start angle with the middle ring
    for i in range(spawn_count):
        ang = b0 + 2 * math.pi * i / spawn_count
        wx = int(round(cx + math.cos(ang) * Rring))
        wz = int(round(cz + math.sin(ang) * Rring))
        rad = Rs * rng.uniform(0.98, 1.02)
        isl = Island(wx, wz, rad, surf_y(), depth_for(rad),
                     pick_biome(100 + i), "spawn")
        islands.append(isl)
        spawn_islands.append(isl)

    cluster_radius = Rring + Rs + 6
    meta = {
        "spawn_count": spawn_count,
        "cluster_radius": cluster_radius,
        "center": [cx, main_y + 1, cz],
        "_spawn_islands": spawn_islands,
        "_main_y": main_y,
    }
    return islands, meta


# ---------------------------------------------------------------------------
# Building a cluster (worker unit)
# ---------------------------------------------------------------------------

def build_cluster(cfg: SkywarsConfig, spawn_count: int, cx: int, cz: int,
                  cluster_index: int, label: str):
    """Generate one cluster; return compressed chunks, positions dict, preview footprints."""
    cluster_seed = _hash(cfg.seed, cx, cz, cluster_index, spawn_count)
    blocks = SkyBlocks(cfg.data_version)

    # Biome palette (void first, then any biome referenced).
    biome_list = [VOID_BIOME] + [b[0] for b in BIOMES]
    biome_index = {name: i for i, name in enumerate(biome_list)}
    void_pid = biome_index[VOID_BIOME]

    store = ChunkStore(void_pid)
    islands, meta = plan_cluster(cfg, spawn_count, cx, cz, cluster_seed)

    # 1) Carve all island terrain first.
    footprints = []   # (wx, wz, radius, color, kind) for preview
    island_cols = []
    for isl in islands:
        isl._biome_pid = biome_index[BIOMES[isl.biome_idx][0]]
        rng = Rng(cluster_seed, isl.wx, isl.wz, 5)
        island_cols.append(stamp_island(store, blocks, cfg, isl, rng))
        footprints.append((isl.wx, isl.wz, isl.radius,
                           BIOMES[isl.biome_idx][1], isl.kind))

    # 2) Chests on the clean solid surface (before foliage/trees).
    spawn_points = []
    for isl in islands:
        crng = Rng(cluster_seed, isl.wx, isl.wz, 77)
        place_island_chests(store, blocks, cfg, isl, crng)
        if isl.kind == "spawn":
            sy = _surface_y_at(store, isl.wx, isl.wz)
            if sy is None:
                sy = isl.surface_y
            spawn_points.append([isl.wx, sy + 1, isl.wz])

    # 3) Trees / rocks / foliage last (air-only, so chests survive).
    for isl, cols in zip(islands, island_cols):
        decorate_island(store, blocks, cfg, isl, cols,
                        Rng(cluster_seed, isl.wx, isl.wz, 9))

    positions = {
        "cluster_index": cluster_index,
        "label": label,
        "spawn_island_count": spawn_count,
        "world_offset": [cx, cz],
        "center": meta["center"],
        "spawn_points": spawn_points,
    }

    # Serialize touched chunks.
    block_palette = blocks.palette()
    compressed = []
    full = np.zeros((WORLD_HEIGHT, 16, 16), dtype=np.int16)
    band_off = BAND_LO - MIN_Y
    for (ccx, ccz), band in store.blocks.items():
        full[:] = AIR
        full[band_off:band_off + BAND_H] = band
        bio = store.biomes[(ccx, ccz)]
        ents = store.entities[(ccx, ccz)]
        root = build_chunk(ccx, ccz, full, bio, block_palette, biome_list,
                           cfg.data_version, MIN_SECTION, N_SECTIONS,
                           block_entities=ents if ents else None)
        compressed.append((ccx, ccz, nbt.to_zlib("", root)))

    return {
        "compressed": compressed,
        "positions": positions,
        "footprints": footprints,
        "chunks": len(compressed),
    }


def _cluster_worker(args):
    cfg, spawn_count, cx, cz, cluster_index, label = args
    return build_cluster(cfg, spawn_count, cx, cz, cluster_index, label)


# ---------------------------------------------------------------------------
# level.dat (void world)
# ---------------------------------------------------------------------------

def _build_level_dat(cfg: SkywarsConfig, spawn):
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
    Data["DayTime"] = nbt.Long(6000)
    Data["clearWeatherTime"] = nbt.Int(1000000)
    Data["rainTime"] = nbt.Int(1000000)
    Data["thunderTime"] = nbt.Int(1000000)
    Data["raining"] = nbt.Byte(0)
    Data["thundering"] = nbt.Byte(0)
    Data["WasModded"] = nbt.Byte(0)

    Data["SpawnX"] = nbt.Int(int(spawn[0]))
    Data["SpawnY"] = nbt.Int(int(spawn[1]))
    Data["SpawnZ"] = nbt.Int(int(spawn[2]))
    Data["SpawnAngle"] = nbt.Float(0.0)

    dp = nbt.Compound()
    dp["Enabled"] = nbt.List(nbt.String.tag_id, [nbt.String("vanilla")])
    dp["Disabled"] = nbt.List(0, [])
    Data["DataPacks"] = dp

    # Void superflat: no layers => empty world outside our written chunks.
    flat_settings = nbt.Compound()
    flat_settings["biome"] = nbt.String("minecraft:the_void")
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
        "doMobSpawning": "false", "keepInventory": "true",
        "doDaylightCycle": "false", "doWeatherCycle": "false",
        "doFireTick": "false", "mobGriefing": "false", "spawnRadius": "0",
        "fallDamage": "true", "naturalRegeneration": "false",
    }.items():
        gr[k] = nbt.String(v)
    Data["GameRules"] = gr

    root = nbt.Compound()
    root["Data"] = Data
    return nbt.to_gzip("", root)


# ---------------------------------------------------------------------------
# Preview rasterization
# ---------------------------------------------------------------------------

def _render_preview(footprints_all, bounds, size=768):
    xmin, xmax, zmin, zmax = bounds
    span = max(xmax - xmin, zmax - zmin, 1)
    scale = size / span
    W = max(1, int((xmax - xmin) * scale) + 1)
    H = max(1, int((zmax - zmin) * scale) + 1)
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:] = (11, 13, 18)

    for (wx, wz, r, color, kind) in footprints_all:
        px = (wx - xmin) * scale
        pz = (wz - zmin) * scale
        pr = max(1.0, r * scale)
        x0 = max(0, int(px - pr)); x1 = min(W, int(px + pr) + 1)
        z0 = max(0, int(pz - pr)); z1 = min(H, int(pz + pr) + 1)
        if x1 <= x0 or z1 <= z0:
            continue
        yy = np.arange(z0, z1)[:, None]
        xx = np.arange(x0, x1)[None, :]
        d = (xx - px) ** 2 + (yy - pz) ** 2
        m = d <= pr * pr
        col = np.array(color, dtype=np.uint8)
        if kind == "main":
            col = np.clip(col.astype(int) + 30, 0, 255).astype(np.uint8)
        img[z0:z1, x0:x1][m] = col
        # rim highlight
        rim = (d <= pr * pr) & (d >= (pr - 1.4) ** 2)
        img[z0:z1, x0:x1][rim] = np.clip(col.astype(int) - 40, 0, 255).astype(np.uint8)
    return img


# ---------------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------------

def _expand_clusters(cfg: SkywarsConfig):
    """Flatten batches into a list of (spawn_count,) cluster specs."""
    specs = []
    for b in cfg.batches:
        for _ in range(max(0, int(b.count))):
            specs.append(max(2, int(b.spawn_islands)))
    if not specs:
        specs = [12]
    return specs


def generate_skywars_world(cfg: SkywarsConfig, out_dir: str,
                           progress: Optional[Callable[[float, str], None]] = None,
                           workers: Optional[int] = None,
                           preview_progress: Optional[Callable[[np.ndarray], None]] = None):
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

    report(0.0, "Planning cluster grid...")
    specs = _expand_clusters(cfg)
    n = len(specs)

    # Cell size: fit the largest cluster in every cell, plus spacing padding.
    radii = []
    for sc in specs:
        _, meta = plan_cluster(cfg, sc, 0, 0, _hash(cfg.seed, sc, 0))
        radii.append(meta["cluster_radius"])
    max_diam = 2 * max(radii)
    cell = int(max_diam + max(200, cfg.cluster_spacing))

    cols = int(math.ceil(math.sqrt(n)))
    placements = []
    for i, sc in enumerate(specs):
        gx = i % cols
        gz = i // cols
        cx = int(gx * cell + cell // 2)
        cz = int(gz * cell + cell // 2)
        label = f"cluster_{i:02d}_{sc}islands"
        placements.append((cfg, sc, cx, cz, i, label))

    worker_count = _resolve_workers(workers, n)
    report(0.02, f"Generating {n} cluster(s) on {worker_count} worker(s)...")

    region_writers: Dict[Tuple[int, int], RegionWriter] = {}
    all_positions = []
    all_footprints = []
    done = 0

    def consume(res):
        nonlocal done
        for (ccx, ccz, payload) in res["compressed"]:
            key = (ccx >> 5, ccz >> 5)
            rw = region_writers.get(key)
            if rw is None:
                rw = RegionWriter(key[0], key[1])
                region_writers[key] = rw
            rw.add_compressed_chunk(ccx, ccz, payload)
        all_positions.append(res["positions"])
        all_footprints.extend(res["footprints"])
        done += 1
        report(0.02 + 0.82 * done / n, f"Built {done}/{n} clusters")

    if worker_count == 1 or n == 1:
        for job in placements:
            consume(_cluster_worker(job))
            if preview_progress is not None:
                preview_progress(_current_preview(all_footprints, placements, radii, cell, cols, n))
    else:
        ctx = mp.get_context("spawn") if threading.current_thread() is not threading.main_thread() else mp.get_context()
        with ProcessPoolExecutor(max_workers=worker_count, mp_context=ctx) as pool:
            for res in pool.map(_cluster_worker, placements):
                consume(res)
                if preview_progress is not None:
                    preview_progress(_current_preview(all_footprints, placements, radii, cell, cols, n))

    report(0.88, "Writing region files...")
    for key in sorted(region_writers):
        rw = region_writers[key]
        with open(os.path.join(region_dir, rw.filename), "wb") as fp:
            fp.write(rw.to_bytes())

    # Spawn = cluster 0's centre.
    spawn = all_positions[0]["center"] if all_positions else [0, cfg.base_y + 1, 0]

    report(0.94, "Writing level.dat...")
    with open(os.path.join(out_dir, "level.dat"), "wb") as fp:
        fp.write(_build_level_dat(cfg, spawn))
    with open(os.path.join(out_dir, "session.lock"), "wb") as fp:
        fp.write(b"\xe2\x98\x83")

    bounds = _world_bounds(placements, radii, cell, cols)
    preview = _render_preview(all_footprints, bounds)

    report(1.0, f"Done in {time.time() - t0:.1f}s")
    if preview_progress is not None:
        preview_progress(preview)

    # positions sorted by cluster index
    all_positions.sort(key=lambda p: p["cluster_index"])
    total_spawns = sum(p["spawn_island_count"] for p in all_positions)
    return {
        "clusters": n,
        "spawn_islands_total": total_spawns,
        "regions": len(region_writers),
        "workers": worker_count,
        "spawn": spawn,
        "world_size": int(cols * cell),
        "seconds": round(time.time() - t0, 1),
        "preview": preview,
        "positions": all_positions,
    }


def _world_bounds(placements, radii, cell, cols):
    xs = []
    zs = []
    for (_, _, cx, cz, i, _), r in zip(placements, radii):
        xs += [cx - r, cx + r]
        zs += [cz - r, cz + r]
    pad = 20
    return (min(xs) - pad, max(xs) + pad, min(zs) - pad, max(zs) + pad)


def _current_preview(footprints, placements, radii, cell, cols, n):
    bounds = _world_bounds(placements, radii, cell, cols)
    return _render_preview(footprints, bounds)


def _resolve_workers(requested, n_clusters):
    cpu = os.cpu_count() or 1
    if requested is None or int(requested) <= 0:
        requested = cpu
    requested = max(1, int(requested))
    return max(1, min(requested, n_clusters))
