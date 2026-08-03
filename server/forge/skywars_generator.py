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
from .bedrock_writer import build_chunk_entries as build_bedrock_chunk_entries
from .bedrock_writer import write_world as write_bedrock_world
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
    output_format: str = "java"       # "java" (Anvil) or "bedrock" (PocketMine LevelDB)

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

# (biome id, preview colour, art style).  The art style is deliberately more
# specific than a vanilla biome category: it drives a coordinated surface,
# cliff and structure palette so two green islands do not become the same grass
# disc with a differently coloured biome tint.
BIOMES = [
    ("minecraft:plains", (127, 178, 56), "meadow"),
    ("minecraft:forest", (89, 145, 40), "woodland"),
    ("minecraft:birch_forest", (120, 163, 75), "birch_grove"),
    ("minecraft:dark_forest", (60, 92, 34), "dark_woodland"),
    ("minecraft:taiga", (86, 116, 82), "taiga"),
    ("minecraft:jungle", (72, 156, 40), "jungle"),
    ("minecraft:savanna", (169, 174, 82), "savanna"),
    ("minecraft:snowy_plains", (240, 244, 250), "snowy"),
    ("minecraft:snowy_taiga", (200, 218, 210), "snowy_grove"),
    ("minecraft:desert", (219, 205, 141), "desert"),
    ("minecraft:badlands", (197, 111, 54), "badlands"),
]

VOID_BIOME = "minecraft:the_void"

# Which log/leaf kinds a surface style uses for its trees.
SURFACE_TREES = {
    "meadow": [("oak", "round"), ("oak", "windswept"), ("birch", "columnar")],
    "woodland": [("oak", "round"), ("oak", "forked"),
                  ("birch", "columnar"), ("dark_oak", "layered")],
    "birch_grove": [("birch", "columnar"), ("birch", "forked"),
                     ("oak", "round")],
    "dark_woodland": [("dark_oak", "layered"), ("dark_oak", "arched"),
                       ("oak", "forked")],
    "taiga": [("spruce", "fir"), ("spruce", "fir"), ("pine", "umbrella")],
    "snowy": [("spruce", "fir"), ("pine", "umbrella")],
    "snowy_grove": [("spruce", "fir"), ("spruce", "snow_fir"),
                     ("pine", "umbrella")],
    "badlands": [],
    "jungle": [("jungle", "canopy"), ("jungle", "palm"), ("oak", "forked")],
    "savanna": [("oak", "flat_canopy"), ("oak", "windswept")],
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
        self.red_terracotta = self.get("minecraft:red_terracotta")
        self.brown_terracotta = self.get("minecraft:brown_terracotta")
        self.gray_terracotta = self.get("minecraft:gray_terracotta")
        self.green_terracotta = self.get("minecraft:green_terracotta")
        self.lime_terracotta = self.get("minecraft:lime_terracotta")
        self.cyan_terracotta = self.get("minecraft:cyan_terracotta")
        for color in ("white", "light_gray", "gray", "brown", "red", "green", "cyan"):
            setattr(self, f"{color}_concrete", self.get(f"minecraft:{color}_concrete"))
        for color in ("white", "light_gray", "gray", "brown", "red", "orange",
                      "green", "cyan", "light_blue", "blue"):
            setattr(self, f"{color}_wool", self.get(f"minecraft:{color}_wool"))
        self.gravel = self.get("minecraft:gravel")
        self.cobble = self.get("minecraft:cobblestone")
        self.mossy_cobble = self.get("minecraft:mossy_cobblestone")
        self.andesite = self.get("minecraft:andesite")
        self.diorite = self.get("minecraft:diorite")
        self.granite = self.get("minecraft:granite")
        self.tuff = self.get("minecraft:tuff")
        self.calcite = self.get("minecraft:calcite")
        self.deepslate = self.get("minecraft:deepslate", {"axis": "y"})
        self.moss = self.get("minecraft:moss_block")
        self.rooted_dirt = self.get("minecraft:rooted_dirt")
        self.clay = self.get("minecraft:clay")
        self.smooth_sandstone = self.get("minecraft:smooth_sandstone")
        self.cut_sandstone = self.get("minecraft:cut_sandstone")
        self.smooth_red_sandstone = self.get("minecraft:smooth_red_sandstone")
        self.cut_red_sandstone = self.get("minecraft:cut_red_sandstone")
        self.packed_ice = self.get("minecraft:packed_ice")
        self.blue_ice = self.get("minecraft:blue_ice")
        self.snow_block = self.get("minecraft:snow_block")
        self.snow_layer = self.get("minecraft:snow", {"layers": "1"})
        self.water = self.get("minecraft:water")

        sg = "minecraft:short_grass" if data_version >= 3698 else "minecraft:grass"
        self.short_grass = self.get(sg)
        self.fern = self.get("minecraft:fern")
        self.dead_bush = self.get("minecraft:dead_bush")
        self.cactus = self.get("minecraft:cactus")
        self.brown_mushroom = self.get("minecraft:brown_mushroom")
        self.red_mushroom = self.get("minecraft:red_mushroom")
        self.flowers = [self.get(f"minecraft:{n}") for n in (
            "dandelion", "poppy", "cornflower", "azure_bluet", "oxeye_daisy")]

        self.chest = self.get("minecraft:chest",
                              {"facing": "north", "type": "single", "waterlogged": "false"})
        self.ender_chest = self.get("minecraft:ender_chest",
                                    {"facing": "north", "waterlogged": "false"})
        self.chest_ids = {self.chest, self.ender_chest}

        self._logs: Dict[Tuple[str, str], int] = {}
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

    def log(self, kind, axis="y"):
        # "pine" is a structure silhouette, not a separate Minecraft wood set.
        wood = "spruce" if kind == "pine" else kind
        key = (wood, axis)
        if key not in self._logs:
            self._logs[key] = self.get(f"minecraft:{wood}_log", {"axis": axis})
        return self._logs[key]

    def leaf(self, kind):
        if kind == "pine":
            kind = "spruce"
        if kind not in self._leaves:
            self._leaves[kind] = self.get(f"minecraft:{kind}_leaves",
                                          {"persistent": "true"})
        return self._leaves[kind]

    def ore(self, name):
        if name not in self._ores:
            n = name if ":" in name else f"minecraft:{name}"
            self._ores[name] = self.get(n)
        return self._ores[name]

    def chest_block(self, kind, facing):
        if kind == "chest":
            bid = self.get("minecraft:chest", {
                "facing": facing, "type": "single", "waterlogged": "false"})
        else:
            bid = self.get("minecraft:ender_chest", {
                "facing": facing, "waterlogged": "false"})
        self.chest_ids.add(bid)
        return bid

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
        self.bedrock_entities: Dict[Tuple[int, int], list] = {}
        self._void = biome_void_id

    def chunk(self, cx, cz):
        key = (cx, cz)
        arr = self.blocks.get(key)
        if arr is None:
            arr = np.zeros((BAND_H, 16, 16), dtype=np.int16)   # [y, z, x], air
            self.blocks[key] = arr
            self.biomes[key] = np.full((16, 16), self._void, dtype=np.int16)
            self.entities[key] = []
            self.bedrock_entities[key] = []
        return arr

    def add_entity(self, wx, wy, wz, comp, kind=None):
        cx, cz = wx >> 4, wz >> 4
        self.chunk(cx, cz)
        self.entities[(cx, cz)].append(comp)
        if kind is not None:
            self.bedrock_entities[(cx, cz)].append((kind, int(wx), int(wy), int(wz)))


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


def _surface_spec(blocks: SkyBlocks, style, variant=0):
    """Return coordinated artist palettes nested inside broad surface regions.

    Low-frequency noise chooses a region; higher-frequency, still-correlated
    noise chooses a close-colour material inside that region. Repeated entries
    are intentional weights, like an artist keeping one dominant paint while
    stippling compatible concrete, wool and terracotta through it.
    """
    brown_soil = (blocks.coarse_dirt, blocks.coarse_dirt, blocks.coarse_dirt,
                  blocks.dirt, blocks.dirt, blocks.dirt,
                  blocks.brown_concrete, blocks.terracotta,
                  blocks.brown_wool)
    rooted_soil = (blocks.rooted_dirt, blocks.rooted_dirt,
                   blocks.coarse_dirt, blocks.coarse_dirt,
                   blocks.dirt, blocks.dirt,
                   blocks.brown_terracotta, blocks.brown_concrete)
    specs = {
        "meadow": dict(
            surface_palettes=(
                (blocks.grass, blocks.grass, blocks.grass, blocks.grass,
                 blocks.green_concrete, blocks.green_wool, blocks.lime_terracotta),
                brown_soil,
                (blocks.grass, blocks.grass, blocks.green_terracotta,
                 blocks.lime_terracotta, blocks.green_concrete, blocks.coarse_dirt)),
            rim_palette=(blocks.grass, blocks.grass, blocks.coarse_dirt,
                         blocks.terracotta, blocks.green_terracotta),
            sub_palettes=(brown_soil, rooted_soil, brown_soil),
            geology=(blocks.stone, blocks.andesite, blocks.tuff)),
        "woodland": dict(
            surface_palettes=(
                (blocks.grass, blocks.grass, blocks.grass, blocks.green_concrete,
                 blocks.green_wool, blocks.green_terracotta),
                (blocks.coarse_dirt, blocks.coarse_dirt, blocks.coarse_dirt,
                 blocks.dirt, blocks.dirt, blocks.dirt, blocks.rooted_dirt,
                 blocks.brown_concrete, blocks.brown_wool, blocks.terracotta),
                (blocks.grass, blocks.rooted_dirt, blocks.green_wool,
                 blocks.brown_terracotta, blocks.coarse_dirt)),
            rim_palette=(blocks.grass, blocks.rooted_dirt, blocks.coarse_dirt,
                         blocks.brown_concrete, blocks.green_terracotta),
            sub_palettes=(rooted_soil, brown_soil, rooted_soil),
            geology=(blocks.stone, blocks.andesite, blocks.tuff)),
        "birch_grove": dict(
            surface_palettes=(
                (blocks.grass, blocks.grass, blocks.grass, blocks.lime_terracotta,
                 blocks.green_wool, blocks.green_concrete),
                brown_soil,
                (blocks.grass, blocks.grass, blocks.white_concrete,
                 blocks.light_gray_concrete, blocks.white_wool, blocks.diorite)),
            rim_palette=(blocks.grass, blocks.coarse_dirt, blocks.light_gray_concrete,
                         blocks.terracotta, blocks.diorite),
            sub_palettes=(brown_soil, rooted_soil, brown_soil),
            geology=(blocks.stone, blocks.diorite, blocks.andesite)),
        "dark_woodland": dict(
            surface_palettes=(
                (blocks.podzol, blocks.podzol, blocks.podzol, blocks.coarse_dirt,
                 blocks.brown_concrete, blocks.brown_wool, blocks.dirt),
                (blocks.podzol, blocks.podzol, blocks.green_terracotta,
                 blocks.green_concrete, blocks.green_wool),
                (blocks.coarse_dirt, blocks.podzol, blocks.gray_concrete,
                 blocks.brown_terracotta, blocks.terracotta)),
            rim_palette=(blocks.podzol, blocks.coarse_dirt, blocks.brown_concrete,
                         blocks.gray_terracotta, blocks.deepslate),
            sub_palettes=(rooted_soil, brown_soil, rooted_soil),
            geology=(blocks.stone, blocks.tuff, blocks.deepslate)),
        "taiga": dict(
            surface_palettes=(
                (blocks.podzol, blocks.podzol, blocks.coarse_dirt, blocks.rooted_dirt,
                 blocks.green_terracotta, blocks.gray_wool),
                brown_soil,
                (blocks.podzol, blocks.andesite, blocks.gray_concrete,
                 blocks.light_gray_wool, blocks.coarse_dirt)),
            rim_palette=(blocks.podzol, blocks.coarse_dirt, blocks.andesite,
                         blocks.gray_terracotta, blocks.terracotta),
            sub_palettes=(rooted_soil, brown_soil, rooted_soil),
            geology=(blocks.stone, blocks.andesite, blocks.tuff)),
        "jungle": dict(
            surface_palettes=(
                (blocks.grass, blocks.grass, blocks.grass, blocks.lime_terracotta,
                 blocks.green_concrete, blocks.green_wool, blocks.lime_terracotta),
                (blocks.coarse_dirt, blocks.coarse_dirt,
                 blocks.dirt, blocks.dirt, blocks.rooted_dirt,
                 blocks.brown_concrete, blocks.green_terracotta, blocks.brown_wool),
                (blocks.grass, blocks.green_wool, blocks.green_concrete,
                 blocks.rooted_dirt, blocks.green_terracotta)),
            rim_palette=(blocks.grass, blocks.rooted_dirt, blocks.coarse_dirt,
                         blocks.green_terracotta, blocks.brown_concrete),
            sub_palettes=(rooted_soil, brown_soil, rooted_soil),
            geology=(blocks.stone, blocks.tuff, blocks.andesite)),
        "savanna": dict(
            surface_palettes=(
                (blocks.grass, blocks.grass, blocks.grass, blocks.grass,
                 blocks.green_terracotta, blocks.green_concrete, blocks.green_wool),
                (blocks.coarse_dirt, blocks.coarse_dirt, blocks.coarse_dirt,
                 blocks.dirt, blocks.dirt, blocks.dirt,
                 blocks.terracotta, blocks.brown_concrete, blocks.brown_wool),
                (blocks.coarse_dirt, blocks.coarse_dirt, blocks.terracotta,
                 blocks.brown_concrete, blocks.brown_wool, blocks.granite)),
            rim_palette=(blocks.coarse_dirt, blocks.terracotta,
                         blocks.brown_concrete, blocks.brown_wool, blocks.granite),
            sub_palettes=(brown_soil, brown_soil,
                          (blocks.terracotta, blocks.brown_terracotta,
                           blocks.coarse_dirt, blocks.brown_concrete)),
            geology=(blocks.stone, blocks.granite, blocks.andesite)),
        "snowy": dict(
            surface_palettes=(
                (blocks.snow_block, blocks.snow_block, blocks.snow_block,
                 blocks.white_concrete, blocks.white_wool, blocks.light_gray_concrete),
                (blocks.light_blue_wool, blocks.light_blue_wool, blocks.blue_wool,
                 blocks.cyan_terracotta, blocks.cyan_concrete),
                (blocks.snow_block, blocks.light_gray_concrete,
                 blocks.white_concrete, blocks.light_blue_wool)),
            rim_palette=(blocks.snow_block, blocks.white_concrete,
                         blocks.gray_concrete, blocks.blue_wool),
            sub_palettes=((blocks.snow_block, blocks.white_concrete,
                           blocks.light_gray_concrete, blocks.dirt),
                          rooted_soil, rooted_soil),
            geology=(blocks.stone, blocks.diorite, blocks.packed_ice)),
        "snowy_grove": dict(
            surface_palettes=(
                (blocks.snow_block, blocks.snow_block, blocks.white_concrete,
                 blocks.white_wool, blocks.light_blue_wool),
                (blocks.podzol, blocks.podzol, blocks.coarse_dirt, blocks.dirt,
                 blocks.brown_concrete, blocks.white_wool),
                (blocks.snow_block, blocks.white_concrete, blocks.blue_wool,
                 blocks.light_gray_wool, blocks.podzol)),
            rim_palette=(blocks.podzol, blocks.snow_block, blocks.coarse_dirt,
                         blocks.light_gray_concrete, blocks.andesite),
            sub_palettes=(rooted_soil, brown_soil, rooted_soil),
            geology=(blocks.stone, blocks.andesite, blocks.diorite)),
        "desert": dict(
            surface_palettes=(
                (blocks.sand, blocks.sand, blocks.sand, blocks.sandstone,
                 blocks.smooth_sandstone, blocks.terracotta),
                (blocks.sandstone, blocks.sandstone, blocks.smooth_sandstone,
                 blocks.white_concrete, blocks.terracotta),
                (blocks.cut_sandstone, blocks.smooth_sandstone,
                 blocks.orange_terracotta, blocks.sandstone)),
            rim_palette=(blocks.sandstone, blocks.sandstone, blocks.cut_sandstone,
                         blocks.smooth_sandstone, blocks.terracotta),
            sub_palettes=((blocks.sandstone, blocks.smooth_sandstone,
                           blocks.cut_sandstone, blocks.terracotta),) * 3,
            geology=(blocks.sandstone, blocks.cut_sandstone, blocks.terracotta)),
        "badlands": dict(
            surface_palettes=(
                (blocks.red_sand, blocks.red_sand, blocks.orange_terracotta,
                 blocks.brown_terracotta, blocks.orange_wool),
                (blocks.red_terracotta, blocks.red_terracotta, blocks.red_concrete,
                 blocks.red_wool, blocks.brown_terracotta),
                (blocks.terracotta, blocks.orange_terracotta, blocks.brown_terracotta,
                 blocks.brown_concrete, blocks.red_wool)),
            rim_palette=(blocks.red_sandstone, blocks.cut_red_sandstone,
                         blocks.red_terracotta, blocks.brown_terracotta,
                         blocks.red_concrete),
            sub_palettes=((blocks.terracotta, blocks.orange_terracotta,
                           blocks.brown_terracotta, blocks.red_terracotta,
                           blocks.brown_concrete),) * 3,
            geology=(blocks.terracotta, blocks.red_sandstone, blocks.granite),
            bands=True),
    }
    spec = dict(specs[style])
    # Keep the weighted dominant pair fixed and rotate only accent ordering.
    shift = int(variant) % 3
    def rotate(palette):
        if len(palette) <= 3 or shift == 0:
            return palette
        head, tail = palette[:2], palette[2:]
        offset = shift % len(tail)
        return head + tail[offset:] + tail[:offset]
    spec["surface_palettes"] = tuple(rotate(p) for p in spec["surface_palettes"])
    spec["sub_palettes"] = tuple(rotate(p) for p in spec["sub_palettes"])
    spec["rim_palette"] = rotate(spec["rim_palette"])
    # Compatibility names remain useful to preview/tests and describe the three
    # broad regions, but actual painting draws from each full micro-palette.
    spec["top"], spec["top_alt"], spec["top_detail"] = (
        palette[0] for palette in spec["surface_palettes"])
    spec["rim"] = spec["rim_palette"][0]
    spec["rim_alt"] = spec["rim_palette"][1]
    spec["sub"] = tuple(palette[0] for palette in spec["sub_palettes"])
    return spec


def _badlands_bands(blocks):
    return [
        blocks.terracotta, blocks.terracotta, blocks.orange_terracotta,
        blocks.terracotta, blocks.red_terracotta, blocks.brown_terracotta,
        blocks.terracotta, blocks.red_terracotta,
    ]


def stamp_island(store: ChunkStore, blocks: SkyBlocks, cfg: SkywarsConfig,
                 isl: Island, rng: Rng):
    R = isl.radius
    style = BIOMES[isl.biome_idx][2]
    biome_pid = isl._biome_pid  # set by caller (id into the void+biome palette)
    island_variant = _hash(cfg.seed, isl.wx, isl.wz, isl.biome_idx, 303) % 3
    spec = _surface_spec(blocks, style, island_variant)
    bands = _badlands_bands(blocks) if spec.get("bands") else None
    max_soil_depth = 7

    edge_seed = _hash(isl.wx, isl.wz, cfg.seed, 7)
    spike_seed = _hash(isl.wx, isl.wz, cfg.seed, 19)
    relief_seed = _hash(isl.wx, isl.wz, cfg.seed, 41)
    paint_seed = _hash(isl.wx, isl.wz, cfg.seed, 53)
    detail_seed = _hash(isl.wx, isl.wz, cfg.seed, 59)
    geology_seed = _hash(isl.wx, isl.wz, cfg.seed, 67)
    micro_seed = _hash(isl.wx, isl.wz, cfg.seed, 73)
    transition_seed = _hash(isl.wx, isl.wz, cfg.seed, 79)
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
            # Two quiet frequencies form broad painterly patches with softened
            # edges. They are reused by all material layers so the surface,
            # exposed rim and underside feel intentionally related.
            paint = (0.72 * _value_noise2d(X * 0.055, Z * 0.055, paint_seed)
                     + 0.28 * _value_noise2d(X * 0.13, Z * 0.13, detail_seed))
            geology = (0.78 * _value_noise2d(X * 0.045, Z * 0.045, geology_seed)
                       + 0.22 * _value_noise2d(X * 0.11, Z * 0.11, geology_seed + 17))
            micro = (0.62 * _value_noise2d(X * 0.31, Z * 0.31, micro_seed)
                     + 0.38 * _value_noise2d(X * 0.67, Z * 0.67, micro_seed + 23))
            transition = (0.74 * _value_noise2d(X * 0.10, Z * 0.10, transition_seed)
                          + 0.26 * _value_noise2d(X * 0.24, Z * 0.24,
                                                 transition_seed + 31))
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
            geo_base, geo_alt, geo_detail = spec["geology"]
            arr[ysolid] = geo_base

            # Broad geological pockets, lightly broken into horizontal strata.
            # The later variable soil pass grows irregular fingers over this
            # stone, so there is no globally flat material cut-off.
            deep_zone = ysolid & (yidx <= (surf - 3)[None])
            phase = ((_hash(cx, cz, geology_seed) >> 5) & 7)
            strata = ((yidx + phase) % 11) < 7
            alt_mask = deep_zone & (geology[None] > 0.61) & strata
            detail_mask = (deep_zone & (geology[None] < 0.29)
                           & (((yidx + phase + 3) % 13) < 5))
            arr[alt_mask] = geo_alt
            arr[detail_mask] = geo_detail

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
                p = paint[z_i, x_i]
                if is_rim:
                    region_idx = 0
                    palette = spec["rim_palette"]
                elif p > 0.685:
                    region_idx = 1
                    palette = spec["surface_palettes"][1]
                elif p < 0.255:
                    region_idx = 2
                    palette = spec["surface_palettes"][2]
                else:
                    region_idx = 0
                    palette = spec["surface_palettes"][0]
                # Correlated micro-noise makes material flecks form tiny runs,
                # not television-static confetti. A small hash dither prevents
                # the runs from looking like contour lines.
                dither = (_hash(int(X[z_i, x_i]), int(Z[z_i, x_i]), micro_seed)
                          & 0xFFFF) / 65535.0
                grain = min(0.999999, max(0.0,
                            0.84 * micro[z_i, x_i] + 0.16 * dither))
                top_id = palette[int(grain * len(palette))]
                arr[si, z_i, x_i] = top_id

                # A 3-7 block cap follows separate noise from the surface paint.
                # Its final two rows are selectively left as geology, producing
                # stone fingers and soil droops instead of a ruler-straight seam.
                soil_depth = 3 + min(4, int(transition[z_i, x_i] * 5.0))
                sub_palette = spec["sub_palettes"][region_idx]
                for d in range(1, min(max_soil_depth, soil_depth + 1) + 1):
                    yi = si - d
                    if yi >= 0 and (s - d) >= b:
                        if bands is not None:
                            arr[yi, z_i, x_i] = bands[(s - d) % len(bands)]
                        else:
                            edge_roll = (_hash(int(X[z_i, x_i]), s - d,
                                               int(Z[z_i, x_i]), transition_seed)
                                         & 0xFFFF) / 65535.0
                            if d == soil_depth + 1 and edge_roll < 0.78:
                                continue
                            if d >= soil_depth - 1:
                                stone_chance = 0.18 + 0.24 * (d - soil_depth + 1)
                                if edge_roll < stone_chance:
                                    continue
                            sub_grain = (grain * 0.68 + edge_roll * 0.32
                                         + d * 0.071) % 1.0
                            sub_id = sub_palette[int(sub_grain * len(sub_palette))]
                            arr[yi, z_i, x_i] = sub_id
                bio[z_i, x_i] = biome_pid
                surface_cols.append((int(X[z_i, x_i]), int(Z[z_i, x_i]), s))

            # Ores are deliberately last. The old ordering painted the soil cap
            # over already-selected ore and silently destroyed much of the 27%
            # budget. This remains one vectorized O(voxels) pass per chunk, but
            # now every selected buried block survives the art pass.
            if ore_ids:
                buried_ore_zone = ysolid & (yidx <= (surf - 1)[None])
                _scatter_ores(arr, buried_ore_zone, ore_ids, ore_probs,
                              _hash(cx, cz, cfg.seed, 101))

    # Surface ores are allocated once for the whole island, after every chunk
    # has been painted. Chunk-local random rolls made small islands luck-based
    # and could starve one side at chunk boundaries. This pass gives each
    # island an explicit per-type quota, then spreads it through shuffled
    # spatial cells so every team receives the same resource opportunity.
    if ore_ids and cfg.surface_ore > 0:
        _surface_ore_quota_pass(
            store, isl, surface_cols, ore_ids, ore_probs, cfg,
            _hash(isl.wx, isl.wz, cfg.seed, 202),
        )

    return surface_cols


def _ore_probs(blocks: SkyBlocks, cfg: SkywarsConfig):
    active = [o for o in cfg.ores if o.ratio > 0 and o.name.strip()]
    if not active:
        return [], []
    ids = [blocks.ore(o.name) for o in active]
    total = sum(o.ratio for o in active)
    # SkyWars needs instantly legible, resource-rich islands. The art pass uses
    # three times the old buried budget, still split by the configured ratios.
    budget = min(0.81, 0.27 * cfg.ore_density)
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


def _surface_ore_quotas(column_count, island_kind, ore_probs, cfg):
    """Allocate a deterministic, ratio-aware surface quota for one island."""
    if column_count <= 0 or not ore_probs or cfg.surface_ore <= 0:
        return [0] * len(ore_probs)

    exposure = min(0.12, sum(ore_probs) * 0.26 * cfg.surface_ore)
    minimum = 8 if island_kind == "spawn" else 12 if island_kind == "middle" else 0
    target = max(int(round(column_count * exposure)), minimum * len(ore_probs))
    target = min(column_count, target)

    # Small custom islands may not physically fit every requested minimum. The
    # normal planner always has ample room; this proportional fallback merely
    # keeps hand-authored extreme configs deterministic instead of looping.
    guaranteed = min(minimum, target // len(ore_probs))
    quotas = [guaranteed] * len(ore_probs)
    extra = target - sum(quotas)
    if extra <= 0:
        return quotas

    weight_total = sum(ore_probs)
    raw = [extra * p / weight_total for p in ore_probs]
    whole = [int(math.floor(value)) for value in raw]
    quotas = [base + add for base, add in zip(quotas, whole)]
    remainder = extra - sum(whole)
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - whole[i], ore_probs[i]),
                   reverse=True)
    for i in order[:remainder]:
        quotas[i] += 1
    return quotas


def _surface_ore_quota_pass(store, isl, surface_cols, ore_ids, ore_probs, cfg, seed):
    """Place exact per-island quotas with randomized, spatially even coverage."""
    # One coordinate can only represent one surface block. De-duplication also
    # makes this robust if a future island painter emits a column twice.
    columns = list(dict.fromkeys(surface_cols))
    quotas = _surface_ore_quotas(len(columns), isl.kind, ore_probs, cfg)
    target = sum(quotas)
    if target <= 0:
        return quotas

    rs = np.random.RandomState(seed & 0x7FFFFFFF)
    xs = [column[0] for column in columns]
    zs = [column[1] for column in columns]
    # Aim for at least one chosen point per roughly sqrt(area/target) cell.
    # Choosing from shuffled cells first avoids ore clumps without producing a
    # visible grid; any remaining quota is filled from independently shuffled
    # unused columns.
    spacing = max(2, int(round(math.sqrt(len(columns) / target) * 0.75)))
    x_origin = min(xs) - int(rs.randint(0, spacing))
    z_origin = min(zs) - int(rs.randint(0, spacing))
    buckets = {}
    for column in columns:
        key = ((column[0] - x_origin) // spacing,
               (column[1] - z_origin) // spacing)
        buckets.setdefault(key, []).append(column)

    keys = list(buckets)
    rs.shuffle(keys)
    selected = []
    selected_set = set()
    for key in keys:
        choices = buckets[key]
        column = choices[int(rs.randint(0, len(choices)))]
        selected.append(column)
        selected_set.add(column)
        if len(selected) >= target:
            break
    if len(selected) < target:
        remaining = [column for column in columns if column not in selected_set]
        rs.shuffle(remaining)
        selected.extend(remaining[:target - len(selected)])

    # Smooth weighted round-robin keeps common and rare ores interleaved across
    # the already-random spatial order. Counts remain exact for every island.
    remaining = list(quotas)
    credits = [float(rs.random_sample()) for _ in quotas]
    total = sum(quotas)
    ore_sequence = []
    for _ in range(total):
        for i, quota in enumerate(quotas):
            if remaining[i] > 0:
                credits[i] += quota
        chosen = max((i for i in range(len(quotas)) if remaining[i] > 0),
                     key=lambda i: credits[i])
        ore_sequence.append(ore_ids[chosen])
        remaining[chosen] -= 1
        credits[chosen] -= total

    for (wx, wz, surface_y), ore_id in zip(selected, ore_sequence):
        _set(store, None, wx, surface_y, wz, ore_id)
    return quotas


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


def _leaf_blob(store, blocks, wx, wy, wz, leaf, rx, ry, rz, rng, ragged=0.18):
    """Small asymmetric ellipsoid. Leaves never descend into head-height lanes."""
    for dy in range(-ry, ry + 1):
        for dz in range(-rz, rz + 1):
            for dx in range(-rx, rx + 1):
                norm = ((dx / max(1, rx)) ** 2 + (dy / max(1, ry)) ** 2
                        + (dz / max(1, rz)) ** 2)
                if norm > 1.15 or (norm > 0.72 and rng.chance(ragged)):
                    continue
                _set(store, blocks, wx + dx, wy + dy, wz + dz, leaf, True)


def _branch(store, blocks, wx, wy, wz, dx, dz, length, kind):
    axis = "x" if abs(dx) >= abs(dz) else "z"
    log = blocks.log(kind, axis)
    for step in range(1, length + 1):
        bx = wx + int(round(dx * step))
        bz = wz + int(round(dz * step))
        _set(store, blocks, bx, wy + (1 if step == length and length > 1 else 0),
             bz, log, True)
    return (wx + int(round(dx * length)),
            wy + (1 if length > 1 else 0),
            wz + int(round(dz * length)))


def _connect_canopy(store, blocks, wx, wz, base_y, max_y, kind, leaf):
    """Bridge one-block canopy gaps and discard genuinely floating foliage."""
    log_ids = {blocks.log(kind, axis) for axis in ("x", "y", "z")}
    directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                  (0, -1, 0), (0, 0, 1), (0, 0, -1))
    x_range = range(wx - 8, wx + 9)
    z_range = range(wz - 8, wz + 9)
    y_range = range(base_y, max_y + 1)

    def foliage():
        return {(x, y, z) for y in y_range for z in z_range for x in x_range
                if _block_at(store, x, y, z) == leaf}

    def attached(leaves):
        roots = {(x, y, z) for y in y_range for z in z_range for x in x_range
                 if _block_at(store, x, y, z) in log_ids}
        seen = set(roots)
        stack = list(roots)
        walkable = leaves | roots
        while stack:
            x, y, z = stack.pop()
            for dx, dy, dz in directions:
                other = (x + dx, y + dy, z + dz)
                if other in walkable and other not in seen:
                    seen.add(other)
                    stack.append(other)
        return leaves & seen

    leaves = foliage()
    connected = attached(leaves)
    # Two rounds handle stacked plates with consecutive independent gaps. Only
    # a single missing block between attached and floating leaves is bridged.
    for _ in range(2):
        bridges = set()
        floating = leaves - connected
        for x, y, z in connected:
            for dx, dy, dz in directions:
                middle = (x + dx, y + dy, z + dz)
                beyond = (x + 2 * dx, y + 2 * dy, z + 2 * dz)
                if beyond in floating and _block_at(store, *middle) == AIR:
                    bridges.add(middle)
        if not bridges:
            break
        for x, y, z in bridges:
            _set(store, blocks, x, y, z, leaf, True)
        leaves |= bridges
        connected = attached(leaves)

    for x, y, z in leaves - connected:
        _set(store, blocks, x, y, z, AIR)


def _tree(store, blocks, wx, wz, base_y, kind, rng):
    """Place one of several compact, hand-built tree silhouettes.

    Canopies begin at least four blocks above the floor and the trunk stays one
    block wide. This keeps sight lines and strafing space useful for SkyWars
    while branches, lean, layer spacing and canopy proportions still vary.
    """
    if isinstance(kind, (tuple, list)):
        kind, form = kind
    else:
        form = "natural"
    shape = rng.randint(0, 3)
    if kind in ("spruce", "pine"):
        trunk_h = rng.randint(7, 10) if kind == "pine" else rng.randint(6, 9)
    elif kind == "jungle":
        trunk_h = rng.randint(7, 10)
    elif kind == "dark_oak":
        trunk_h = rng.randint(6, 8)
    else:
        trunk_h = rng.randint(5, 8)
    if form == "palm":
        trunk_h = rng.randint(9, 12)
    elif form in ("flat_canopy", "windswept"):
        trunk_h = rng.randint(6, 9)

    log = blocks.log(kind)
    leaf = blocks.leaf(kind)
    # An occasional one-block lean gives the trunk gesture without widening the
    # collision footprint at player height.
    lean_dx, lean_dz = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
    wants_lean = form in ("windswept", "flat_canopy", "palm") or shape == 3
    lean_at = trunk_h - rng.randint(2, 3) if wants_lean and kind not in ("spruce", "pine") else trunk_h + 1
    tx = tz = 0
    for i in range(trunk_h):
        if i == lean_at:
            axis = "x" if lean_dx else "z"
            _set(store, blocks, wx + tx, base_y + i, wz + tz,
                 blocks.log(kind, axis), True)
            tx += lean_dx; tz += lean_dz
        _set(store, blocks, wx + tx, base_y + i, wz + tz, log, True)
    top = base_y + trunk_h - 1
    crown_x, crown_z = wx + tx, wz + tz

    if form == "palm":
        # Four high fronds and a small crown read as a palm without bringing
        # leaves into player head-height lanes.
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        for dx, dz in dirs:
            bx, by, bz = _branch(store, blocks, crown_x, top - 1,
                                 crown_z, dx, dz, rng.randint(2, 3), kind)
            _leaf_blob(store, blocks, bx, by, bz, leaf, 1, 1, 1, rng, 0.18)
        _leaf_blob(store, blocks, crown_x, top + 1, crown_z, leaf, 2, 1, 2, rng, 0.28)
    elif form == "flat_canopy":
        # Savanna umbrella: one directional bough and two offset flat plates.
        dx, dz = lean_dx, lean_dz
        bx, by, bz = _branch(store, blocks, crown_x, top - 2,
                             crown_z, dx, dz, 2, kind)
        rx, rz = (3, 2) if dx else (2, 3)
        _leaf_blob(store, blocks, bx, by + 1, bz, leaf, rx, 1, rz, rng, 0.24)
        _leaf_blob(store, blocks, crown_x - dx, top, crown_z - dz,
                   leaf, 2, 1, 2, rng, 0.28)
    elif form == "windswept":
        # A compact flag-shaped crown; repeated specimens can face any compass
        # direction, so the island does not look cloned.
        dx, dz = lean_dx, lean_dz
        for length, lift in ((2, 0), (3, 1)):
            bx, by, bz = _branch(store, blocks, crown_x, top - 2 + lift,
                                 crown_z, dx, dz, length, kind)
            _leaf_blob(store, blocks, bx, by + 1, bz, leaf, 2, 1, 2, rng, 0.30)
        _leaf_blob(store, blocks, crown_x, top, crown_z, leaf, 2, 1, 2, rng, 0.28)
    elif form == "arched":
        dirs = [(1, 0), (-1, 0)] if rng.chance(0.5) else [(0, 1), (0, -1)]
        for dx, dz in dirs:
            bx, by, bz = _branch(store, blocks, crown_x, top - 2,
                                 crown_z, dx, dz, 3, kind)
            _leaf_blob(store, blocks, bx, by + 1, bz, leaf, 3, 1, 2, rng, 0.30)
        _leaf_blob(store, blocks, crown_x, top + 1, crown_z, leaf, 2, 1, 2, rng, 0.22)
    elif kind == "spruce":
        # Alternating whorls read much better than a solid leaf pyramid and keep
        # the lowest needles above the combat lane.
        layers = ((top - 3, 3), (top - 1, 2), (top + 1, 2), (top + 3, 1))
        for ly, rad in layers:
            _leaf_blob(store, blocks, crown_x, ly, crown_z, leaf, rad, 0, rad, rng, 0.28)
        _set(store, blocks, crown_x, top + 4, crown_z, leaf, True)
    elif kind == "pine":
        # Tall clear bole with a wind-bent umbrella crown.
        bend = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
        _leaf_blob(store, blocks, crown_x + bend[0], top, crown_z + bend[1],
                   leaf, 3, 1, 2, rng, 0.24)
        _leaf_blob(store, blocks, crown_x, top + 2, crown_z, leaf, 2, 1, 2, rng, 0.20)
    elif kind == "birch":
        # Air between layered crowns keeps the pale trunk visible.
        _leaf_blob(store, blocks, crown_x, top - 1, crown_z, leaf, 2, 1, 2, rng, 0.22)
        _leaf_blob(store, blocks, crown_x + lean_dx, top + 1,
                   crown_z + lean_dz, leaf, 2, 1, 2, rng, 0.26)
    else:
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        # Two or three branch-end crowns make every silhouette asymmetrical.
        branch_count = 3 if kind in ("dark_oak", "jungle") or form == "forked" else 2
        for _ in range(branch_count):
            dx, dz = rng.choice(dirs)
            dirs.remove((dx, dz))
            length = 2 if kind in ("dark_oak", "jungle") or rng.chance(0.45) else 1
            bx, by, bz = _branch(store, blocks, crown_x, top - 2,
                                 crown_z, dx, dz, length, kind)
            rad = 3 if kind == "dark_oak" and rng.chance(0.45) else 2
            _leaf_blob(store, blocks, bx, by + 1, bz, leaf, rad, 1, rad, rng, 0.25)
        main_r = 3 if kind in ("dark_oak", "jungle") else 2
        _leaf_blob(store, blocks, crown_x, top, crown_z, leaf,
                   main_r, 2, main_r, rng, 0.25)

    _connect_canopy(store, blocks, wx, wz, base_y, top + 6, kind, leaf)


def _rock_materials(blocks, style):
    if style in ("woodland", "dark_woodland", "jungle"):
        return (blocks.andesite, blocks.tuff, blocks.stone, blocks.cobble)
    if style == "birch_grove":
        return (blocks.diorite, blocks.light_gray_concrete, blocks.stone, blocks.andesite)
    if style in ("taiga", "snowy", "snowy_grove"):
        return (blocks.andesite, blocks.tuff, blocks.stone, blocks.diorite)
    if style == "desert":
        return (blocks.sandstone, blocks.cut_sandstone,
                blocks.smooth_sandstone, blocks.terracotta)
    if style == "badlands":
        return (blocks.red_sandstone, blocks.cut_red_sandstone,
                blocks.terracotta, blocks.granite)
    if style == "savanna":
        return (blocks.granite, blocks.andesite, blocks.terracotta, blocks.stone)
    return (blocks.cobble, blocks.andesite, blocks.stone, blocks.tuff)


def _rock(store, blocks, wx, wz, style, rng, surface_map):
    """Place a grounded boulder, split outcrop, or clustered crag.

    Every occupied X/Z column starts directly on its own terrain column. The
    highest tier is forced to a 2x2 plateau, so rocks never taper into the
    one-block Lego towers or floating halves that uneven terrain used to make.
    """
    mats = _rock_materials(blocks, style)
    shape = rng.randint(0, 2)
    seed = _hash(wx, wz, shape, 0xA0C)
    columns = {}

    def add_column(dx, dz, height):
        columns[(dx, dz)] = max(columns.get((dx, dz), 0), int(height))

    def mat(dx, dy, dz, exposed=False):
        h = _hash(seed, dx, dy, dz)
        # One dominant material with sparse secondary seams looks composed.
        idx = 0 if h % 10 < 6 else (1 if h % 10 < 9 else 2 + (h & 1))
        return mats[idx]

    if shape == 0:
        rx = rng.randint(2, 3); rz = rng.randint(2, 3); peak = rng.randint(2, 4)
        for dz in range(-rz, rz + 1):
            for dx in range(-rx, rx + 1):
                norm = (dx / rx) ** 2 + (dz / rz) ** 2
                if norm <= 1.02:
                    height = max(1, int(round(peak * (1.0 - 0.48 * norm))))
                    if norm < 0.24:
                        height = peak
                    add_column(dx, dz, height)
        for dx, dz in ((0, 0), (1, 0), (0, 1), (1, 1)):
            add_column(dx, dz, peak)
    elif shape == 1:
        direction = rng.choice(((1, 0), (0, 1)))
        for ox, oz, peak in ((0, 0, rng.randint(3, 4)),
                             (direction[0] * 2, direction[1] * 2, rng.randint(2, 3))):
            for dz in range(-2, 3):
                for dx in range(-2, 3):
                    norm = dx * dx + dz * dz
                    if norm <= 5:
                        add_column(ox + dx, oz + dz, peak if dx in (0, 1) and dz in (0, 1)
                                   else max(1, peak - 1 - (norm > 2)))
    else:
        # Three overlapping round masses create depth from every viewing angle;
        # there is no two-block-thick ridge or wall silhouette.
        peak = rng.randint(3, 5)
        centers = ((0, 0, peak), (2, 1, max(2, peak - 1)),
                   (-1, -2, max(2, peak - 2)))
        for ox, oz, lobe_peak in centers:
            rx = rng.randint(2, 3); rz = rng.randint(2, 3)
            for dz in range(-rz, rz + 1):
                for dx in range(-rx, rx + 1):
                    norm = (dx / rx) ** 2 + (dz / rz) ** 2
                    if norm <= 1.0:
                        height = max(1, int(round(lobe_peak * (1.0 - 0.52 * norm))))
                        add_column(ox + dx, oz + dz, height)
        for dx, dz in ((0, 0), (1, 0), (0, 1), (1, 1)):
            add_column(dx, dz, peak)

    for (dx, dz), height in columns.items():
        ground_y = surface_map.get((wx + dx, wz + dz))
        if ground_y is None:
            continue
        for dy in range(height):
            _set(store, blocks, wx + dx, ground_y + 1 + dy, wz + dz,
                 mat(dx, dy, dz, dy == height - 1), True)


def _cactus_monument(store, blocks, wx, wz, surface_map, rng):
    """A broad, branching desert cactus landmark with a stable 2x2 trunk."""
    height = rng.randint(5, 8)
    for dx in (0, 1):
        for dz in (0, 1):
            ground_y = surface_map[(wx + dx, wz + dz)]
            for dy in range(height - ((dx + dz) & 1)):
                _set(store, blocks, wx + dx, ground_y + 1 + dy, wz + dz,
                     blocks.cactus, True)
    arm_y = height - 3
    for dx, dz in rng.choice((((-1, 0), (2, 1)), ((0, -1), (1, 2)))):
        ground_y = surface_map.get((wx + dx, wz + dz))
        if ground_y is None:
            continue
        for dy in range(arm_y, arm_y + 2):
            _set(store, blocks, wx + dx, ground_y + 1 + dy, wz + dz,
                 blocks.cactus, True)


def _ice_spikes(store, blocks, wx, wz, surface_map, rng):
    """Clustered ice needles with wide anchored bases and varied heights."""
    for ox, oz, height in ((0, 0, rng.randint(5, 8)),
                           (2, 1, rng.randint(3, 5)),
                           (-2, 1, rng.randint(2, 4))):
        footprint = ((0, 0), (1, 0), (0, 1), (1, 1))
        for dx, dz in footprint:
            ground_y = surface_map.get((wx + ox + dx, wz + oz + dz))
            if ground_y is None:
                continue
            for dy in range(max(2, height - ((dx + dz) & 1))):
                palette = (blocks.packed_ice, blocks.packed_ice,
                           blocks.blue_ice, blocks.cyan_concrete)
                _set(store, blocks, wx + ox + dx, ground_y + 1 + dy,
                     wz + oz + dz, palette[_hash(wx + dx, dy, wz + dz) % len(palette)], True)


def _terracotta_mound(store, blocks, wx, wz, surface_map, rng, style="badlands"):
    """Low, weathered earth mound—organic mass rather than a striped pipe."""
    rx = rng.randint(3, 4); rz = rng.randint(3, 4); peak = rng.randint(2, 3)
    if style == "savanna":
        palette = (blocks.terracotta, blocks.terracotta, blocks.brown_terracotta,
                   blocks.brown_concrete, blocks.granite, blocks.coarse_dirt)
    else:
        palette = (blocks.terracotta, blocks.terracotta, blocks.orange_terracotta,
                   blocks.brown_terracotta, blocks.red_terracotta, blocks.brown_concrete)
    columns = {}
    for dz in range(-rz, rz + 1):
        for dx in range(-rx, rx + 1):
            norm = (dx / rx) ** 2 + (dz / rz) ** 2
            ragged = ((_hash(wx + dx, wz + dz, 0xEA27) & 0xFFFF) / 65535.0) * 0.16
            if norm <= 0.96 + ragged:
                columns[(dx, dz)] = max(1, int(round(peak * (1.0 - 0.58 * norm))))
    for dx, dz in ((0, 0), (1, 0), (0, 1), (1, 1)):
        columns[(dx, dz)] = peak
    for (dx, dz), height in columns.items():
        ground_y = surface_map.get((wx + dx, wz + dz))
        if ground_y is None:
            continue
        for dy in range(height):
            material = palette[_hash(wx + dx, ground_y + dy, wz + dz) % len(palette)]
            _set(store, blocks, wx + dx, ground_y + 1 + dy, wz + dz,
                 material, True)


def _fallen_log(store, blocks, wx, wz, base_y, kind, rng):
    dx, dz = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
    axis = "x" if dx else "z"
    length = rng.randint(2, 4)
    for i in range(length):
        _set(store, blocks, wx + dx * i, base_y, wz + dz * i,
             blocks.log(kind, axis), True)
    # A small tuft at one end integrates the log without making a wall.
    leaf = blocks.leaf(kind)
    _set(store, blocks, wx - dx, base_y, wz - dz, leaf, True)
    _set(store, blocks, wx - dx, base_y + 1, wz - dz, leaf, True)


def _shrub(store, blocks, wx, wz, base_y, kind, rng):
    leaf = blocks.leaf(kind)
    _set(store, blocks, wx, base_y, wz, blocks.log(kind), True)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if abs(dx) + abs(dz) <= 1 or rng.chance(0.35):
                _set(store, blocks, wx + dx, base_y + 1, wz + dz, leaf, True)
    _set(store, blocks, wx, base_y + 2, wz, leaf, True)


def decorate_island(store, blocks, cfg, isl, surface_cols, rng):
    if not surface_cols:
        return
    style = BIOMES[isl.biome_idx][2]
    tree_kinds = SURFACE_TREES.get(style, [])
    area = math.pi * isl.radius * isl.radius
    variant = _hash(cfg.seed, isl.wx, isl.wz, isl.biome_idx, 303) % 3
    surface_spec = _surface_spec(blocks, style, variant)
    painted_ground = set(surface_spec["rim_palette"])
    for palette in surface_spec["surface_palettes"]:
        painted_ground.update(palette)

    # Ground blocks that plants / trees may sit on (never ore, stone, snow, etc.).
    soil_ids = {blocks.grass, blocks.grass_snow, blocks.dirt,
                blocks.coarse_dirt, blocks.podzol}
    sand_ids = {blocks.sand, blocks.red_sand}
    soil_ids.add(blocks.moss)
    tree_ground = painted_ground | soil_ids | {blocks.snow_block}
    natural_ground = (painted_ground | tree_ground | sand_ids | {blocks.sandstone,
                      blocks.red_sandstone, blocks.terracotta,
                      blocks.orange_terracotta, blocks.red_terracotta})
    plant_ground = painted_ground | soil_ids
    surface_map = {(wx, wz): sy for wx, wz, sy in surface_cols}
    placed = []

    def ground(wx, wz, sy):
        return _block_at(store, wx, sy, wz)

    def valid_feature(wx, wz, sy, footprint, center_clear, separation,
                      max_relief=1):
        if math.hypot(wx - isl.wx, wz - isl.wz) + footprint > isl.radius * 0.73:
            return False
        # Account for the whole footprint, not just the feature's anchor, so a
        # boulder cannot lean into the nominally clear spawn/combat disc.
        if math.hypot(wx - isl.wx, wz - isl.wz) - footprint < center_clear:
            return False
        if any((wx - px) ** 2 + (wz - pz) ** 2 < (separation + pr) ** 2
               for px, pz, pr in placed):
            return False
        heights = []
        for dz in range(-footprint, footprint + 1):
            for dx in range(-footprint, footprint + 1):
                if dx * dx + dz * dz <= footprint * footprint + 1:
                    h = surface_map.get((wx + dx, wz + dz))
                    if h is None:
                        return False
                    heights.append(h)
                    for y in range(h + 1, h + 4):
                        if _block_at(store, wx + dx, y, wz + dz) in blocks.chest_ids:
                            return False
        return heights and max(heights) - min(heights) <= max_relief

    def pick_feature(candidates, footprint, center_clear, separation,
                     max_relief=1):
        for _ in range(40):
            if not candidates:
                break
            wx, wz, sy = rng.choice(candidates)
            if ground(wx, wz, sy) in natural_ground and valid_feature(
                    wx, wz, sy, footprint, center_clear, separation, max_relief):
                placed.append((wx, wz, footprint))
                return wx, wz, sy
        return None

    inner_cols = [c for c in surface_cols
                  if math.hypot(c[0] - isl.wx, c[1] - isl.wz) <= isl.radius * 0.72]

    # Landmark trees: a handful of deliberate silhouettes with generous space
    # between them, instead of dozens of cloned vanilla saplings.
    n_trees = 0
    if tree_kinds and cfg.tree_density > 0:
        base = {"main": 0.00078, "middle": 0.00092, "spawn": 0.00068}[isl.kind]
        n_trees = int(round(area * base * cfg.tree_density * rng.uniform(0.78, 1.25)))
        n_trees = min(n_trees, {"main": 10, "middle": 3, "spawn": 3}[isl.kind])
    for _ in range(n_trees):
        pos = pick_feature(inner_cols, 3, 6.5 if isl.kind == "spawn" else 5.5, 3.5)
        if pos is not None:
            wx, wz, sy = pos
            if ground(wx, wz, sy) in tree_ground:
                _tree(store, blocks, wx, wz, sy + 1, rng.choice(tree_kinds), rng)

    # Geological landmarks use three silhouettes and biome-specific stone sets.
    n_rocks = int(round(area * 0.00058 * cfg.rock_density * rng.uniform(0.7, 1.25)))
    n_rocks = min(n_rocks, {"main": 7, "middle": 2, "spawn": 2}[isl.kind])
    for _ in range(n_rocks):
        pos = pick_feature(inner_cols, 5, 6.5 if isl.kind == "spawn" else 5.5,
                           3.0, max_relief=0)
        if pos is not None:
            wx, wz, sy = pos
            _rock(store, blocks, wx, wz, style, rng, surface_map)

    # Biome signatures are sparse enough to stay playable, but strong enough
    # that a desert, ice field or mesa has its own landmark language.
    if style in ("desert", "snowy", "snowy_grove", "badlands", "savanna"):
        landmark_count = {"main": 2, "middle": 1, "spawn": 1}[isl.kind]
        for _ in range(landmark_count):
            pos = pick_feature(inner_cols, 4,
                               7.0 if isl.kind == "spawn" else 5.5,
                               3.5, max_relief=0)
            if pos is None:
                continue
            wx, wz, _ = pos
            if style == "desert":
                _cactus_monument(store, blocks, wx, wz, surface_map, rng)
            elif style in ("snowy", "snowy_grove"):
                _ice_spikes(store, blocks, wx, wz, surface_map, rng)
            else:
                _terracotta_mound(store, blocks, wx, wz, surface_map, rng, style)

    # Low, authored accents fill the visual gap between a flower and a tree.
    if tree_kinds and cfg.foliage_density > 0:
        n_accents = min({"main": 5, "middle": 2, "spawn": 1}[isl.kind],
                        int(round(area * 0.00055 * cfg.foliage_density)))
        for _ in range(n_accents):
            pos = pick_feature(inner_cols, 3, 6.5 if isl.kind == "spawn" else 5.5, 2.0)
            if pos is None:
                continue
            wx, wz, sy = pos
            tree_form = rng.choice(tree_kinds)
            kind = tree_form[0] if isinstance(tree_form, (tuple, list)) else tree_form
            if rng.chance(0.38) and isl.kind != "spawn":
                _fallen_log(store, blocks, wx, wz, sy + 1, kind, rng)
            else:
                _shrub(store, blocks, wx, wz, sy + 1, kind, rng)

    # Foliage -- only ever on natural ground blocks (grass/dirt for plants,
    # sand for cactus), never on exposed ore or stone.
    if cfg.foliage_density > 0:
        n_foliage = int(area * 0.026 * cfg.foliage_density)
        for _ in range(n_foliage):
            wx, wz, sy = rng.choice(inner_cols or surface_cols)
            if math.hypot(wx - isl.wx, wz - isl.wz) <= (6.0 if isl.kind == "spawn" else 5.0):
                continue
            if _block_at(store, wx, sy + 1, wz) != AIR:
                continue
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
            elif style in ("snowy", "snowy_grove"):
                pass   # snow surface: keep it clean (no floating grass)
            elif gid in plant_ground:
                r = rng.rand()
                if style in ("dark_woodland", "jungle") and r > 0.91:
                    _set(store, blocks, wx, top, wz,
                         blocks.brown_mushroom if rng.chance(0.75) else blocks.red_mushroom, True)
                elif r < 0.6:
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


def _place_chest(store, blocks, kind, wx, wy, wz, rng):
    bid = blocks.chest_block(kind, rng.choice(("north", "south", "east", "west")))
    _set(store, blocks, wx, wy, wz, bid)
    store.add_entity(wx, wy, wz, _chest_entity(kind, wx, wy, wz), kind)


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
        _place_chest(store, blocks, kind, wx, sy + 1, wz, rng)
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

    # Shuffled ring-specific art decks prevent one unlucky seed from making five
    # adjacent grass islands. Every style appears before a deck repeats; repeat
    # cycles rotate so the first and last islands of a circular ring still differ.
    def biome_sequence(count, salt):
        if not cfg.varied_biomes:
            return [0] * count
        deck_rng = Rng(cluster_seed, salt, 0xB10)
        deck = list(range(len(BIOMES)))
        for i in range(len(deck) - 1, 0, -1):
            j = deck_rng.randint(0, i)
            deck[i], deck[j] = deck[j], deck[i]
        out = []
        shift = 0
        while len(out) < count:
            cycle = deck[shift:] + deck[:shift]
            out.extend(cycle[:count - len(out)])
            shift = (shift + max(2, len(deck) // 3)) % len(deck)
        if count > 1 and out[-1] == out[0]:
            out[-1], out[-2] = out[-2], out[-1]
        return out

    main_biomes = biome_sequence(1, 1)
    middle_biomes = biome_sequence(n_mid, 2)
    spawn_biomes = biome_sequence(spawn_count, 3)

    def surf_y():
        if cfg.height_jitter <= 0:
            return cfg.base_y
        return cfg.base_y + rng.randint(-cfg.height_jitter, cfg.height_jitter)

    def depth_for(radius):
        return max(cfg.edge_thickness + 6, int(round(radius * cfg.taper_ratio)))

    # Main.
    main_y = surf_y()
    islands.append(Island(cx, cz, Rmain, main_y, depth_for(Rmain),
                          main_biomes[0], "main"))

    # Single middle ring, evenly spaced.
    a0 = rng.uniform(0, 2 * math.pi)
    for i in range(n_mid):
        ang = a0 + 2 * math.pi * i / n_mid
        wx = int(round(cx + math.cos(ang) * Rmid))
        wz = int(round(cz + math.sin(ang) * Rmid))
        rad = Rm * rng.uniform(0.99, 1.01)
        islands.append(Island(wx, wz, rad, surf_y(), depth_for(rad),
                              middle_biomes[i], "middle"))

    # Spawn ring, aligned to the middle ring so spawns sit near a middle island.
    spawn_islands: List[Island] = []
    b0 = a0                                # share the start angle with the middle ring
    for i in range(spawn_count):
        ang = b0 + 2 * math.pi * i / spawn_count
        wx = int(round(cx + math.cos(ang) * Rring))
        wz = int(round(cz + math.sin(ang) * Rring))
        rad = Rs * rng.uniform(0.98, 1.02)
        isl = Island(wx, wz, rad, surf_y(), depth_for(rad),
                     spawn_biomes[i], "spawn")
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

    # Serialize touched chunks directly to the selected edition. Bedrock uses
    # PocketMine-readable LevelDB entries; it is not a renamed Java world.
    block_palette = blocks.palette()
    compressed = []
    bedrock_entries = []
    full = np.zeros((WORLD_HEIGHT, 16, 16), dtype=np.int16)
    band_off = BAND_LO - MIN_Y
    for (ccx, ccz), band in store.blocks.items():
        full[:] = AIR
        full[band_off:band_off + BAND_H] = band
        bio = store.biomes[(ccx, ccz)]
        ents = store.entities[(ccx, ccz)]
        if cfg.output_format == "bedrock":
            bedrock_entries.extend(build_bedrock_chunk_entries(
                ccx, ccz, full, bio, block_palette, biome_list,
                store.bedrock_entities[(ccx, ccz)]))
        else:
            root = build_chunk(ccx, ccz, full, bio, block_palette, biome_list,
                               cfg.data_version, MIN_SECTION, N_SECTIONS,
                               block_entities=ents if ents else None)
            compressed.append((ccx, ccz, nbt.to_zlib("", root)))

    return {
        "compressed": compressed,
        "bedrock_entries": bedrock_entries,
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
    cfg.output_format = "bedrock" if str(cfg.output_format).lower() == "bedrock" else "java"
    region_dir = os.path.join(out_dir, "region")
    os.makedirs(out_dir, exist_ok=True)
    if cfg.output_format == "java":
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
    bedrock_entries = []
    all_positions = []
    all_footprints = []
    done = 0

    def consume(res):
        nonlocal done
        bedrock_entries.extend(res["bedrock_entries"])
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

    # Spawn = cluster 0's centre.
    spawn = all_positions[0]["center"] if all_positions else [0, cfg.base_y + 1, 0]

    if cfg.output_format == "bedrock":
        report(0.88, "Writing PocketMine Bedrock 1.18.30 LevelDB world...")
        write_bedrock_world(out_dir, bedrock_entries, cfg, spawn)
    else:
        report(0.88, "Writing Java region files...")
        for key in sorted(region_writers):
            rw = region_writers[key]
            with open(os.path.join(region_dir, rw.filename), "wb") as fp:
                fp.write(rw.to_bytes())
        report(0.94, "Writing Java level.dat...")
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
        "output_format": cfg.output_format,
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
