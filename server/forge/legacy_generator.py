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

from . import nbt
from . import noise
from . import underworld
from . import structures
from .region import RegionWriter, build_chunk

# Fixed vertical layout (standard overworld dimension range).
MIN_Y = -64
WORLD_HEIGHT = 384          # -64 .. 319
MIN_SECTION = MIN_Y // 16   # -4
N_SECTIONS = WORLD_HEIGHT // 16  # 24

NETHER_BOX_SIZE = 1000
NETHER_BOX_GAP = 500
NETHER_BOX_CEILING_Y = 127


_UINT32 = 0xFFFFFFFF
_SURFACE_HEIGHT_CACHE = {}


def _mix32(n):
    n &= _UINT32
    n ^= (n >> 16)
    n = (n * 0x7FEB352D) & _UINT32
    n ^= (n >> 15)
    n = (n * 0x846CA68B) & _UINT32
    n ^= (n >> 16)
    return n & _UINT32


def _hash2i(x, z, seed, salt=0):
    n = (int(x) * 0x632BE59B) ^ (int(z) * 0x85157AF5) ^ \
        (int(seed) * 0x9E3779B9) ^ (int(salt) * 0x165667B1)
    return _mix32(n)


def _rand01(x, z, seed, salt=0):
    return _hash2i(x, z, seed, salt) / 4294967296.0


def _rand_range(x, z, seed, salt, lo, hi):
    return lo + (hi - lo) * _rand01(x, z, seed, salt)


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
        self.red_sand = g(_bkey("minecraft:red_sand"))
        self.red_sandstone = g(_bkey("minecraft:red_sandstone"))
        self.terracotta = g(_bkey("minecraft:terracotta"))
        self.orange_terracotta = g(_bkey("minecraft:orange_terracotta"))
        self.yellow_terracotta = g(_bkey("minecraft:yellow_terracotta"))
        self.brown_terracotta = g(_bkey("minecraft:brown_terracotta"))
        self.red_terracotta = g(_bkey("minecraft:red_terracotta"))
        self.white_terracotta = g(_bkey("minecraft:white_terracotta"))
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

        # Deep dark
        self.deepslate = g(_bkey("minecraft:deepslate", {"axis": "y"}))
        self.tuff = g(_bkey("minecraft:tuff"))
        self.cobbled_deepslate = g(_bkey("minecraft:cobbled_deepslate"))
        self.sculk = g(_bkey("minecraft:sculk"))
        self.sculk_vein = g(_bkey("minecraft:sculk_vein",
                                  {"up": "true", "down": "false", "north": "false",
                                   "south": "false", "east": "false", "west": "false"}))
        self.sculk_sensor = g(_bkey("minecraft:sculk_sensor"))
        self.sculk_shrieker = g(_bkey("minecraft:sculk_shrieker", {"can_summon": "true"}))
        self.sculk_catalyst = g(_bkey("minecraft:sculk_catalyst"))
        self.reinforced_deepslate = g(_bkey("minecraft:reinforced_deepslate"))
        self.deepslate_bricks = g(_bkey("minecraft:deepslate_bricks"))
        self.deepslate_tiles = g(_bkey("minecraft:deepslate_tiles"))
        self.cracked_deepslate_bricks = g(_bkey("minecraft:cracked_deepslate_bricks"))
        self.polished_deepslate = g(_bkey("minecraft:polished_deepslate"))

        # Nether
        self.netherrack = g(_bkey("minecraft:netherrack"))
        self.soul_sand = g(_bkey("minecraft:soul_sand"))
        self.soul_soil = g(_bkey("minecraft:soul_soil"))
        self.magma = g(_bkey("minecraft:magma_block"))
        self.glowstone = g(_bkey("minecraft:glowstone"))
        self.basalt = g(_bkey("minecraft:basalt", {"axis": "y"}))
        self.blackstone = g(_bkey("minecraft:blackstone"))
        self.gilded_blackstone = g(_bkey("minecraft:gilded_blackstone"))
        self.crimson_nylium = g(_bkey("minecraft:crimson_nylium"))
        self.warped_nylium = g(_bkey("minecraft:warped_nylium"))
        self.nether_wart_block = g(_bkey("minecraft:nether_wart_block"))
        self.warped_wart_block = g(_bkey("minecraft:warped_wart_block"))
        self.crimson_stem = g(_bkey("minecraft:crimson_stem", {"axis": "y"}))
        self.warped_stem = g(_bkey("minecraft:warped_stem", {"axis": "y"}))
        self.shroomlight = g(_bkey("minecraft:shroomlight"))
        self.nether_bricks = g(_bkey("minecraft:nether_bricks"))
        self.nether_quartz_ore = g(_bkey("minecraft:nether_quartz_ore"))
        self.nether_gold_ore = g(_bkey("minecraft:nether_gold_ore"))
        self.ancient_debris = g(_bkey("minecraft:ancient_debris"))
        self.polished_blackstone_bricks = g(_bkey("minecraft:polished_blackstone_bricks"))

        # Structure blocks
        self.cobblestone = g(_bkey("minecraft:cobblestone"))
        self.stone_bricks = g(_bkey("minecraft:stone_bricks"))
        self.mossy_stone_bricks = g(_bkey("minecraft:mossy_stone_bricks"))
        self.cracked_stone_bricks = g(_bkey("minecraft:cracked_stone_bricks"))
        self.chiseled_stone_bricks = g(_bkey("minecraft:chiseled_stone_bricks"))
        self.stone_brick_wall = g(_bkey("minecraft:stone_bricks"))
        self.oak_planks = g(_bkey("minecraft:oak_planks"))
        self.spruce_planks = g(_bkey("minecraft:spruce_planks"))
        self.cobblestone_stairs = g(_bkey("minecraft:cobblestone"))
        self.glass = g(_bkey("minecraft:glass"))
        self.bookshelf = g(_bkey("minecraft:bookshelf"))
        self.torch = g(_bkey("minecraft:torch"))
        self.lantern = g(_bkey("minecraft:lantern", {"hanging": "false"}))
        self.soul_lantern = g(_bkey("minecraft:soul_lantern", {"hanging": "false"}))
        self.cobweb = g(_bkey("minecraft:cobweb"))
        self.chiseled_sandstone = g(_bkey("minecraft:chiseled_sandstone"))
        self.cut_sandstone = g(_bkey("minecraft:cut_sandstone"))
        self.smooth_sandstone = g(_bkey("minecraft:smooth_sandstone"))
        self.blue_terracotta = g(_bkey("minecraft:blue_terracotta"))
        self.polished_blackstone = g(_bkey("minecraft:polished_blackstone"))
        self.chiseled_polished_blackstone = g(_bkey("minecraft:chiseled_polished_blackstone"))
        self.gold_block = g(_bkey("minecraft:gold_block"))
        self.nether_wart_plant = g(_bkey("minecraft:nether_wart", {"age": "3"}))
        self.spawner = g(_bkey("minecraft:spawner"))
        self.iron_bars = g(_bkey("minecraft:iron_bars",
                                 {"north": "false", "south": "false", "east": "false",
                                  "west": "false", "waterlogged": "false"}))
        self.nether_brick_fence = g(_bkey("minecraft:nether_brick_fence",
                                          {"north": "false", "south": "false",
                                           "east": "false", "west": "false",
                                           "waterlogged": "false"}))
        self.chest_face = {f: g(_bkey("minecraft:chest",
                                      {"facing": f, "type": "single", "waterlogged": "false"}))
                           for f in ("north", "south", "east", "west")}
        self.portal_frame = {f: g(_bkey("minecraft:end_portal_frame",
                                         {"eye": "false", "facing": f}))
                             for f in ("north", "south", "east", "west")}

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

        # Four vine attachment states. These are pre-registered so jungle
        # vines remain stable across multiprocessing workers.
        vine_base = {"up": "false", "north": "false", "south": "false",
                     "east": "false", "west": "false"}
        props = dict(vine_base); props["north"] = "true"
        self.vine_n = g(_bkey("minecraft:vine", props))
        props = dict(vine_base); props["south"] = "true"
        self.vine_s = g(_bkey("minecraft:vine", props))
        props = dict(vine_base); props["east"] = "true"
        self.vine_e = g(_bkey("minecraft:vine", props))
        props = dict(vine_base); props["west"] = "true"
        self.vine_w = g(_bkey("minecraft:vine", props))

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
    "minecraft:birch_forest": (95, 143, 74),
    "minecraft:dark_forest": (45, 83, 36),
    "minecraft:taiga": (81, 122, 100),
    "minecraft:jungle": (89, 158, 42),
    "minecraft:desert": (219, 205, 141),
    "minecraft:badlands": (197, 111, 54),
    "minecraft:beach": (224, 214, 166),
    "minecraft:snowy_plains": (236, 244, 250),
    "minecraft:snowy_taiga": (198, 216, 208),
    "minecraft:snowy_slopes": (223, 231, 240),
    "minecraft:river": (72, 118, 178),
    "minecraft:nether_wastes": (111, 54, 52),
    "minecraft:crimson_forest": (150, 46, 63),
    "minecraft:warped_forest": (55, 132, 126),
    "minecraft:soul_sand_valley": (94, 76, 74),
    "minecraft:basalt_deltas": (67, 67, 73),
}


# Minecraft map-style preview base colours. These are intentionally flatter and
# chunkier than biome tinting so the live preview reads like an in-game map.
MAP_BASE_COLORS = {
    "minecraft:plains": (127, 178, 56),
    "minecraft:forest": (89, 125, 39),
    "minecraft:birch_forest": (111, 158, 69),
    "minecraft:dark_forest": (55, 88, 31),
    "minecraft:taiga": (79, 110, 80),
    "minecraft:jungle": (64, 145, 35),
    "minecraft:desert": (247, 233, 163),
    "minecraft:badlands": (216, 127, 51),
    "minecraft:beach": (247, 233, 163),
    "minecraft:snowy_plains": (255, 255, 255),
    "minecraft:snowy_taiga": (216, 238, 226),
    "minecraft:snowy_slopes": (255, 255, 255),
    "minecraft:river": (64, 64, 255),
    "minecraft:nether_wastes": (112, 54, 55),
    "minecraft:crimson_forest": (153, 48, 64),
    "minecraft:warped_forest": (40, 124, 118),
    "minecraft:soul_sand_valley": (92, 77, 75),
    "minecraft:basalt_deltas": (67, 67, 73),
}
MAP_SHADE = (0.71, 0.86, 1.0, 1.15)


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
        OreCfg("coal_ore", -64, 319, 15.0, 12, "high"),
        OreCfg("iron_ore", -64, 319, 13.0, 9, "mid"),
        OreCfg("copper_ore", -16, 96, 8.0, 10, "mid"),
        OreCfg("gold_ore", -64, 64, 7.0, 8, "low"),
        OreCfg("redstone_ore", -64, 16, 11.0, 8, "low"),
        OreCfg("lapis_ore", -64, 16, 5.5, 7, "low"),
        OreCfg("diamond_ore", -64, 16, 10.0, 6, "low"),
        OreCfg("emerald_ore", -64, 48, 4.0, 5, "high"),
    ]


@dataclass
class WorldConfig:
    name: str = "Forged World"
    size: int = 1000
    seed: int = 0
    data_version: int = 3465          # 1.20.1
    version_name: str = "1.20.1"
    output_format: str = "java"       # java or bedrock; native generator only

    floor_y: int = -20                # overworld bottom; underworld/void below
    water_level: int = 118
    base_height: int = 128            # raised so the underworld layers get room below
    hill_amp: float = 8.0
    mountain_amp: float = 74.0
    mountain_amount: float = 0.30      # 0..1, higher => more/higher mountains
    extreme_mountain_amount: float = 0.20  # sparse local mountain ranges
    snowline_y: int = 246

    caves: bool = True
    cave_intensity: float = 1.0
    ravines: bool = True
    fluids: bool = True                # underground lava/water pockets

    rivers: bool = True
    river_amount: float = 0.4          # 0..1
    lakes: bool = True
    lake_amount: float = 0.4           # 0..1
    surface_lava_pools: bool = True    # rare small surface lava pools

    # Internal, auto-tuned per generated world so one seed does not become
    # 70% forest/plains or 50% snow. These are filled by _tune_biomes().
    biome_frozen_cut: float = 0.20
    biome_cold_cut: float = 0.36
    biome_warm_cut: float = 0.70
    biome_desert_hum_cut: float = 0.42
    biome_forest_hum_cut: float = 0.62
    biome_jungle_hum_cut: float = 0.82
    biome_plains_forest_weight: float = 50.0
    biome_desert_weight: float = 20.0
    biome_snow_weight: float = 15.0
    biome_rare_weight: float = 15.0
    biome_tuned: bool = False

    trees: bool = True
    tree_density: float = 1.0
    decorations: bool = True           # grass, flowers, sugar cane, cactus
    stone_variants: bool = True        # diorite / granite / andesite / dirt / gravel

    wall: bool = True
    wall_min_y: int = MIN_Y
    wall_max_y: int = 319

    game_type: int = 0                # 0 survival, 1 creative
    allow_commands: bool = True

    # Optional extra spaces. Deep dark is vertical under the overworld floor.
    # Nether is no longer a vertical layer; it is generated as its own sealed
    # 1000x1000 box to the right of the overworld.
    deep_dark: bool = False           # deepslate/tuff/sculk below the caverns
    deep_dark_height: int = 0         # legacy field; deep dark now fills to MIN_Y
    nether: bool = False              # separate netherrack/lava biome box
    nether_height: int = 0            # legacy field; kept for old callers
    structures: bool = False          # scatter recognizable structures
    structure_plan: Optional[list] = None   # filled by generate_world before workers run

    ores: List[OreCfg] = field(default_factory=_default_ores)


# ---------------------------------------------------------------------------
# Palette preparation / worker planning
# ---------------------------------------------------------------------------

def _prepare_palettes(cfg: WorldConfig):
    """Create stable block and biome palettes before workers generate chunks."""
    blocks = Blocks(cfg.data_version)

    # These are normally registered lazily by tree placement. Pre-registering
    # makes worker output independent from generation order.
    for kind in ("oak", "spruce", "jungle", "birch", "dark_oak"):
        blocks.log(kind)
        blocks.leaves(kind)

    # Ores are user-configurable, so register the active set in config order.
    # Deep-dark ores are placed after the deep-dark material palette pass, using
    # deepslate ore variants. Register those up-front too so multiprocessing
    # workers never create block IDs after their chunk palette snapshot has been
    # taken. If a custom ore is not an *_ore block, _deep_dark_ore_name() returns
    # it unchanged and Registry.get() deduplicates it normally.
    for ore in cfg.ores:
        blocks.ore(ore.name)
        if cfg.deep_dark:
            blocks.ore(_deep_dark_ore_name(ore.name))

    biome_reg = Registry()
    for biome in BIOME_COLORS:
        biome_reg.get(biome)
    biome_reg.get("minecraft:plains")

    return blocks, biome_reg


def _chunk_rng_seed(seed, cx, cz):
    return (seed & 0xFFFFFFFF) ^ ((cx & 0xFFFF) << 8) ^ \
           ((cz & 0xFFFF) << 20) ^ 0x9E3779B1


def _resolve_worker_count(requested, total_regions, total_chunks, job_count=None):
    cpu_count = os.cpu_count() or 1
    auto = requested is None or int(requested) <= 0
    requested = cpu_count if auto else int(requested)
    requested = max(1, requested)

    # Tiny worlds are faster without process startup / pickle overhead.
    if total_chunks < 256 or total_regions <= 1:
        return 1

    # Manual worker counts are allowed to oversubscribe for experimentation,
    # but never create more processes than there are useful jobs.
    if job_count is None:
        job_count = total_regions
    return max(1, min(requested, job_count))


def overworld_bounds(cfg):
    xmin = -(cfg.size // 2)
    xmax = xmin + cfg.size - 1
    return xmin, xmax, xmin, xmax


def nether_bounds(cfg):
    oxmin, oxmax, _, _ = overworld_bounds(cfg)
    nxmin = oxmax + 1 + NETHER_BOX_GAP
    nxmax = nxmin + NETHER_BOX_SIZE - 1
    nzmin = -(NETHER_BOX_SIZE // 2)
    nzmax = nzmin + NETHER_BOX_SIZE - 1
    return nxmin, nxmax, nzmin, nzmax


def generation_areas(cfg):
    areas = [("overworld",) + overworld_bounds(cfg)]
    if cfg.nether:
        areas.append(("nether",) + nether_bounds(cfg))
    return areas


def generation_bounds(cfg):
    areas = generation_areas(cfg)
    xmins = [a[1] for a in areas]
    xmaxs = [a[2] for a in areas]
    zmins = [a[3] for a in areas]
    zmaxs = [a[4] for a in areas]
    return min(xmins), max(xmaxs), min(zmins), max(zmaxs)


def _chunk_bounds_for_area(area):
    _, xmin, xmax, zmin, zmax = area
    return xmin >> 4, xmax >> 4, zmin >> 4, zmax >> 4


def _chunk_areas(cfg):
    return [_chunk_bounds_for_area(area) for area in generation_areas(cfg)]


def _chunk_in_any_area(cx, cz, chunk_areas):
    for cxmin, cxmax, czmin, czmax in chunk_areas:
        if cxmin <= cx <= cxmax and czmin <= cz <= czmax:
            return True
    return False


def _generation_chunks(cfg):
    seen = set()
    for cxmin, cxmax, czmin, czmax in _chunk_areas(cfg):
        for cz in range(czmin, czmax + 1):
            for cx in range(cxmin, cxmax + 1):
                key = (cx, cz)
                if key not in seen:
                    seen.add(key)
                    yield key


def _region_jobs_for_areas(cfg):
    chunk_areas = _chunk_areas(cfg)
    region_ranges = set()
    for cxmin, cxmax, czmin, czmax in chunk_areas:
        for rz in range(czmin >> 5, (czmax >> 5) + 1):
            for rx in range(cxmin >> 5, (cxmax >> 5) + 1):
                region_ranges.add((rx, rz))

    for rx, rz in sorted(region_ranges, key=lambda r: (r[1], r[0])):
        rcxmin = rx << 5
        rcxmax = rcxmin + 31
        rczmin = rz << 5
        rczmax = rczmin + 31
        yield rx, rz, rcxmin, rcxmax, rczmin, rczmax


def _tile_jobs_for_areas(cfg, tile_chunks=4):
    chunk_areas = _chunk_areas(cfg)
    for cxmin, cxmax, czmin, czmax in chunk_areas:
        for jczmin in range(czmin, czmax + 1, tile_chunks):
            jczmax = min(czmax, jczmin + tile_chunks - 1)
            for jcxmin in range(cxmin, cxmax + 1, tile_chunks):
                jcxmax = min(cxmax, jcxmin + tile_chunks - 1)
                yield jcxmin, jcxmax, jczmin, jczmax


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


def _tile_jobs(cxmin, cxmax, czmin, czmax, tile_chunks=4):
    for jczmin in range(czmin, czmax + 1, tile_chunks):
        jczmax = min(czmax, jczmin + tile_chunks - 1)
        for jcxmin in range(cxmin, cxmax + 1, tile_chunks):
            jcxmax = min(cxmax, jcxmin + tile_chunks - 1)
            yield jcxmin, jcxmax, jczmin, jczmax


def _preview_patch_rect(preview_xmin, preview_zmin, preview_w, preview_h, pscale,
                        cxmin, cxmax, czmin, czmax):
    wx0 = max(preview_xmin, cxmin * 16)
    wx1 = min(preview_xmin + preview_w - 1, cxmax * 16 + 15)
    wz0 = max(preview_zmin, czmin * 16)
    wz1 = min(preview_zmin + preview_h - 1, czmax * 16 + 15)

    px0 = max(0, min((preview_w + pscale - 1) // pscale - 1, (wx0 - preview_xmin) // pscale))
    px1 = max(0, min((preview_w + pscale - 1) // pscale - 1, (wx1 - preview_xmin) // pscale))
    pz0 = max(0, min((preview_h + pscale - 1) // pscale - 1, (wz0 - preview_zmin) // pscale))
    pz1 = max(0, min((preview_h + pscale - 1) // pscale - 1, (wz1 - preview_zmin) // pscale))

    return px0, pz0, px1 - px0 + 1, pz1 - pz0 + 1


# ---------------------------------------------------------------------------
# Height / biome / water fields (per column)
# ---------------------------------------------------------------------------




def _smoothstep01(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _temperature_height(cfg, Hf):
    # Temperature falloff follows the raised terrain frame. Since the generator
    # now shifts the land/water/snow defaults upward together, keep altitude
    # cooling soft enough that hot biomes cannot sit directly against snow just
    # because one smoothed mountain shoulder crossed a height threshold.
    return np.clip((Hf - cfg.water_level) / 640.0, 0.0, 0.16)


def _base_climate_fields(cfg, X, Z, Hf):
    # Vanilla 1.12 rivers/biomes are layer based, not real erosion. This uses
    # the same broad idea: a slow climate layer with a stronger warped edge
    # layer on top. The important part is that the climate map is coherent at
    # landmass scale, while the final border gets noisy enough to avoid blocky
    # straight desert/snow rectangles.
    s = cfg.seed
    size_scale = max(650.0, min(1450.0, cfg.size * 0.92))
    warp_scale = max(520.0, size_scale * 0.78)

    warp_x = (noise.fbm2d(X + 6111, Z - 4222, s + 33, octaves=4, freq=1 / warp_scale) - 0.5) * 330.0
    warp_z = (noise.fbm2d(X - 1777, Z + 9333, s + 34, octaves=4, freq=1 / warp_scale) - 0.5) * 330.0
    warp_x += (noise.fbm2d(X + 313, Z + 991, s + 35, octaves=2, freq=1 / 185.0) - 0.5) * 72.0
    warp_z += (noise.fbm2d(X - 919, Z - 707, s + 36, octaves=2, freq=1 / 185.0) - 0.5) * 72.0
    CX = X + warp_x
    CZ = Z + warp_z

    # Crossed, rotated temperature waves guarantee a finite 1000x1000-ish map
    # contains hot/temperate/cold bands. Noise alone can miss one extreme on a
    # small world, which caused the "all plains" or "all snow" seeds.
    phase_a = _rand_range(0, 0, s, 41001, 0.0, np.pi * 2.0)
    phase_b = _rand_range(0, 0, s, 41002, 0.0, np.pi * 2.0)
    angle = _rand_range(0, 0, s, 41003, 0.0, np.pi * 2.0)
    ca = np.cos(angle)
    sa = np.sin(angle)
    rx = CX * ca - CZ * sa
    rz = CX * sa + CZ * ca

    wave_temp = (np.sin(rx / size_scale * np.pi * 2.15 + phase_a) * 0.28 +
                 np.sin((rx * 0.55 + rz * 0.78) / size_scale * np.pi * 1.72 + phase_b) * 0.18)
    tw = noise.fbm2d(CX + 1000, CZ - 1000, s + 3, octaves=5, freq=1 / (size_scale * 0.92))
    temp_detail = (noise.fbm2d(CX + 333, CZ - 777, s + 31, octaves=3, freq=1 / 260.0) - 0.5) * 0.085
    edge_wobble = (noise.fbm2d(X + 7077, Z - 1212, s + 74, octaves=3, freq=1 / 135.0) - 0.5)
    temp = 0.5 + wave_temp + (tw - 0.5) * 0.70 + temp_detail + edge_wobble * 0.025
    temp -= _temperature_height(cfg, Hf)

    mw = noise.fbm2d(CX - 2000, CZ + 2000, s + 4, octaves=5, freq=1 / (size_scale * 0.82))
    hum_wave = (np.sin((rz * 0.95 - rx * 0.28) / size_scale * np.pi * 2.05 + phase_b * 0.7) * 0.16)
    hum_detail = (noise.fbm2d(CX - 444, CZ + 666, s + 41, octaves=3, freq=1 / 245.0) - 0.5) * 0.105
    hum = 0.5 + (mw - 0.5) * 0.96 + hum_wave + hum_detail - edge_wobble * 0.026

    selector = noise.fbm2d(CX + 8811, CZ - 4417, s + 73, octaves=3, freq=1 / 255.0)
    return np.clip(temp, 0.0, 1.0), np.clip(hum, 0.0, 1.0), selector, edge_wobble


def _badlands_strength(cfg, X, Z, temp_band, hum_band):
    # Mesa/badlands need to behave like a coherent climate province, not a
    # high-frequency paint mask. Use broad dry-hot patches with a smooth edge so
    # the elevation can blend back into desert/plains instead of forming a
    # terracotta knife wall against neighboring biomes.
    arid_core = noise.fbm2d(X - 5005, Z + 7077, cfg.seed + 77, octaves=4, freq=1 / 620.0)
    arid_warp = (noise.fbm2d(X + 3030, Z - 9090, cfg.seed + 177, octaves=2, freq=1 / 240.0) - 0.5) * 0.18
    dry_gate = _smoothstep01((cfg.biome_desert_hum_cut + 0.100 - hum_band) / 0.24)
    warm_gate = _smoothstep01((temp_band - (cfg.biome_warm_cut - 0.055)) / 0.24)
    province = _smoothstep01((arid_core + arid_warp - 0.340) / 0.30)
    return np.clip(province * dry_gate * warm_gate, 0.0, 1.0)


def _classify_biomes(cfg, X, Z, Hf, mnt, temp, hum, selector, edge_wobble):
    temp_band = np.clip(temp + edge_wobble * 0.018, 0.0, 1.0)
    hum_band = np.clip(hum + (noise.fbm2d(X - 818, Z + 181, cfg.seed + 75, octaves=2, freq=1 / 190.0) - 0.5) * 0.040, 0.0, 1.0)
    forest_style = noise.fbm2d(X + 9191, Z - 3939, cfg.seed + 76, octaves=3, freq=1 / 240.0)
    badlands_strength = _badlands_strength(cfg, X, Z, temp_band, hum_band)

    frozen = temp_band < cfg.biome_frozen_cut
    cold = (temp_band >= cfg.biome_frozen_cut) & (temp_band < cfg.biome_cold_cut)
    temperate = (temp_band >= cfg.biome_cold_cut) & (temp_band < cfg.biome_warm_cut)
    warm = temp_band >= cfg.biome_warm_cut

    biome = np.full(X.shape, "minecraft:plains", dtype=object)

    # Frozen land stays cold. Desert/badlands can never select this range;
    # hot/cold adjacency always has cold/temperate transition space between it.
    biome[frozen & (hum_band < 0.50)] = "minecraft:snowy_plains"
    biome[frozen & (hum_band >= 0.50)] = "minecraft:snowy_taiga"

    # Cold transition: taiga/forest/plains buffer the snowy band.
    biome[cold & (hum_band >= 0.56)] = "minecraft:taiga"
    biome[cold & (hum_band >= 0.43) & (hum_band < 0.56) & (selector > 0.38)] = "minecraft:forest"
    biome[cold & (hum_band < 0.43)] = "minecraft:plains"

    # Temperate land. Forest variants are selected by a second slow style
    # layer, so we get coherent birch/dark-oak forests instead of random dots.
    biome[temperate] = "minecraft:plains"
    forest = temperate & (hum_band >= cfg.biome_forest_hum_cut) & (selector > 0.28)
    biome[forest] = "minecraft:forest"
    biome[forest & (forest_style < 0.34)] = "minecraft:birch_forest"
    biome[forest & (forest_style > 0.74) & (hum_band > cfg.biome_forest_hum_cut + 0.08)] = "minecraft:dark_forest"
    biome[temperate & (hum_band >= cfg.biome_jungle_hum_cut) & (selector > 0.55)] = "minecraft:jungle"

    # Warm land: hot/dry splits into desert or mesa/badlands, hot/wet becomes
    # jungle, and the middle remains plains/sparse forest.
    biome[warm] = "minecraft:plains"
    dry_hot = warm & (temp_band >= cfg.biome_warm_cut + 0.020) & (hum_band < cfg.biome_desert_hum_cut)
    biome[dry_hot] = "minecraft:desert"
    mesa = dry_hot & (badlands_strength > 0.36)
    biome[mesa] = "minecraft:badlands"
    biome[warm & (hum_band >= max(cfg.biome_forest_hum_cut, 0.58)) & (selector > 0.62)] = "minecraft:forest"
    biome[warm & (hum_band >= cfg.biome_jungle_hum_cut)] = "minecraft:jungle"

    # Keep temperature transitions vanilla-feeling. A desert/badlands cell is
    # only allowed when it is inside a warm province, not on the blurred edge of
    # a cold/snow province. This prevents hard desert-snow contact while keeping
    # elevation completely independent from biome paint.
    if biome.ndim == 2 and biome.shape[0] >= 3 and biome.shape[1] >= 3:
        cold_buffer = _soft_mask((temp_band < cfg.biome_cold_cut + 0.080).astype(np.float64), passes=7)
        snow_buffer = _soft_mask((temp_band < cfg.biome_frozen_cut + 0.070).astype(np.float64), passes=8)
        arid_edge = ((biome == "minecraft:desert") | (biome == "minecraft:badlands")) & \
                    ((cold_buffer > 0.018) | (snow_buffer > 0.004) | (temp_band < cfg.biome_warm_cut + 0.105))
        biome[arid_edge & (hum_band >= cfg.biome_forest_hum_cut)] = "minecraft:forest"
        biome[arid_edge & (hum_band < cfg.biome_forest_hum_cut)] = "minecraft:plains"
        badlands_strength = np.where(arid_edge, 0.0, badlands_strength)

    is_arid = (biome == "minecraft:desert") | (biome == "minecraft:badlands")
    high = mnt > 0.72
    snowy_peak = high & ((Hf >= cfg.snowline_y + 8) | ((Hf >= cfg.snowline_y - 4) & (temp < cfg.biome_cold_cut - 0.10))) & ~is_arid
    biome[snowy_peak] = "minecraft:snowy_slopes"

    # Final temperature safety pass. Any hot/arid patch close enough to snowy
    # terrain becomes a neutral transition biome, matching vanilla's climate
    # bands instead of letting desert touch snow directly on the preview map.
    if biome.ndim == 2 and biome.shape[0] >= 3 and biome.shape[1] >= 3:
        snow_near = _soft_mask(((biome == "minecraft:snowy_plains") |
                                (biome == "minecraft:snowy_taiga") |
                                (biome == "minecraft:snowy_slopes")).astype(np.float64), passes=7)
        arid_near_snow = ((biome == "minecraft:desert") | (biome == "minecraft:badlands")) & (snow_near > 0.004)
        biome[arid_near_snow & (hum_band >= cfg.biome_forest_hum_cut)] = "minecraft:forest"
        biome[arid_near_snow & (hum_band < cfg.biome_forest_hum_cut)] = "minecraft:plains"
        badlands_strength = np.where(arid_near_snow, 0.0, badlands_strength)
    return biome, temp_band, hum_band, badlands_strength

def _build_river_nodes(cfg, cell_x, cell_z, spacing):
    # Finite, local river fragments. The old contour-noise river could run from
    # one side of the 1k box to the other and then every local-water tweak had to
    # fight a huge feature. These nodes make each river body its own short,
    # level aquifer-style feature with a flat water plane.
    margin = max(18.0, spacing * 0.12)
    ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5102, margin, spacing - margin)
    oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5103, margin, spacing - margin)
    angle = _rand_range(cell_x, cell_z, cfg.seed, 5104, 0.0, np.pi * 2.0)
    bias = _rand_range(cell_x, cell_z, cfg.seed, 5105, -0.105, 0.105)
    segs = 2 + int(_rand01(cell_x, cell_z, cfg.seed, 5106) * 3.0)
    nodes = [(ox, oz)]
    x = ox
    z = oz
    for i in range(1, segs + 1):
        turn = _rand_range(cell_x, cell_z, cfg.seed, 5110 + i, -0.52, 0.52)
        turn += np.sin(i * 1.81 + _rand_range(cell_x, cell_z, cfg.seed, 5111, 0.0, np.pi * 2.0)) * 0.12
        angle += turn + bias
        step = _rand_range(cell_x, cell_z, cfg.seed, 5120 + i, 16.0, 34.0)
        if i == segs:
            step *= _rand_range(cell_x, cell_z, cfg.seed, 5125 + i, 0.55, 0.88)
        x += np.cos(angle) * step
        z += np.sin(angle) * step
        nodes.append((x, z))
    return nodes


def _segment_distance_field(X, Z, ax, az, bx, bz):
    vx = bx - ax
    vz = bz - az
    length2 = vx * vx + vz * vz
    if length2 <= 0.001:
        return np.sqrt((X - ax) ** 2 + (Z - az) ** 2)
    t = ((X - ax) * vx + (Z - az) * vz) / length2
    t = np.clip(t, 0.0, 1.0)
    px = ax + vx * t
    pz = az + vz * t
    return np.sqrt((X - px) ** 2 + (Z - pz) ** 2)


def _finite_river_field(cfg, X, Z, mountain_mask, temp=None, hum=None):
    if not cfg.rivers or cfg.river_amount <= 0.0:
        zeros = np.zeros(X.shape, dtype=np.float64)
        return zeros, zeros, np.full(X.shape, int(cfg.water_level), dtype=np.int32)

    # Modern Minecraft uses local water levels for aquifer-like bodies instead
    # of letting every cave/river below sea level flood. This hybrid version does
    # the same for surface rivers: short finite paths, each with one flat water
    # surface, and terrain is only allowed to bend a few blocks toward it.
    amount = np.clip(cfg.river_amount, 0.0, 1.0)
    spacing = int(round(245.0 - amount * 55.0))
    reach = 118
    core = np.zeros(X.shape, dtype=np.float64)
    valley = np.zeros(X.shape, dtype=np.float64)
    level = np.full(X.shape, int(cfg.water_level), dtype=np.int32)
    level_strength = np.zeros(X.shape, dtype=np.float64)

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
            active = 0.28 + amount * 0.28
            if _rand01(cell_x, cell_z, cfg.seed, 5160) > active:
                continue

            nodes = _build_river_nodes(cfg, cell_x, cell_z, spacing)
            sx, sz = nodes[0]
            sy = _surface_height_at(cfg, None, sx, sz)
            body_level = int(round(sy - _rand_range(cell_x, cell_z, cfg.seed, 5161, 0.15, 0.85)))
            body_level = int(np.clip(body_level, cfg.floor_y + 8, MIN_Y + WORLD_HEIGHT - 42))
            width_base = _rand_range(cell_x, cell_z, cfg.seed, 5162, 4.8, 7.4) * (0.88 + amount * 0.24)

            for i in range(len(nodes) - 1):
                ax, az = nodes[i]
                bx, bz = nodes[i + 1]
                d = _segment_distance_field(X, Z, ax, az, bx, bz)
                width = width_base * _rand_range(cell_x, cell_z, cfg.seed, 5170 + i, 0.82, 1.22)
                bank = width + _rand_range(cell_x, cell_z, cfg.seed, 5180 + i, 13.0, 22.0)
                c = _smoothstep01((width - d) / max(width, 0.001))
                v = _smoothstep01((bank - d) / max(bank, 0.001))
                wobble = 0.82 + 0.36 * noise.fbm2d(X + cell_x * 31 + i * 17, Z - cell_z * 29 - i * 19,
                                                   cfg.seed + 5190, octaves=2, freq=1 / 38.0)
                c = np.clip(c * wobble, 0.0, 1.0)
                v = np.clip(v * (0.88 + 0.20 * wobble), 0.0, 1.0)
                stronger = c > level_strength
                level = np.where(stronger, body_level, level)
                level_strength = np.maximum(level_strength, c)
                core = np.maximum(core, c)
                valley = np.maximum(valley, v)

    if temp is not None:
        cold_suppression = np.where(temp < cfg.biome_frozen_cut + 0.02, 0.40, 1.0)
        core *= cold_suppression
        valley *= np.maximum(cold_suppression, 0.70)
    if hum is not None:
        dry_suppression = np.where(hum < 0.18, 0.70, 1.0)
        core *= dry_suppression
        valley *= np.maximum(dry_suppression, 0.80)

    mountain = mountain_mask > 0.64
    core[mountain] *= 0.24
    valley[mountain] *= 0.46
    return np.clip(core, 0.0, 1.0), np.clip(valley, 0.0, 1.0), level.astype(np.int32)


def _finite_lake_field(cfg, X, Z, mountain_mask, temp=None, hum=None):
    if not cfg.lakes or cfg.lake_amount <= 0.0:
        zeros = np.zeros(X.shape, dtype=np.float64)
        return zeros, zeros, np.full(X.shape, int(cfg.water_level), dtype=np.int32)

    amount = np.clip(cfg.lake_amount, 0.0, 1.0)
    spacing = int(round(235.0 - amount * 48.0))
    reach = 72
    core = np.zeros(X.shape, dtype=np.float64)
    bank = np.zeros(X.shape, dtype=np.float64)
    level = np.full(X.shape, int(cfg.water_level), dtype=np.int32)
    level_strength = np.zeros(X.shape, dtype=np.float64)

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
            active = 0.08 + amount * 0.18
            if _rand01(cell_x, cell_z, cfg.seed, 5310) > active:
                continue
            ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5311, 24.0, spacing - 24.0)
            oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5312, 24.0, spacing - 24.0)
            sy = _surface_height_at(cfg, None, ox, oz)
            body_level = int(round(sy - _rand_range(cell_x, cell_z, cfg.seed, 5313, 0.6, 1.2)))
            body_level = int(np.clip(body_level, cfg.floor_y + 8, MIN_Y + WORLD_HEIGHT - 42))
            rx = _rand_range(cell_x, cell_z, cfg.seed, 5314, 9.0, 22.0) * (0.85 + amount * 0.42)
            rz = _rand_range(cell_x, cell_z, cfg.seed, 5315, 9.0, 22.0) * (0.85 + amount * 0.42)
            ang = _rand_range(cell_x, cell_z, cfg.seed, 5316, 0.0, np.pi * 2.0)
            ca = np.cos(ang)
            sa = np.sin(ang)
            dx = X - ox
            dz = Z - oz
            lx = dx * ca + dz * sa
            lz = -dx * sa + dz * ca
            d = np.sqrt((lx / max(1.0, rx)) ** 2 + (lz / max(1.0, rz)) ** 2)
            c = _smoothstep01((1.0 - d) / 0.22)
            b = _smoothstep01((1.80 - d) / 0.72)
            stronger = c > level_strength
            level = np.where(stronger, body_level, level)
            level_strength = np.maximum(level_strength, c)
            core = np.maximum(core, c)
            bank = np.maximum(bank, b)

    if temp is not None:
        cold_suppression = np.where(temp < cfg.biome_frozen_cut + 0.02, 0.34, 1.0)
        core *= cold_suppression
        bank *= np.maximum(cold_suppression, 0.70)
    if hum is not None:
        dry_suppression = np.where(hum < 0.18, 0.80, 1.0)
        core *= dry_suppression
        bank *= np.maximum(dry_suppression, 0.86)
    high = mountain_mask > 0.46
    core[high] *= 0.16
    bank[high] *= 0.38
    return np.clip(core, 0.0, 1.0), np.clip(bank, 0.0, 1.0), level.astype(np.int32)


def _surface_lava_pool_field(cfg, X, Z, mountain_mask, temp=None, hum=None):
    if not getattr(cfg, "surface_lava_pools", True) or not getattr(cfg, "fluids", True):
        zeros = np.zeros(X.shape, dtype=np.float64)
        return zeros, zeros, np.full(X.shape, int(cfg.water_level), dtype=np.int32)

    spacing = 285
    reach = 44
    core = np.zeros(X.shape, dtype=np.float64)
    bank = np.zeros(X.shape, dtype=np.float64)
    level = np.full(X.shape, int(cfg.water_level), dtype=np.int32)
    level_strength = np.zeros(X.shape, dtype=np.float64)

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
            if _rand01(cell_x, cell_z, cfg.seed, 5440) > 0.155:
                continue
            ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5441, 28.0, spacing - 28.0)
            oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5442, 28.0, spacing - 28.0)
            sy = _surface_height_at(cfg, None, ox, oz)
            body_level = int(round(sy - _rand_range(cell_x, cell_z, cfg.seed, 5443, 0.20, 0.70)))
            body_level = int(np.clip(body_level, cfg.floor_y + 10, MIN_Y + WORLD_HEIGHT - 50))
            rx = _rand_range(cell_x, cell_z, cfg.seed, 5444, 3.5, 6.5)
            rz = _rand_range(cell_x, cell_z, cfg.seed, 5445, 3.5, 6.5)
            ang = _rand_range(cell_x, cell_z, cfg.seed, 5446, 0.0, np.pi * 2.0)
            ca = np.cos(ang)
            sa = np.sin(ang)
            dx = X - ox
            dz = Z - oz
            lx = dx * ca + dz * sa
            lz = -dx * sa + dz * ca
            d = np.sqrt((lx / max(1.0, rx)) ** 2 + (lz / max(1.0, rz)) ** 2)
            c = _smoothstep01((1.0 - d) / 0.24)
            b = _smoothstep01((1.55 - d) / 0.55)
            stronger = c > level_strength
            level = np.where(stronger, body_level, level)
            level_strength = np.maximum(level_strength, c)
            core = np.maximum(core, c)
            bank = np.maximum(bank, b)

    if temp is not None:
        warm_bias = np.where(temp >= cfg.biome_warm_cut - 0.04, 1.0, 0.52)
        core *= warm_bias
        bank *= np.maximum(warm_bias, 0.62)
    if hum is not None:
        wet_suppression = np.where(hum > 0.72, 0.50, 1.0)
        core *= wet_suppression
        bank *= np.maximum(wet_suppression, 0.56)
    high = mountain_mask > 0.62
    core[high] *= 0.70
    bank[high] *= 0.82
    return np.clip(core, 0.0, 1.0), np.clip(bank, 0.0, 1.0), level.astype(np.int32)


def _coord_grids(cx, cz):
    xs = cx * 16 + np.arange(16)
    zs = cz * 16 + np.arange(16)
    X, Z = np.meshgrid(xs, zs)   # [z, x]
    return X.astype(np.float64), Z.astype(np.float64)


def _raw_terrain_height_fields(cfg, X, Z):
    s = cfg.seed
    hills = (noise.fbm2d(X, Z, s + 1, octaves=4, freq=1 / 112.0) - 0.5) * 2.0
    detail = (noise.fbm2d(X, Z, s + 11, octaves=3, freq=1 / 74.0) - 0.5) * 2.0

    # Mountains should be roundish provinces with rough ridges on top, not huge
    # raised continents. The amount slider now mostly controls how many cells can
    # become mountains; the peak height comes from the amp slider. This keeps the
    # raised deep-dark world from turning large snowy areas into plateaus.
    amount = np.clip(float(cfg.mountain_amount), 0.0, 1.0)
    extreme_amount = np.clip(float(cfg.extreme_mountain_amount), 0.0, 1.0)

    mnt_mask = noise.fbm2d(X, Z, s + 2, octaves=4, freq=1 / 610.0)
    province = noise.fbm2d(X - 2900, Z + 1700, s + 16, octaves=3, freq=1 / 760.0)
    province_gate = _smoothstep01((province - (0.39 - amount * 0.050)) / 0.34)
    lo = 0.530 - 0.115 * amount
    mnt = _smoothstep01((mnt_mask - lo) / max(0.001, 0.90 - lo)) ** 1.32
    mnt *= 0.58 + province_gate * 0.42

    ridge_raw = 1.0 - np.abs(noise.fbm2d(X, Z, s + 12, octaves=3, freq=1 / 250.0) - 0.5) * 2.0
    ridge = _smoothstep01((ridge_raw - 0.30) / 0.62) ** 1.18

    range_noise = noise.fbm2d(X - 1700, Z + 2600, s + 14, octaves=4, freq=1 / 980.0)
    range_gate = _smoothstep01((range_noise - (0.625 - extreme_amount * 0.070)) / 0.30)
    range_ridge = 1.0 - np.abs(noise.fbm2d(X + 6400, Z - 3100, s + 15, octaves=4, freq=1 / 300.0) - 0.5) * 2.0
    range_ridge = _smoothstep01((range_ridge - 0.25) / 0.66) ** 1.48
    extreme = range_gate * range_ridge

    peak_noise = noise.fbm2d(X + 11200, Z - 8800, s + 18, octaves=4, freq=1 / 540.0)
    peak_gate = _smoothstep01((peak_noise - 0.655) / 0.245)
    peak_ridge = 1.0 - np.abs(noise.fbm2d(X - 14400, Z + 5300, s + 19, octaves=4, freq=1 / 190.0) - 0.5) * 2.0
    peak_ridge = _smoothstep01((peak_ridge - 0.22) / 0.60) ** 1.34
    alpine_peak = peak_gate * peak_ridge * (0.45 + 0.55 * province_gate)

    mnt_rough = (noise.fbm2d(X + 4100, Z - 2700, s + 21, octaves=3, freq=1 / 92.0) - 0.5) * 2.0
    mnt_medium = (noise.fbm2d(X - 6200, Z + 1500, s + 22, octaves=3, freq=1 / 150.0) - 0.5) * 2.0
    mnt_shape = _smoothstep01(np.maximum.reduce((mnt, extreme * 0.78, alpine_peak * 0.88)))

    Hf = (cfg.base_height
          + hills * cfg.hill_amp * 0.72
          + detail * 0.42
          + mnt * cfg.mountain_amp * 0.64
          + (mnt ** 1.18) * ridge * cfg.mountain_amp * 0.28
          + extreme * cfg.mountain_amp * 0.64
          + alpine_peak * cfg.mountain_amp * 1.70
          + mnt_shape * (mnt_medium * cfg.mountain_amp * 0.090
                         + mnt_rough * cfg.mountain_amp * 0.052))
    return Hf, mnt_shape


def _blur_height_grid(grid):
    p = np.pad(grid, 2, mode="edge")
    # Keep this kernel normalized. The old weights summed to 1.08, which subtly
    # pushed every smoothing pass uphill and made the raised-terrain version drift
    # into too many snowy/mountain cells while water-body banks kept fighting an
    # inflated local average.
    return (p[2:-2, 2:-2] * 0.28 +
            (p[1:-3, 2:-2] + p[3:-1, 2:-2] + p[2:-2, 1:-3] + p[2:-2, 3:-1]) * 0.13 +
            (p[1:-3, 1:-3] + p[1:-3, 3:-1] + p[3:-1, 1:-3] + p[3:-1, 3:-1]) * 0.045 +
            (p[0:-4, 2:-2] + p[4:, 2:-2] + p[2:-2, 0:-4] + p[2:-2, 4:]) * 0.025) / 1.08


def _relax_height_grid(grid, max_step=4.5, passes=4):
    # Local erosion-style relaxation. The previous min-neighbor/max-neighbor
    # clamp fought itself whenever a low column and high column touched the same
    # cell, which could amplify one-column cuts into long ditch valleys. Clamp
    # only against the local median and blend a little toward the local mean so
    # genuine slopes survive while biome seams and accidental trenches get worn
    # down deterministically on the expanded sample grid.
    out = grid.astype(np.float64, copy=True)
    for _ in range(max(1, int(passes))):
        p = np.pad(out, 1, mode="edge")
        n0 = p[0:-2, 1:-1]
        n1 = p[2:, 1:-1]
        n2 = p[1:-1, 0:-2]
        n3 = p[1:-1, 2:]
        d0 = p[0:-2, 0:-2]
        d1 = p[0:-2, 2:]
        d2 = p[2:, 0:-2]
        d3 = p[2:, 2:]
        neigh = np.stack((n0, n1, n2, n3, d0, d1, d2, d3), axis=0)
        med = np.median(neigh, axis=0)
        avg = (n0 + n1 + n2 + n3) * 0.25
        out = np.minimum(out, med + max_step)
        out = np.maximum(out, med - max_step)
        out = out * 0.82 + avg * 0.18
    return out


def _soft_mask(mask, passes=2):
    out = mask.astype(np.float64)
    for _ in range(max(1, int(passes))):
        out = _blur_height_grid(out)
    return np.clip(out, 0.0, 1.0)


def _terrain_height_fields(cfg, X, Z):
    # Sample terrain on a small border, smooth it, then crop back. This fixes the
    # post-height-shift vertical cone/wall look without introducing chunk seams.
    if X.ndim == 2 and Z.ndim == 2 and X.shape == Z.shape:
        h, w = X.shape
        if h > 0 and w > 0:
            margin = 4
            if h == 1 and w == 1:
                cx = float(X[0, 0])
                cz = float(Z[0, 0])
                xs = cx + np.arange(-margin, margin + 1, dtype=np.float64)
                zs = cz + np.arange(-margin, margin + 1, dtype=np.float64)
                Xe, Ze = np.meshgrid(xs, zs)
                He, Me = _raw_terrain_height_fields(cfg, Xe, Ze)
                blur1 = _blur_height_grid(He)
                blur2 = _blur_height_grid(blur1)
                He = He * 0.68 + blur1 * 0.24 + blur2 * 0.08
                He = _relax_height_grid(He, max_step=8.0, passes=1)
                Me = _blur_height_grid(Me)
                center = margin
                return He[center:center + 1, center:center + 1], Me[center:center + 1, center:center + 1]

            dx_ok = w == 1 or np.allclose(np.diff(X[0]), 1.0)
            dz_ok = h == 1 or np.allclose(np.diff(Z[:, 0]), 1.0)
            grid_ok = dx_ok and dz_ok and np.allclose(X, X[0, :][None, :]) and np.allclose(Z, Z[:, 0][:, None])
            if grid_ok:
                xs = np.arange(float(X[0, 0]) - margin, float(X[0, -1]) + margin + 1, dtype=np.float64)
                zs = np.arange(float(Z[0, 0]) - margin, float(Z[-1, 0]) + margin + 1, dtype=np.float64)
                Xe, Ze = np.meshgrid(xs, zs)
                He, Me = _raw_terrain_height_fields(cfg, Xe, Ze)
                blur1 = _blur_height_grid(He)
                blur2 = _blur_height_grid(blur1)
                He = He * 0.68 + blur1 * 0.24 + blur2 * 0.08
                He = _relax_height_grid(He, max_step=8.0, passes=1)
                Me = _blur_height_grid(Me)
                return He[margin:margin + h, margin:margin + w], Me[margin:margin + h, margin:margin + w]

    return _raw_terrain_height_fields(cfg, X, Z)


def _regular_unit_grid_info(X, Z):
    if X.ndim != 2 or Z.ndim != 2 or X.shape != Z.shape:
        return None
    h, w = X.shape
    if h <= 0 or w <= 0:
        return None

    if w > 1 and not np.allclose(np.diff(X[0]), 1.0):
        return None
    if h > 1 and not np.allclose(np.diff(Z[:, 0]), 1.0):
        return None
    if w > 1 and not np.allclose(X, X[0, :][None, :]):
        return None
    if h > 1 and not np.allclose(Z, Z[:, 0][:, None]):
        return None

    return h, w, float(X[0, 0]), float(X[0, -1]), float(Z[0, 0]), float(Z[-1, 0])


def _crop_field_dict(fields, margin, h, w):
    out = {}
    for key, value in fields.items():
        if isinstance(value, np.ndarray) and value.ndim == 2 and \
                value.shape[0] >= margin + h and value.shape[1] >= margin + w:
            out[key] = value[margin:margin + h, margin:margin + w]
        else:
            out[key] = value
    return out


def _smooth_final_height_grid(Hf):
    if Hf.ndim != 2 or Hf.shape[0] < 3 or Hf.shape[1] < 3:
        return Hf

    # Smooth the height field as one continuous terrain surface, but do not
    # crush the mountain range noise. Biomes no longer edit elevation, so this
    # only needs to remove accidental one-column artifacts and not forcibly cap
    # every slope into shallow terraces.
    blur1 = _blur_height_grid(Hf)
    blur2 = _blur_height_grid(blur1)
    smoothed = Hf * 0.78 + blur1 * 0.16 + blur2 * 0.06
    return _relax_height_grid(smoothed, max_step=8.0, passes=1)


def _smooth_climate_grid(temp, hum, selector, edge_wobble):
    if temp.ndim != 2 or temp.shape[0] < 3 or temp.shape[1] < 3:
        return temp, hum, selector, edge_wobble

    # Climate should be broad and continuous. Sharp high-frequency temperature
    # noise is what allowed desert/snow to touch directly after the vertical
    # terrain shift. Height stays independent from biomes; only the paint layer
    # gets this wider temperature/humidity blending.
    for _ in range(4):
        temp = temp * 0.55 + _blur_height_grid(temp) * 0.45
        hum = hum * 0.62 + _blur_height_grid(hum) * 0.38
        selector = selector * 0.70 + _blur_height_grid(selector) * 0.30
        edge_wobble = edge_wobble * 0.45 + _blur_height_grid(edge_wobble) * 0.55
    return np.clip(temp, 0.0, 1.0), np.clip(hum, 0.0, 1.0), selector, edge_wobble


def _fields(cfg, blocks, X, Z):
    """Return everything the column needs. All arrays are [z, x]."""
    info = _regular_unit_grid_info(X, Z)
    if info is not None:
        h, w, x0, x1, z0, z1 = info
        # Chunks and one-column structure lookups get an expanded border so the
        # final erosion pass has context and cannot create seams. Large overview
        # grids keep the cheaper direct path because they are only UI samples.
        if max(h, w) <= 64:
            margin = 10
            xs = np.arange(x0 - margin, x1 + margin + 1, dtype=np.float64)
            zs = np.arange(z0 - margin, z1 + margin + 1, dtype=np.float64)
            Xe, Ze = np.meshgrid(xs, zs)
            fields = _fields_core(cfg, blocks, Xe, Ze, smooth_heights=True)
            return _crop_field_dict(fields, margin, h, w)

    return _fields_core(cfg, blocks, X, Z, smooth_heights=False)


def _fields_core(cfg, blocks, X, Z, smooth_heights):
    s = cfg.seed
    wl = cfg.water_level

    # --- base terrain height -------------------------------------------
    Hf, mnt = _terrain_height_fields(cfg, X, Z)

    # --- climate --------------------------------------------------------
    temp, hum, selector, edge_wobble = _base_climate_fields(cfg, X, Z, Hf)
    if smooth_heights:
        temp, hum, selector, edge_wobble = _smooth_climate_grid(temp, hum, selector, edge_wobble)
    biome, temp_band, hum_band, badlands_strength = _classify_biomes(cfg, X, Z, Hf, mnt, temp, hum, selector, edge_wobble)

    # Keep hot/normal biome paint independent from elevation, but let cold
    # climates bias the existing mountain/ridge field upward in one broad smooth
    # mask. That gives snowy regions naturally hillier, higher terrain without
    # creating a desert/snow staircase seam at the exact biome edge.
    is_desert = biome == "minecraft:desert"
    is_badlands = biome == "minecraft:badlands"
    is_arid = is_desert | is_badlands

    cold_relief = _smoothstep01(((cfg.biome_cold_cut + 0.14) - temp_band) / 0.34)
    cold_relief *= 1.0 - _smoothstep01((hum_band - 0.94) / 0.12)

    frozen_paint = ((biome == "minecraft:snowy_plains") |
                    (biome == "minecraft:snowy_taiga") |
                    (biome == "minecraft:snowy_slopes")).astype(np.float64)
    if frozen_paint.ndim == 2 and min(frozen_paint.shape) >= 3:
        frozen_region = _soft_mask(frozen_paint, passes=4)
    else:
        frozen_region = frozen_paint

    frozen_relief = _smoothstep01(((cfg.biome_frozen_cut + 0.070) - temp_band) / 0.20)
    frozen_relief *= 1.0 - _smoothstep01((hum_band - 0.965) / 0.11)
    frozen_relief = np.maximum(frozen_relief, frozen_region * 0.92)

    cold_lumps = (noise.fbm2d(X + 5100, Z - 5100, s + 74, octaves=3, freq=1 / 150.0) - 0.5) * 2.0
    cold_ridges = 1.0 - np.abs(noise.fbm2d(X - 9700, Z + 9700, s + 75, octaves=3, freq=1 / 220.0) - 0.5) * 2.0
    cold_ridges = _smoothstep01((cold_ridges - 0.24) / 0.66)

    snow_range = noise.fbm2d(X + 14800, Z - 11800, s + 176, octaves=4, freq=1 / 430.0)
    snow_range = _smoothstep01((snow_range - 0.31) / 0.45)
    snow_ridges = 1.0 - np.abs(noise.fbm2d(X - 18100, Z + 12700, s + 177, octaves=4, freq=1 / 185.0) - 0.5) * 2.0
    snow_ridges = _smoothstep01((snow_ridges - 0.20) / 0.64) ** 1.14
    snow_peaks = 1.0 - np.abs(noise.fbm2d(X + 22100, Z - 16100, s + 178, octaves=3, freq=1 / 92.0) - 0.5) * 2.0
    snow_peaks = _smoothstep01((snow_peaks - 0.34) / 0.52) ** 1.28
    snow_roll = (noise.fbm2d(X - 23300, Z + 17500, s + 179, octaves=3, freq=1 / 125.0) - 0.5) * 2.0

    # Keep snow regions hillier than temperate lowlands, but do not let snow
    # itself manufacture continent-sized cliff walls. This matches the older
    # pre-extreme pass behavior while still allowing isolated rocky snowy peaks.
    snow_alpine = frozen_relief * (0.52 + 0.48 * snow_range)
    Hf += cold_relief * (cold_lumps * cfg.hill_amp * 0.30 + cold_ridges * cfg.mountain_amp * 0.14 * (0.30 + 0.70 * mnt))
    Hf += snow_alpine * (snow_roll * cfg.hill_amp * 0.78 +
                         snow_range * cfg.mountain_amp * 0.20 +
                         snow_ridges * cfg.mountain_amp * 0.64 +
                         snow_ridges * snow_peaks * cfg.mountain_amp * 0.74)
    mnt = np.clip(np.maximum(mnt, snow_alpine * (snow_ridges * 0.70 + snow_peaks * 0.30)), 0.0, 1.0)

    if smooth_heights:
        lowland_flat = 1.0 - _smoothstep01((mnt - 0.14) / 0.24)
        low_blur = _blur_height_grid(Hf)
        low_blur2 = _blur_height_grid(low_blur)
        low_smoothed = Hf * 0.68 + low_blur * 0.22 + low_blur2 * 0.10
        Hf = Hf * (1.0 - lowland_flat * 0.28) + low_smoothed * (lowland_flat * 0.28)
        Hf = _smooth_final_height_grid(Hf)

    # --- finite rivers + small lakes -----------------------------------
    river_core, river_valley, river_level = _finite_river_field(cfg, X, Z, mnt, temp_band, hum_band)
    lake_core, lake_bank, lake_level = _finite_lake_field(cfg, X, Z, mnt, temp_band, hum_band)
    lava_core, lava_bank, lava_level = _surface_lava_pool_field(cfg, X, Z, mnt, temp_band, hum_band)

    # Rivers and ponds use flat local water planes. The important distinction is
    # that the water level is decided by the feature body first, then nearby
    # terrain is gently shaped into banks. We never ask the feature to excavate
    # down to a global Y, which is what created the giant water ravines.
    pre_water_hf = Hf.copy()
    local_blur = _blur_height_grid(pre_water_hf) if pre_water_hf.ndim == 2 and min(pre_water_hf.shape) >= 3 else pre_water_hf
    local_slope = np.abs(pre_water_hf - local_blur)

    feature_level = np.where(lake_core > river_core, lake_level, river_level).astype(np.int32)
    feature_surface_f = feature_level.astype(np.float64)
    cut_to_surface = pre_water_hf - feature_surface_f

    mountain_water_gate = 1.0 - _smoothstep01((mnt - 0.30) / 0.22)
    slope_water_gate = 1.0 - _smoothstep01((local_slope - 4.6) / 5.8)
    shallow_gate = _smoothstep01((cut_to_surface + 0.10) / 0.45) * (1.0 - _smoothstep01((cut_to_surface - 2.20) / 0.90))
    water_gate = np.clip(mountain_water_gate * slope_water_gate * shallow_gate, 0.0, 1.0)

    river_core = river_core * water_gate
    river_valley = river_valley * (0.16 + 0.84 * water_gate)
    lake_core = lake_core * water_gate
    lake_bank = lake_bank * (0.18 + 0.82 * water_gate)

    river_water = river_core > 0.185
    lake_water = lake_core > 0.315
    water_feature = river_water | lake_water

    bank_strength = np.clip(river_valley * 1.18 + lake_bank * 1.08, 0.0, 1.0)
    bank_noise = (noise.fbm2d(X + 330, Z - 660, s + 73, octaves=2, freq=1 / 44.0) - 0.5) * 0.18

    floodplain = bank_strength * (1.0 - _smoothstep01((np.abs(pre_water_hf - feature_surface_f) - 3.6) / 3.0))
    floodplain = np.clip(floodplain, 0.0, 1.0)
    floodplain_target = feature_surface_f + 0.14 + bank_noise
    Hf = Hf * (1.0 - floodplain * 0.46) + floodplain_target * (floodplain * 0.46)

    bank_target = feature_surface_f + 0.22 + bank_noise
    bank_delta = np.maximum(0.0, Hf - bank_target)
    bank_cut = np.minimum(bank_delta, 1.85) * bank_strength * 0.64
    Hf = Hf - bank_cut

    river_depth = 0.82 + 0.42 * np.clip(river_core, 0.0, 1.0)
    lake_depth = 0.90 + 0.58 * np.clip(lake_core, 0.0, 1.0)
    wanted_bed = np.where(lake_core > river_core,
                          feature_surface_f - lake_depth,
                          feature_surface_f - river_depth)
    max_cut = 1.72 + 0.32 * np.clip(lake_core, 0.0, 1.0)
    bed = np.maximum(wanted_bed, pre_water_hf - max_cut)
    bed = np.minimum(bed, feature_surface_f - 1.0)
    Hf = np.where(water_feature, np.minimum(Hf, bed), Hf)

    # Rare hot surface lava pools use the same local-plane system as water, but
    # they are smaller and cannot overlap river/lake floodplains.
    lava_surface_f = lava_level.astype(np.float64)
    lava_cut = Hf - lava_surface_f
    lava_gate = (1.0 - np.clip(bank_strength * 1.55, 0.0, 1.0))
    lava_gate *= 1.0 - _smoothstep01((np.abs(Hf - lava_surface_f) - 2.4) / 1.2)
    lava_core = lava_core * np.clip(lava_gate, 0.0, 1.0)
    lava_bank = lava_bank * np.clip(lava_gate, 0.0, 1.0)
    lava_pool = lava_core > 0.425
    lava_bank_strength = np.clip(lava_bank, 0.0, 1.0)
    lava_target = lava_surface_f + 0.08
    Hf = Hf * (1.0 - lava_bank_strength * 0.32) + lava_target * (lava_bank_strength * 0.32)
    lava_bed = np.minimum(lava_surface_f - 1.0, Hf - np.clip(lava_cut, 0.0, 1.35))
    lava_bed = np.minimum(lava_bed, lava_surface_f - 1.0)
    Hf = np.where(lava_pool, np.minimum(Hf, lava_bed), Hf)

    H = np.rint(np.clip(Hf, cfg.floor_y + 3, MIN_Y + WORLD_HEIGHT - 40)).astype(np.int32)
    H = np.where(water_feature, np.minimum(H, feature_level - 1), H)
    H = np.where(lava_pool, np.minimum(H, lava_level - 1), H)

    # Natural sea-level water can still exist in low basins. Finite rivers and
    # ponds use their own local water surface and are not pulled down to wl.
    sea_water = H <= wl - 1
    is_lava = lava_pool & ~water_feature & ~sea_water
    is_water = (sea_water | water_feature) & ~is_lava
    water_surface = np.full(X.shape, int(wl), dtype=np.int32)
    water_surface = np.where(water_feature, feature_level, water_surface).astype(np.int32)
    water_surface = np.where(is_water, np.maximum(water_surface, H + 1), H).astype(np.int32)
    lava_surface = np.where(is_lava, np.maximum(lava_level, H + 1), H).astype(np.int32)
    is_river = river_water & is_water
    biome[is_river] = "minecraft:river"

    # Beaches/river banks are local paint around the feature. Do not convert the
    # whole arid biome edge into a sandstone retaining wall; just paint the shallow
    # banks that actually border water.
    local_shore = (river_valley > 0.110) | (lake_bank > 0.135)
    sea_shore = (H <= wl + 1) & (H >= wl - 2)
    is_beach = (~is_water) & (~is_lava) & (sea_shore | local_shore)
    biome[is_beach & (biome != "minecraft:snowy_plains") &
          (biome != "minecraft:snowy_taiga") & (biome != "minecraft:snowy_slopes")] = "minecraft:beach"

    # --- snow ----------------------------------------------------------
    frozen_land = (biome == "minecraft:snowy_plains") | (biome == "minecraft:snowy_taiga") | (biome == "minecraft:snowy_slopes")
    if H.ndim == 2 and min(H.shape) >= 3:
        hb = _blur_height_grid(H.astype(np.float64))
        local_relief = np.abs(H.astype(np.float64) - hb)
    else:
        local_relief = np.zeros_like(H, dtype=np.float64)
    alpine_snow = frozen_land & ((H >= cfg.base_height + 31) |
                                 ((H >= cfg.base_height + 22) & ((mnt > 0.34) | (local_relief > 2.2))))
    biome[alpine_snow] = "minecraft:snowy_slopes"
    frozen_land = (biome == "minecraft:snowy_plains") | (biome == "minecraft:snowy_taiga") | (biome == "minecraft:snowy_slopes")
    is_snowy = (~is_arid) & (~is_water) & ((H >= cfg.snowline_y + 6) | frozen_land)

    # --- top + subsurface block per column -----------------------------
    top = np.full(X.shape, blocks.grass, dtype=np.int32)
    sub = np.full(X.shape, blocks.dirt, dtype=np.int32)

    top[biome == "minecraft:taiga"] = blocks.podzol
    top[biome == "minecraft:snowy_taiga"] = blocks.grass_snow
    top[(biome == "minecraft:snowy_plains")] = blocks.grass_snow
    top[biome == "minecraft:snowy_slopes"] = blocks.snow_block
    sub[biome == "minecraft:snowy_slopes"] = blocks.stone
    snowy_rock_noise = noise.fbm2d(X - 27200, Z + 19100, s + 180, octaves=3, freq=1 / 68.0)
    snowy_rock = ((biome == "minecraft:snowy_slopes") &
                  ((H >= cfg.base_height + 76) |
                   ((H >= cfg.base_height + 50) & ((mnt > 0.52) | (snowy_rock_noise > 0.54))) |
                   (local_relief > 3.0)))
    top[snowy_rock] = blocks.stone
    sub[snowy_rock] = blocks.stone
    top[is_snowy & (top == blocks.grass)] = blocks.grass_snow

    top[is_desert] = blocks.sand
    sub[is_desert] = blocks.sandstone
    top[is_badlands] = blocks.red_sand
    sub[is_badlands] = blocks.terracotta
    top[is_beach] = blocks.sand
    sub[is_beach] = blocks.sandstone

    return {
        "H": H, "biome": biome, "top": top, "sub": sub,
        "is_water": is_water, "is_river": is_river, "is_beach": is_beach,
        "water_surface": water_surface, "is_lava": is_lava, "lava_surface": lava_surface,
        "is_desert": is_desert, "is_badlands": is_badlands,
        "is_snowy": is_snowy, "mnt": mnt,
        "badlands_strength": badlands_strength,
    }


# ---------------------------------------------------------------------------
# Chunk generation
# ---------------------------------------------------------------------------

def generate_nether_chunk(cfg, blocks, biome_reg, cx, cz, bounds, rng_seed):
    X, Z = _coord_grids(cx, cz)
    nxmin, nxmax, nzmin, nzmax = bounds
    inside = (X >= nxmin) & (X <= nxmax) & (Z >= nzmin) & (Z <= nzmax)
    on_wall = inside & ((X == nxmin) | (X == nxmax) | (Z == nzmin) | (Z == nzmax))

    block_ids = np.zeros((WORLD_HEIGHT, 16, 16), dtype=np.int16)
    biome_ids = np.zeros((16, 16), dtype=np.int16)
    H = np.full((16, 16), NETHER_BOX_CEILING_Y, dtype=np.int32)
    is_water = np.zeros((16, 16), dtype=bool)
    block_entities = []

    if not inside.any():
        biome_str = np.full((16, 16), "minecraft:plains", dtype=object)
        for iz in range(16):
            for ix in range(16):
                biome_ids[iz, ix] = biome_reg.get("minecraft:plains")
        return block_ids, biome_ids, H, biome_str, inside, is_water, block_entities

    rng = np.random.default_rng(rng_seed)
    nbm = underworld.nether_biome_map(cfg, X, Z)
    names = np.array(underworld.NETHER_BIOMES, dtype=object)
    biome_str = np.where(inside, names[nbm], "minecraft:plains")

    y_lo = MIN_Y + 1
    y_hi = NETHER_BOX_CEILING_Y - 1
    yr = np.arange(y_lo, y_hi + 1)
    band = slice(y_lo - MIN_Y, y_hi - MIN_Y + 1)
    nb = yr.size
    Xf = X.astype(np.float64)
    Zf = Z.astype(np.float64)
    Yb = yr[:, None, None].astype(np.float64)
    X3 = np.broadcast_to(Xf[None, :, :], (nb, 16, 16))
    Z3 = np.broadcast_to(Zf[None, :, :], (nb, 16, 16))
    Y3 = np.broadcast_to(Yb, (nb, 16, 16))
    interior = inside & ~on_wall
    inside_b = np.broadcast_to(interior[None, :, :], (nb, 16, 16))
    underworld._fill_nether(cfg, blocks, block_ids, band, X3, Y3, Z3,
                            inside_b, yr, y_lo, y_hi, nbm, rng)

    world_y = (np.arange(WORLD_HEIGHT) + MIN_Y).reshape(-1, 1, 1)
    inside3 = inside[None, :, :]
    box_y = (world_y >= MIN_Y) & (world_y <= NETHER_BOX_CEILING_Y)
    block_ids[np.broadcast_to(inside3 & (world_y == MIN_Y), block_ids.shape)] = blocks.bedrock
    block_ids[np.broadcast_to(inside3 & (world_y == NETHER_BOX_CEILING_Y), block_ids.shape)] = blocks.bedrock
    block_ids[np.broadcast_to(on_wall[None, :, :] & box_y, block_ids.shape)] = blocks.bedrock

    if cfg.structures and cfg.structure_plan:
        block_entities = structures.stamp_structures(
            cfg, blocks, block_ids, cx, cz, MIN_Y, WORLD_HEIGHT)

    for iz in range(16):
        for ix in range(16):
            b = biome_str[iz, ix] if inside[iz, ix] else "minecraft:plains"
            biome_ids[iz, ix] = biome_reg.get(b)

    return block_ids, biome_ids, H, biome_str, inside, is_water, block_entities


def generate_chunk(cfg, blocks, biome_reg, cx, cz, xmin, xmax, rng_seed):
    X, Z = _coord_grids(cx, cz)                    # [z, x]
    zz, xx = np.meshgrid(np.arange(16), np.arange(16), indexing="ij")

    inside = (X >= xmin) & (X <= xmax) & (Z >= xmin) & (Z <= xmax)
    on_wall = inside & ((X == xmin) | (X == xmax) | (Z == xmin) | (Z == xmax))

    f = _fields(cfg, blocks, X, Z)
    H = f["H"]; biome_str = f["biome"]; top = f["top"]; sub = f["sub"]
    is_water = f["is_water"]; is_river = f["is_river"]; is_beach = f["is_beach"]
    water_surface = f["water_surface"]
    is_lava = f["is_lava"]; lava_surface = f["lava_surface"]
    is_desert = f["is_desert"]; is_badlands = f["is_badlands"]; is_snowy = f["is_snowy"]

    block_ids = np.zeros((WORLD_HEIGHT, 16, 16), dtype=np.int16)  # [y, z, x]
    world_y = (np.arange(WORLD_HEIGHT) + MIN_Y).reshape(-1, 1, 1)
    inside3 = inside[None, :, :]
    Hs = H[None, :, :]
    overworld_floor = cfg.floor_y
    floor = MIN_Y if cfg.deep_dark else cfg.floor_y
    rng = np.random.default_rng(rng_seed)

    # --- columns: bedrock floor, stone body, subsurface, top -----------
    block_ids[np.broadcast_to(inside3 & (world_y == floor), block_ids.shape)] = blocks.bedrock
    block_ids[inside3 & (world_y > floor) & (world_y <= Hs - 4)] = blocks.stone
    sub3 = np.broadcast_to(sub[None, :, :], block_ids.shape)
    dirt_band = inside3 & (world_y >= Hs - 3) & (world_y <= Hs)
    block_ids[dirt_band] = sub3[dirt_band]

    if is_badlands.any():
        _paint_badlands_bands(cfg, blocks, block_ids, X, Z, H, is_badlands, inside, world_y, overworld_floor)

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
        _carve_caves(cfg, blocks, block_ids, X, Z, H, inside, overworld_floor)
        if cfg.deep_dark:
            _carve_deep_dark_with_regular_caverns(cfg, blocks, block_ids, X, Z, H, inside, floor)
    if cfg.ravines:
        _carve_ravines(cfg, blocks, block_ids, X, Z, H, inside, on_wall,
                       world_y, floor, is_water | is_lava, is_beach)

    if is_water.any():
        _seal_water_beds(cfg, blocks, block_ids, H, is_water, is_river, inside,
                         on_wall, floor)

    # --- surface top block (land) --------------------------------------
    land = inside & ~on_wall & ~is_water & ~is_lava & (H > floor + 3)
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
        water_top = water_surface[None, :, :]
        water_fill = inside3 & is_water[None, :, :] & (world_y > Hs) & (world_y <= water_top)
        block_ids[water_fill & (block_ids == 0)] = blocks.water

    if is_lava.any():
        lcol = inside & ~on_wall & is_lava
        ly, lz, lx = arr_surf[lcol], zz[lcol], xx[lcol]
        lava_bed_noise = noise.fbm2d(X + 910, Z - 910, cfg.seed + 76, octaves=2, freq=1 / 9.0)
        lava_bed = np.where(lava_bed_noise > 0.58, blocks.magma, blocks.stone).astype(np.int16)
        block_ids[ly, lz, lx] = lava_bed[lcol]
        lava_top = lava_surface[None, :, :]
        lava_fill = inside3 & is_lava[None, :, :] & (world_y > Hs) & (world_y <= lava_top)
        block_ids[lava_fill & (block_ids == 0)] = blocks.lava

    # --- underground fluids + static liquid settling -------------------
    if cfg.fluids:
        _place_fluids(cfg, blocks, block_ids, X, Z, inside, world_y, floor, rng)

    # Minecraft will not tick freshly written region files before the player
    # sees them. Precompute bounded water/lava flow so river edges become
    # waterfalls and cave lava pools look settled instead of frozen source
    # sheets. This is chunk-local by design, but it catches the visible cases
    # inside generated ravines/caves.
    _settle_liquid_flow(cfg, blocks, block_ids, inside, floor)

    if cfg.deep_dark:
        underworld.apply_deep_dark_palette(cfg, blocks, block_ids, X, Z, inside, rng)

    # --- ores (replace stone, stone variants, or badlands terracotta) ---
    replaceable = (blocks.stone, blocks.andesite, blocks.diorite,
                   blocks.granite, blocks.terracotta, blocks.orange_terracotta,
                   blocks.yellow_terracotta, blocks.brown_terracotta,
                   blocks.red_terracotta, blocks.white_terracotta,
                   blocks.deepslate, blocks.tuff, blocks.cobbled_deepslate)
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

    if cfg.deep_dark:
        _place_deep_dark_ores(cfg, blocks, block_ids, H, inside, rng)

    # --- trees ----------------------------------------------------------
    if cfg.trees:
        _place_trees(cfg, blocks, block_ids, H, biome_str, is_water | is_lava, inside,
                     on_wall, rng)

    # --- surface decoration (grass, flowers, sugar cane, cactus) -------
    if cfg.decorations:
        _decorate(cfg, blocks, block_ids, H, biome_str, is_water | is_lava,
                  is_desert | is_badlands, is_snowy, inside, on_wall, rng)

    # --- snow layer -----------------------------------------------------
    if is_snowy.any():
        cols = inside & ~on_wall & is_snowy & ~is_water & ~is_lava & (H > floor + 3)
        sy = (H - MIN_Y) + 1
        valid = cols & (sy < WORLD_HEIGHT)
        syy, szz, sxx = sy[valid], zz[valid], xx[valid]
        below = block_ids[syy - 1, szz, sxx]
        ok = (block_ids[syy, szz, sxx] == 0) & (below != 0) & (below != blocks.water)
        block_ids[syy[ok], szz[ok], sxx[ok]] = blocks.snow_layer

    # --- structures (stamp the parts overlapping this chunk) ------------
    block_entities = []
    if cfg.structures and cfg.structure_plan and inside.any():
        block_entities = structures.stamp_structures(
            cfg, blocks, block_ids, cx, cz, MIN_Y, WORLD_HEIGHT)

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

    return block_ids, biome_ids, H, biome_str, inside, is_water, block_entities


# ---------------------------------------------------------------------------
# Carving / feature helpers
# ---------------------------------------------------------------------------

def _surface_height_at(cfg, blocks, x, z):
    # Cached because every chunk checks the same nearby cave-system cells.
    # Without this, denser cave placement spends most of its time recomputing
    # one-column terrain estimates for the same cave mouths.
    key = (int(cfg.seed), int(cfg.floor_y), int(round(cfg.base_height * 16.0)),
           int(round(cfg.hill_amp * 16.0)), int(round(cfg.mountain_amp * 16.0)),
           int(round(cfg.mountain_amount * 4096.0)), int(round(x * 16.0)),
           int(round(z * 16.0)))
    cached = _SURFACE_HEIGHT_CACHE.get(key)
    if cached is not None:
        return cached

    if len(_SURFACE_HEIGHT_CACHE) > 200000:
        _SURFACE_HEIGHT_CACHE.clear()

    # Fast terrain-only estimate for cave mouths. Calling _fields() here would
    # recursively evaluate climate/river feature scans for every candidate cave
    # system, which is far too expensive during chunk carving.
    X = np.array([[float(x)]], dtype=np.float64)
    Z = np.array([[float(z)]], dtype=np.float64)
    Hf, _ = _terrain_height_fields(cfg, X, Z)
    value = int(round(float(np.clip(Hf, cfg.floor_y + 3, MIN_Y + WORLD_HEIGHT - 40)[0, 0])))
    _SURFACE_HEIGHT_CACHE[key] = value
    return value

def _diamond_target_y(cfg, floor):
    target = -12
    for ore in cfg.ores:
        if "diamond" in ore.name:
            target = min(ore.y_max - 2, ore.y_min + 4)
            break
    return max(floor + 5, min(8, target))


def _solid_carve_mask(block_ids, blocks):
    return ((block_ids == blocks.stone) | (block_ids == blocks.dirt) |
            (block_ids == blocks.grass) | (block_ids == blocks.grass_snow) |
            (block_ids == blocks.sand) | (block_ids == blocks.sandstone) |
            (block_ids == blocks.red_sand) | (block_ids == blocks.red_sandstone) |
            (block_ids == blocks.terracotta) |
            (block_ids == blocks.orange_terracotta) |
            (block_ids == blocks.yellow_terracotta) |
            (block_ids == blocks.brown_terracotta) |
            (block_ids == blocks.red_terracotta) |
            (block_ids == blocks.white_terracotta) |
            (block_ids == blocks.andesite) | (block_ids == blocks.diorite) |
            (block_ids == blocks.granite) | (block_ids == blocks.gravel) |
            (block_ids == blocks.podzol) | (block_ids == blocks.snow_block))


def _paint_badlands_bands(cfg, blocks, block_ids, X, Z, H, is_badlands, inside,
                          world_y, floor):
    colors = (blocks.terracotta, blocks.orange_terracotta, blocks.yellow_terracotta,
              blocks.brown_terracotta, blocks.red_terracotta, blocks.white_terracotta)
    Y = world_y
    wobble = ((noise.fbm2d(X + 220, Z - 440, cfg.seed + 8601, octaves=3, freq=1 / 46.0) - 0.5) * 5.0).astype(np.int32)
    band = ((Y + wobble[None, :, :] + 512) // 5) % len(colors)
    mesa_body = inside[None, :, :] & is_badlands[None, :, :] & (Y > floor) & (Y <= H[None, :, :] - 1)
    replace = ((block_ids == blocks.stone) | (block_ids == blocks.dirt) |
               (block_ids == blocks.sandstone) | (block_ids == blocks.terracotta))
    for i, color in enumerate(colors):
        m = mesa_body & replace & (band == i)
        block_ids[m] = color

    cap = inside[None, :, :] & is_badlands[None, :, :] & (Y >= H[None, :, :] - 3) & (Y <= H[None, :, :])
    block_ids[cap & (block_ids != blocks.bedrock)] = blocks.red_sand


def _seal_water_beds(cfg, blocks, block_ids, H, is_water, is_river, inside,
                     on_wall, floor):
    # Rivers/lakes are generated before cave carving. After caves/ravines run,
    # repair a shallow buffer around every water body so cave mouths cannot cut
    # straight into the river edge and turn a two-block stream into a vertical
    # stone ravine. Actual water columns get the deepest cap; nearby banks get a
    # thinner plug that still allows caves a little farther away.
    protected = (is_water | is_river) & inside & ~on_wall
    if not protected.any():
        return

    buffer = _soft_mask(protected, passes=3)
    for iz in range(16):
        for ix in range(16):
            if not inside[iz, ix] or on_wall[iz, ix] or buffer[iz, ix] <= 0.035:
                continue

            surf = int(H[iz, ix])
            if is_water[iz, ix]:
                depth = 8 if is_river[iz, ix] else 7
            else:
                depth = 4 + int(buffer[iz, ix] * 5.0)

            y0 = max(floor + 1, surf - depth)
            y1 = min(surf, MIN_Y + WORLD_HEIGHT - 2)
            for wy in range(y0, y1 + 1):
                yi = wy - MIN_Y
                if block_ids[yi, iz, ix] != blocks.air:
                    continue
                if wy >= surf - 1:
                    block_ids[yi, iz, ix] = blocks.sand
                elif wy >= surf - 4:
                    block_ids[yi, iz, ix] = blocks.sandstone
                else:
                    block_ids[yi, iz, ix] = blocks.stone


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


def _carve_caves(cfg, blocks, block_ids, X, Z, H, inside, floor):
    # Hybrid cave pass:
    #   1) a bounded 1.12-ish noise pass for familiar messy underground caves,
    #   2) guaranteed surface-to-diamond systems,
    #   3) extra underground branch networks that do not need surface mouths.
    # This avoids the boring single-tube cave while keeping real routes downward.
    intensity = max(0.2, float(cfg.cave_intensity))
    target_y = _diamond_target_y(cfg, floor)

    _carve_legacy_cave_noise(cfg, blocks, block_ids, X, Z, H, inside, floor,
                             target_y, intensity)
    _carve_surface_cave_systems(cfg, blocks, block_ids, X, Z, H, inside, floor,
                                target_y, intensity)
    # Do not add per-chunk fake mouths here. They look common, but because they
    # are local chunk features they often stop after a few blocks and create the
    # bad square dead-end holes seen from the surface. Surface openings now come
    # only from real feature-cell cave systems that continue across chunks.
    _carve_underground_cave_networks(cfg, blocks, block_ids, X, Z, H, inside,
                                     floor, target_y, intensity)


def _carve_deep_dark_with_regular_caverns(cfg, blocks, block_ids, X, Z, H, inside, floor):
    # Deep dark uses the same cave-carving primitives as the normal cavern
    # layer. The only difference is the later palette pass, which swaps the
    # lower rock to deepslate/tuff/sculk. This avoids the old separate deep-dark
    # density field that created flat ceilings, bedrock-floor stadiums and a
    # hard vertical drop from the cavern layer above.
    intensity = max(0.2, float(cfg.cave_intensity))
    target_y = max(floor + 8, min(cfg.floor_y - 12, _diamond_target_y(cfg, floor) - 26))

    _carve_legacy_cave_noise(cfg, blocks, block_ids, X, Z, H, inside, floor,
                             target_y, intensity)
    _carve_underground_cave_networks(cfg, blocks, block_ids, X, Z, H, inside,
                                     floor, target_y, intensity)


def _carve_legacy_cave_noise(cfg, blocks, block_ids, X, Z, H, inside, floor,
                             target_y, intensity):
    # This is the part that feels closest to old Minecraft caves: two warped 3D
    # noise fields intersect into irregular tunnels. It is gated by large cave
    # provinces and kept below the surface so it fills underground without
    # shredding every hillside.
    cave_lo = max(floor + 4, target_y - 8)
    cave_hi = min(int(H.max()) - 7, cfg.water_level + 46)
    if cave_hi <= cave_lo:
        return

    ys = np.arange(cave_lo, cave_hi + 1, dtype=np.float64)
    Xc = X[None, :, :]
    Zc = Z[None, :, :]
    Yc = ys.reshape(-1, 1, 1)

    warp_x = (noise.fbm3d(Xc + 17, Yc - 91, Zc + 43,
                          cfg.seed + 9301, octaves=2, freq=1 / 92.0) - 0.5) * 18.0
    warp_z = (noise.fbm3d(Xc - 71, Yc + 29, Zc - 11,
                          cfg.seed + 9302, octaves=2, freq=1 / 92.0) - 0.5) * 18.0

    n1 = noise.fbm3d(Xc + warp_x, Yc, Zc + warp_z,
                     cfg.seed + 9303, octaves=2, freq=1 / 33.0)
    n2 = noise.fbm3d(Xc - 109.0 + warp_z * 0.5, Yc + 47.0, Zc + 83.0 - warp_x * 0.5,
                     cfg.seed + 9304, octaves=2, freq=1 / 39.0)
    n3 = noise.fbm3d(Xc + 211.0, Yc - 31.0, Zc - 167.0,
                     cfg.seed + 9305, octaves=1, freq=1 / 71.0)

    province = noise.fbm2d(X + 5000.0, Z - 5000.0,
                           cfg.seed + 9306, octaves=3, freq=1 / 165.0)
    local = noise.fbm3d(Xc - 3000.0, Yc * 0.35, Zc + 3000.0,
                        cfg.seed + 9307, octaves=2, freq=1 / 96.0)
    province_gate = np.clip((province - 0.33) / 0.27, 0.0, 1.0)[None, :, :]
    local_gate = np.clip((local - 0.38) / 0.28, 0.0, 1.0)

    below_surface = np.clip((H[None, :, :] - Yc - 5.0) / 22.0, 0.0, 1.0)
    low_bias = np.clip((cfg.water_level + 18.0 - Yc) / 70.0, 0.0, 1.0)
    deep_bias = np.clip((target_y + 34.0 - Yc) / 42.0, 0.0, 1.0)

    width = (0.021 + 0.014 * low_bias + 0.008 * deep_bias) * intensity
    width = width * (0.46 + 0.72 * province_gate) * (0.50 + 0.70 * local_gate)

    tube_a = (np.abs(n1 - 0.5) < width) & (np.abs(n2 - 0.5) < width * 1.32)
    tube_b = (np.abs(n2 - 0.5) < width * 0.92) & (np.abs(n3 - 0.5) < width * 1.20)
    carve = (tube_a | tube_b) & (below_surface > 0.0) & inside[None, :, :]
    if not carve.any():
        return

    sl = block_ids[cave_lo - MIN_Y:cave_hi - MIN_Y + 1]
    solid = _solid_carve_mask(sl, blocks)
    sl[carve & solid] = blocks.air


def _carve_surface_cave_systems(cfg, blocks, block_ids, X, Z, H, inside, floor,
                                target_y, intensity):
    spacing = 148
    reach = 292
    chance = min(0.82, 0.44 * intensity)
    chunk_x0 = float(X.min())
    chunk_x1 = float(X.max())
    chunk_z0 = float(Z.min())
    chunk_z1 = float(Z.max())

    for cell_x, cell_z in _iter_feature_cells(X, Z, spacing, reach):
        if _rand01(cell_x, cell_z, cfg.seed, 7001) > chance:
            continue

        ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 7002, 10.0, spacing - 10.0)
        oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 7003, 10.0, spacing - 10.0)
        surf = _surface_height_at(cfg, blocks, ox, oz)

        # Avoid turning oceans/lakes into ugly underwater swiss cheese mouths,
        # but still allow some low shore caves.
        if surf <= cfg.water_level + 8 and _rand01(cell_x, cell_z, cfg.seed, 7004) > 0.10:
            continue

        start_y = min(surf - 4, cfg.water_level + 48)
        start_y = max(target_y + 34, start_y)
        if start_y <= target_y + 10:
            continue

        nodes = _build_cave_spine(cfg, cell_x, cell_z, ox, oz, start_y, target_y)
        min_node_x = min(n[0] for n in nodes)
        max_node_x = max(n[0] for n in nodes)
        min_node_z = min(n[2] for n in nodes)
        max_node_z = max(n[2] for n in nodes)
        if chunk_x1 < min_node_x - 44.0 or chunk_x0 > max_node_x + 44.0:
            continue
        if chunk_z1 < min_node_z - 44.0 or chunk_z0 > max_node_z + 44.0:
            continue

        base_r = _rand_range(cell_x, cell_z, cfg.seed, 7010, 2.55, 4.05) * (0.98 + 0.22 * intensity)

        for i in range(len(nodes) - 1):
            r_noise = _rand_range(cell_x, cell_z, cfg.seed, 7020 + i, 0.70, 1.42)
            r = base_r * r_noise
            if i == 0:
                r *= 1.42
            if i % 4 == 2:
                r *= 1.22
            _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor,
                                  nodes[i], nodes[i + 1], r, cell_x, cell_z,
                                  702000 + i * 37)

        for i, node in enumerate(nodes):
            room_roll = _rand01(cell_x, cell_z, cfg.seed, 7100 + i)
            mandatory = i in (0, len(nodes) - 1) or (i % 3 == 1)
            if not mandatory and room_roll > 0.52:
                continue
            scale = 1.0
            if i == 0:
                scale = 1.32
            elif i == len(nodes) - 1:
                scale = 1.45
            elif i % 3 == 1:
                scale = 1.20
            rx = _rand_range(cell_x, cell_z, cfg.seed, 7110 + i, 3.8, 8.4) * scale
            ry = _rand_range(cell_x, cell_z, cfg.seed, 7120 + i, 2.6, 5.8) * scale
            rz = _rand_range(cell_x, cell_z, cfg.seed, 7130 + i, 3.8, 8.4) * scale
            _carve_room(block_ids, blocks, X, Z, H, inside, floor, node, rx, ry, rz)
            if _rand01(cell_x, cell_z, cfg.seed, 7140 + i) < 0.46:
                _carve_lumpy_room(block_ids, blocks, X, Z, H, inside, floor, node,
                                  rx, ry, rz, cell_x, cell_z, 715000 + i * 29)

        branch_count = 4 + int(_rand01(cell_x, cell_z, cfg.seed, 7200) * 5.0 * min(1.65, intensity))
        for b in range(branch_count):
            if len(nodes) <= 4:
                break
            anchor_i = 1 + int(_rand01(cell_x, cell_z, cfg.seed, 7210 + b) * (len(nodes) - 3))
            anchor = nodes[anchor_i]
            branch = _build_cave_branch(cfg, cell_x, cell_z, b, anchor, target_y)
            for i in range(len(branch) - 1):
                br = base_r * _rand_range(cell_x, cell_z, cfg.seed, 7300 + b * 13 + i, 0.58, 1.18)
                _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor,
                                      branch[i], branch[i + 1], br, cell_x, cell_z,
                                      730000 + b * 101 + i * 17)
            end_node = branch[-1]
            _carve_lumpy_room(block_ids, blocks, X, Z, H, inside, floor, end_node,
                              base_r * _rand_range(cell_x, cell_z, cfg.seed, 7400 + b, 1.25, 2.50),
                              base_r * _rand_range(cell_x, cell_z, cfg.seed, 7410 + b, 0.95, 1.85),
                              base_r * _rand_range(cell_x, cell_z, cfg.seed, 7420 + b, 1.25, 2.50),
                              cell_x, cell_z, 742000 + b * 43)

        # Lower connectors imitate the way old caves suddenly merge and give
        # multiple path choices instead of one obvious descent.
        connector_count = 1 + int(_rand01(cell_x, cell_z, cfg.seed, 7500) * 3.0)
        for c in range(connector_count):
            a_i = max(2, int(_rand_range(cell_x, cell_z, cfg.seed, 7501 + c, 2.0, max(3.0, len(nodes) - 2.0))))
            a_i = min(len(nodes) - 2, a_i)
            a = nodes[a_i]
            ang = _rand_range(cell_x, cell_z, cfg.seed, 7510 + c, 0.0, np.pi * 2.0)
            dist = _rand_range(cell_x, cell_z, cfg.seed, 7520 + c, 44.0, 118.0)
            y = max(target_y + 2, a[1] - _rand_range(cell_x, cell_z, cfg.seed, 7530 + c, -4.0, 16.0))
            end = (a[0] + np.cos(ang) * dist, y, a[2] + np.sin(ang) * dist)
            mid = ((a[0] + end[0]) * 0.5 + np.cos(ang + np.pi * 0.5) * _rand_range(cell_x, cell_z, cfg.seed, 7540 + c, -24.0, 24.0),
                   (a[1] + end[1]) * 0.5 + _rand_range(cell_x, cell_z, cfg.seed, 7550 + c, -5.0, 7.0),
                   (a[2] + end[2]) * 0.5 + np.sin(ang + np.pi * 0.5) * _rand_range(cell_x, cell_z, cfg.seed, 7560 + c, -24.0, 24.0))
            _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor, a, mid, base_r * 0.88,
                                  cell_x, cell_z, 756000 + c * 61)
            _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor, mid, end, base_r * 0.84,
                                  cell_x, cell_z, 757000 + c * 61)
            _carve_lumpy_room(block_ids, blocks, X, Z, H, inside, floor, mid,
                              base_r * 1.9, base_r * 1.25, base_r * 1.9,
                              cell_x, cell_z, 758000 + c * 61)


def _carve_underground_cave_networks(cfg, blocks, block_ids, X, Z, H, inside,
                                     floor, target_y, intensity):
    # Dense cave clusters that may never touch the surface. These are what make
    # the underground feel like old Minecraft once you are down there: multiple
    # paths, many small-to-medium rooms, and varied tunnel scale.
    spacing = 104
    reach = 220
    chance = min(0.88, 0.52 * intensity)
    chunk_x0 = float(X.min())
    chunk_x1 = float(X.max())
    chunk_z0 = float(Z.min())
    chunk_z1 = float(Z.max())

    for cell_x, cell_z in _iter_feature_cells(X, Z, spacing, reach):
        if _rand01(cell_x, cell_z, cfg.seed, 8101) > chance:
            continue

        ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 8102, 6.0, spacing - 6.0)
        oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 8103, 6.0, spacing - 6.0)
        surf = _surface_height_at(cfg, blocks, ox, oz)

        roll = _rand01(cell_x, cell_z, cfg.seed, 8104)
        if roll < 0.58:
            lo = target_y + 3.0
            hi = min(float(cfg.water_level - 8), target_y + 42.0)
        elif roll < 0.86:
            lo = target_y + 18.0
            hi = min(float(cfg.water_level + 18), float(surf - 14))
        else:
            lo = max(target_y + 8.0, float(floor + 10))
            hi = min(float(cfg.water_level + 38), float(surf - 18))
        if hi <= lo + 3.0:
            continue
        oy = _rand_range(cell_x, cell_z, cfg.seed, 8105, lo, hi)
        center = (ox, oy, oz)

        max_extent = _rand_range(cell_x, cell_z, cfg.seed, 8106, 72.0, 138.0)
        if chunk_x1 < ox - max_extent or chunk_x0 > ox + max_extent:
            continue
        if chunk_z1 < oz - max_extent or chunk_z0 > oz + max_extent:
            continue

        base_r = _rand_range(cell_x, cell_z, cfg.seed, 8110, 2.05, 4.35) * (0.95 + 0.22 * intensity)
        if _rand01(cell_x, cell_z, cfg.seed, 8111) < 0.22:
            base_r *= _rand_range(cell_x, cell_z, cfg.seed, 8112, 1.35, 1.85)

        hub_rx = base_r * _rand_range(cell_x, cell_z, cfg.seed, 8120, 1.7, 3.4)
        hub_ry = base_r * _rand_range(cell_x, cell_z, cfg.seed, 8121, 1.0, 2.0)
        hub_rz = base_r * _rand_range(cell_x, cell_z, cfg.seed, 8122, 1.7, 3.4)
        _carve_lumpy_room(block_ids, blocks, X, Z, H, inside, floor, center,
                          hub_rx, hub_ry, hub_rz, cell_x, cell_z, 812000)

        arm_count = 3 + int(_rand01(cell_x, cell_z, cfg.seed, 8130) * 5.0)
        arm_ends = []
        for arm in range(arm_count):
            nodes = _build_underground_cave_arm(cfg, cell_x, cell_z, arm, center,
                                                floor, target_y)
            arm_ends.append(nodes[-1])
            for i in range(len(nodes) - 1):
                rr = base_r * _rand_range(cell_x, cell_z, cfg.seed, 8140 + arm * 19 + i, 0.52, 1.38)
                if i == 0:
                    rr *= 1.15
                _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor,
                                      nodes[i], nodes[i + 1], rr, cell_x, cell_z,
                                      814000 + arm * 211 + i * 31)

                if _rand01(cell_x, cell_z, cfg.seed, 8150 + arm * 19 + i) < 0.48:
                    n = nodes[i + 1]
                    _carve_lumpy_room(block_ids, blocks, X, Z, H, inside, floor, n,
                                      base_r * _rand_range(cell_x, cell_z, cfg.seed, 8160 + arm * 19 + i, 1.1, 2.7),
                                      base_r * _rand_range(cell_x, cell_z, cfg.seed, 8170 + arm * 19 + i, 0.7, 1.7),
                                      base_r * _rand_range(cell_x, cell_z, cfg.seed, 8180 + arm * 19 + i, 1.1, 2.7),
                                      cell_x, cell_z, 819000 + arm * 211 + i * 31)

            # Every arm gets a chance to split once more so it stops reading as
            # a single worm. These side branches are shorter and more chaotic.
            if len(nodes) >= 3 and _rand01(cell_x, cell_z, cfg.seed, 8200 + arm) < 0.74:
                anchor = nodes[1 + int(_rand01(cell_x, cell_z, cfg.seed, 8201 + arm) * (len(nodes) - 2))]
                side = _build_cave_branch(cfg, cell_x, cell_z, 100 + arm, anchor, target_y)
                for i in range(len(side) - 1):
                    _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor,
                                          side[i], side[i + 1], base_r * _rand_range(cell_x, cell_z, cfg.seed, 8210 + arm * 7 + i, 0.48, 1.05),
                                          cell_x, cell_z, 821000 + arm * 101 + i * 23)

        # Link a few arm ends together. This produces loop choices instead of a
        # pure tree of dead ends, which is the main thing the straight-tube pass
        # was missing.
        if len(arm_ends) >= 3:
            link_count = 1 + int(_rand01(cell_x, cell_z, cfg.seed, 8300) * 3.0)
            for link in range(link_count):
                a_i = int(_rand01(cell_x, cell_z, cfg.seed, 8301 + link) * len(arm_ends))
                b_i = int(_rand01(cell_x, cell_z, cfg.seed, 8311 + link) * len(arm_ends))
                if a_i == b_i:
                    b_i = (b_i + 1) % len(arm_ends)
                a = arm_ends[a_i]
                b = arm_ends[b_i]
                mid = ((a[0] + b[0]) * 0.5 + _rand_range(cell_x, cell_z, cfg.seed, 8320 + link, -18.0, 18.0),
                       (a[1] + b[1]) * 0.5 + _rand_range(cell_x, cell_z, cfg.seed, 8330 + link, -4.0, 6.0),
                       (a[2] + b[2]) * 0.5 + _rand_range(cell_x, cell_z, cfg.seed, 8340 + link, -18.0, 18.0))
                _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor, a, mid,
                                      base_r * 0.68, cell_x, cell_z, 835000 + link * 59)
                _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor, mid, b,
                                      base_r * 0.64, cell_x, cell_z, 836000 + link * 59)


def _carve_shallow_surface_mouths(cfg, blocks, block_ids, X, Z, H, inside, floor,
                                  target_y, intensity):
    # Cheap local mouth pass. This gives the surface more visible cave entrances
    # without spawning a full expensive world-spanning feature system for every
    # mouth. Most of these short descents connect into the noise/underground cave
    # layer beneath them.
    chunk_x = int(np.floor(float(X.min()) / 16.0))
    chunk_z = int(np.floor(float(Z.min()) / 16.0))
    count = int(_rand01(chunk_x, chunk_z, cfg.seed, 8801) < min(0.42, 0.25 * intensity))
    if _rand01(chunk_x, chunk_z, cfg.seed, 8802) < min(0.12, 0.06 * intensity):
        count += 1

    for i in range(count):
        ix = 2 + int(_rand01(chunk_x, chunk_z, cfg.seed, 8810 + i) * 12.0)
        iz = 2 + int(_rand01(chunk_x, chunk_z, cfg.seed, 8820 + i) * 12.0)
        if not inside[iz, ix]:
            continue

        surf = int(H[iz, ix])
        if surf <= cfg.water_level + 2:
            continue

        x = float(X[iz, ix])
        z = float(Z[iz, ix])
        start = (x, float(surf + 1), z)
        angle = _rand_range(chunk_x, chunk_z, cfg.seed, 8830 + i, 0.0, np.pi * 2.0)
        length = _rand_range(chunk_x, chunk_z, cfg.seed, 8840 + i, 18.0, 38.0)
        end_y = max(float(target_y + 18), float(surf - _rand_range(chunk_x, chunk_z, cfg.seed, 8850 + i, 16.0, 36.0)))
        mid = (x + np.cos(angle) * length * 0.45, (surf + end_y) * 0.5,
               z + np.sin(angle) * length * 0.45)
        end = (x + np.cos(angle) * length, end_y, z + np.sin(angle) * length)
        radius = _rand_range(chunk_x, chunk_z, cfg.seed, 8860 + i, 2.6, 4.4)

        _carve_lumpy_room(block_ids, blocks, X, Z, H, inside, floor, start,
                          radius * 1.5, radius * 1.05, radius * 1.5,
                          chunk_x, chunk_z, 887000 + i)
        _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor, start, mid,
                              radius * 0.92, chunk_x, chunk_z, 888000 + i)
        _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor, mid, end,
                              radius * 0.78, chunk_x, chunk_z, 889000 + i)

def _build_cave_spine(cfg, cell_x, cell_z, ox, oz, start_y, target_y):
    segments = 7 + int(_rand01(cell_x, cell_z, cfg.seed, 7800) * 4.0)
    angle = _rand_range(cell_x, cell_z, cfg.seed, 7801, 0.0, np.pi * 2.0)
    turn_bias = _rand_range(cell_x, cell_z, cfg.seed, 7802, -0.18, 0.18)
    x = ox
    z = oz
    nodes = [(x, float(start_y), z)]

    for i in range(1, segments + 1):
        t = i / segments
        turn = _rand_range(cell_x, cell_z, cfg.seed, 7810 + i, -0.72, 0.72)
        turn += np.sin(i * 1.37 + _rand_range(cell_x, cell_z, cfg.seed, 7820, 0.0, np.pi * 2.0)) * 0.16
        angle += turn + turn_bias
        step = _rand_range(cell_x, cell_z, cfg.seed, 7830 + i, 18.0, 32.0)
        if i % 4 == 0:
            step *= _rand_range(cell_x, cell_z, cfg.seed, 7840 + i, 0.55, 0.85)
        x += np.cos(angle) * step
        z += np.sin(angle) * step

        descent = (1.0 - t) ** 1.24
        y = target_y + (start_y - target_y) * descent
        y += np.sin(t * np.pi * 4.0 + _rand_range(cell_x, cell_z, cfg.seed, 7850, 0.0, np.pi * 2.0)) * (3.6 * (1.0 - t))
        y += _rand_range(cell_x, cell_z, cfg.seed, 7860 + i, -2.4, 2.8) * (0.35 + 0.65 * (1.0 - t))
        y = max(target_y, min(start_y, y))
        if i == segments:
            y = target_y
        nodes.append((x, float(y), z))

    return nodes


def _build_cave_branch(cfg, cell_x, cell_z, branch_i, anchor, target_y):
    x, y, z = anchor
    angle = _rand_range(cell_x, cell_z, cfg.seed, 7900 + branch_i, 0.0, np.pi * 2.0)
    segs = 2 + int(_rand01(cell_x, cell_z, cfg.seed, 7910 + branch_i) * 3.0)
    nodes = [anchor]
    for i in range(1, segs + 1):
        angle += _rand_range(cell_x, cell_z, cfg.seed, 7920 + branch_i * 7 + i, -0.95, 0.95)
        step = _rand_range(cell_x, cell_z, cfg.seed, 7930 + branch_i * 7 + i, 12.0, 25.0)
        x += np.cos(angle) * step
        z += np.sin(angle) * step
        # Branches usually drift downward but may plateau briefly, matching
        # vanilla-ish cave wandering without making the guaranteed route fail.
        y -= _rand_range(cell_x, cell_z, cfg.seed, 7940 + branch_i * 7 + i, -1.5, 5.8)
        y = max(target_y + 2.0, y)
        nodes.append((x, float(y), z))
    return nodes


def _build_underground_cave_arm(cfg, cell_x, cell_z, arm_i, center, floor, target_y):
    x, y, z = center
    angle = _rand_range(cell_x, cell_z, cfg.seed, 8400 + arm_i, 0.0, np.pi * 2.0)
    pitch = _rand_range(cell_x, cell_z, cfg.seed, 8401 + arm_i, -0.42, 0.32)
    segs = 3 + int(_rand01(cell_x, cell_z, cfg.seed, 8402 + arm_i) * 5.0)
    nodes = [center]
    for i in range(1, segs + 1):
        angle += _rand_range(cell_x, cell_z, cfg.seed, 8410 + arm_i * 17 + i, -1.18, 1.18)
        pitch += _rand_range(cell_x, cell_z, cfg.seed, 8420 + arm_i * 17 + i, -0.30, 0.30)
        pitch = float(np.clip(pitch, -0.62, 0.42))
        step = _rand_range(cell_x, cell_z, cfg.seed, 8430 + arm_i * 17 + i, 11.0, 28.0)
        if _rand01(cell_x, cell_z, cfg.seed, 8435 + arm_i * 17 + i) < 0.22:
            step *= _rand_range(cell_x, cell_z, cfg.seed, 8436 + arm_i * 17 + i, 1.35, 1.95)
        x += np.cos(angle) * step
        z += np.sin(angle) * step
        y += np.sin(pitch) * step * 0.55

        # Underground branches can rise/fall naturally, but do not shoot above
        # normal cave range or below the floor.
        y += _rand_range(cell_x, cell_z, cfg.seed, 8440 + arm_i * 17 + i, -4.5, 4.0)
        y = max(float(floor + 6), min(float(cfg.water_level + 36), y))
        if i == segs and _rand01(cell_x, cell_z, cfg.seed, 8450 + arm_i) < 0.34:
            y = max(float(target_y + 1), y - _rand_range(cell_x, cell_z, cfg.seed, 8451 + arm_i, 8.0, 24.0))
        nodes.append((x, float(y), z))
    return nodes


def _carve_varied_segment(block_ids, blocks, X, Z, H, inside, floor, p0, p1,
                          radius, cell_x, cell_z, salt):
    radius = max(2.20, float(radius))
    _carve_capsule(block_ids, blocks, X, Z, H, inside, floor, p0, p1, radius)

    x0, y0, z0 = p0
    x1, y1, z1 = p1
    vx = x1 - x0
    vy = y1 - y0
    vz = z1 - z0
    length = float(np.sqrt(vx * vx + vy * vy + vz * vz))
    if length <= 1.0:
        return

    seed = _segment_seed(salt)
    bumps = max(1, min(7, int(length / 15.0)))
    for j in range(bumps):
        roll = _rand01(cell_x, cell_z, seed, 8500 + j)
        if roll > 0.68:
            continue
        t = (j + _rand_range(cell_x, cell_z, seed, 8510 + j, 0.25, 0.85)) / bumps
        t = max(0.0, min(1.0, t))
        cx = x0 + vx * t
        cy = y0 + vy * t
        cz = z0 + vz * t

        side_a = _rand_range(cell_x, cell_z, seed, 8520 + j, 0.0, np.pi * 2.0)
        side_d = radius * _rand_range(cell_x, cell_z, seed, 8530 + j, 0.15, 1.65)
        cx += np.cos(side_a) * side_d
        cz += np.sin(side_a) * side_d
        cy += _rand_range(cell_x, cell_z, seed, 8540 + j, -1.7, 1.9)

        rx = radius * _rand_range(cell_x, cell_z, seed, 8550 + j, 0.70, 1.95)
        ry = radius * _rand_range(cell_x, cell_z, seed, 8560 + j, 0.55, 1.38)
        rz = radius * _rand_range(cell_x, cell_z, seed, 8570 + j, 0.70, 1.95)
        _carve_room(block_ids, blocks, X, Z, H, inside, floor, (cx, cy, cz), rx, ry, rz)

        # Tiny alcove/dead-end nubs create the noisy wall choices players expect
        # from older cave systems without making enormous caverns everywhere.
        if _rand01(cell_x, cell_z, seed, 8580 + j) < 0.26:
            end = (cx + np.cos(side_a) * radius * _rand_range(cell_x, cell_z, seed, 8590 + j, 2.5, 6.5),
                   cy + _rand_range(cell_x, cell_z, seed, 8600 + j, -1.5, 2.2),
                   cz + np.sin(side_a) * radius * _rand_range(cell_x, cell_z, seed, 8610 + j, 2.5, 6.5))
            _carve_capsule(block_ids, blocks, X, Z, H, inside, floor, (cx, cy, cz), end,
                           radius * _rand_range(cell_x, cell_z, seed, 8620 + j, 0.38, 0.78))


def _segment_seed(salt):
    return int((int(salt) * 2654435761) & 0x7FFFFFFF)


def _carve_lumpy_room(block_ids, blocks, X, Z, H, inside, floor, center,
                      rx, ry, rz, cell_x, cell_z, salt):
    _carve_room(block_ids, blocks, X, Z, H, inside, floor, center, rx, ry, rz)
    seed = _segment_seed(salt)
    cx, cy, cz = center
    lumps = 2 + int(_rand01(cell_x, cell_z, seed, 8700) * 5.0)
    for i in range(lumps):
        if _rand01(cell_x, cell_z, seed, 8710 + i) < 0.18:
            continue
        a = _rand_range(cell_x, cell_z, seed, 8720 + i, 0.0, np.pi * 2.0)
        d = _rand_range(cell_x, cell_z, seed, 8730 + i, 0.25, 0.82)
        ox = cx + np.cos(a) * rx * d
        oz = cz + np.sin(a) * rz * d
        oy = cy + _rand_range(cell_x, cell_z, seed, 8740 + i, -0.45, 0.52) * ry
        _carve_room(block_ids, blocks, X, Z, H, inside, floor, (ox, oy, oz),
                    rx * _rand_range(cell_x, cell_z, seed, 8750 + i, 0.34, 0.72),
                    ry * _rand_range(cell_x, cell_z, seed, 8760 + i, 0.30, 0.68),
                    rz * _rand_range(cell_x, cell_z, seed, 8770 + i, 0.34, 0.72))


def _carve_capsule(block_ids, blocks, X, Z, H, inside, floor, p0, p1, radius):
    x0, y0, z0 = p0
    x1, y1, z1 = p1
    vx = x1 - x0
    vy = y1 - y0
    vz = z1 - z0
    denom = vx * vx + vy * vy + vz * vz
    if denom <= 0.001:
        return

    pad = radius + 2.0
    if float(X.max()) < min(x0, x1) - pad or float(X.min()) > max(x0, x1) + pad:
        return
    if float(Z.max()) < min(z0, z1) - pad or float(Z.min()) > max(z0, z1) + pad:
        return

    y_min = max(floor + 2, int(np.floor(min(y0, y1) - radius - 2)))
    y_max = min(MIN_Y + WORLD_HEIGHT - 2, int(np.ceil(max(y0, y1) + radius + 2)))
    if y_max <= y_min:
        return

    ys = np.arange(y_min, y_max + 1, dtype=np.float64)
    X3 = X[None, :, :]
    Z3 = Z[None, :, :]
    Y3 = ys.reshape(-1, 1, 1)
    t = ((X3 - x0) * vx + (Y3 - y0) * vy + (Z3 - z0) * vz) / denom
    t = np.clip(t, 0.0, 1.0)
    cx = x0 + vx * t
    cy = y0 + vy * t
    cz = z0 + vz * t

    # Taller than wide so cave tubes do not turn into flat head-crushing slits.
    # Slightly more vertical bias keeps good caves from becoming skinny worms.
    dist = ((X3 - cx) ** 2 + ((Y3 - cy) / 1.78) ** 2 + (Z3 - cz) ** 2)
    surface_cap = H[None, :, :] + 1
    mask = (dist <= radius * radius) & inside[None, :, :] & (Y3 > floor + 1) & (Y3 <= surface_cap)
    if not mask.any():
        return
    sl = block_ids[y_min - MIN_Y:y_max - MIN_Y + 1]
    solid = _solid_carve_mask(sl, blocks)
    sl[mask & solid] = blocks.air


def _carve_room(block_ids, blocks, X, Z, H, inside, floor, center, rx, ry, rz):
    rx = max(2.10, float(rx))
    ry = max(1.95, float(ry))
    rz = max(2.10, float(rz))
    cx, cy, cz = center
    pad = max(rx, rz) + 2.0
    if float(X.max()) < cx - pad or float(X.min()) > cx + pad:
        return
    if float(Z.max()) < cz - pad or float(Z.min()) > cz + pad:
        return

    y_min = max(floor + 2, int(np.floor(cy - ry - 2)))
    y_max = min(MIN_Y + WORLD_HEIGHT - 2, int(np.ceil(cy + ry + 2)))
    if y_max <= y_min:
        return

    ys = np.arange(y_min, y_max + 1, dtype=np.float64)
    X3 = X[None, :, :]
    Z3 = Z[None, :, :]
    Y3 = ys.reshape(-1, 1, 1)
    d = ((X3 - cx) / max(0.1, rx)) ** 2 + ((Y3 - cy) / max(0.1, ry)) ** 2 + ((Z3 - cz) / max(0.1, rz)) ** 2
    surface_cap = H[None, :, :] + 1
    mask = (d <= 1.0) & inside[None, :, :] & (Y3 > floor + 1) & (Y3 <= surface_cap)
    if not mask.any():
        return
    sl = block_ids[y_min - MIN_Y:y_max - MIN_Y + 1]
    solid = _solid_carve_mask(sl, blocks)
    sl[mask & solid] = blocks.air


def _carve_ravines(cfg, blocks, block_ids, X, Z, H, inside, on_wall,
                   world_y, floor, is_water, is_beach):
    # Old implementation used an infinite noise contour, which can generate a
    # world-spanning laser-cut canyon. This is finite segment carving with
    # tapered ends, sloped walls, and sparse lava only in tiny bottom pools.
    spacing = 330
    reach = 260
    chance = 0.048
    solid = _solid_carve_mask(block_ids, blocks)

    protected_water = is_water | is_beach | (H <= cfg.water_level + 7)
    water_buffer = _soft_mask(protected_water, passes=5)

    for cell_x, cell_z in _iter_feature_cells(X, Z, spacing, reach):
        if _rand01(cell_x, cell_z, cfg.seed, 8001) > chance:
            continue

        ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 8002, 45.0, spacing - 45.0)
        oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 8003, 45.0, spacing - 45.0)
        angle = _rand_range(cell_x, cell_z, cfg.seed, 8004, 0.0, np.pi * 2.0)
        length = _rand_range(cell_x, cell_z, cfg.seed, 8005, 82.0, 185.0)
        width = _rand_range(cell_x, cell_z, cfg.seed, 8006, 3.5, 7.0)
        depth = _rand_range(cell_x, cell_z, cfg.seed, 8007, 18.0, 44.0)
        wiggle = _rand_range(cell_x, cell_z, cfg.seed, 8008, 2.0, 11.0)
        phase = _rand_range(cell_x, cell_z, cfg.seed, 8009, 0.0, np.pi * 2.0)

        dx = np.cos(angle)
        dz = np.sin(angle)
        px = -dz
        pz = dx
        half = length * 0.5
        rx = X - ox
        rz = Z - oz
        along = rx * dx + rz * dz
        t = np.clip((along + half) / max(1.0, length), 0.0, 1.0)
        taper = np.sin(np.pi * t)
        along_clamped = t * length - half
        lateral_center = np.sin(t * np.pi * 1.65 + phase) * wiggle * taper
        lateral = rx * px + rz * pz - lateral_center
        dist = np.abs(lateral)
        radius = width * (0.18 + 0.82 * np.sqrt(np.clip(taper, 0.0, 1.0)))
        col = (dist < radius) & inside & ~on_wall & (taper > 0.04) & (water_buffer < 0.18)
        if not col.any():
            continue

        bottom = np.maximum(floor + 4, (H - depth * (0.68 + 0.32 * taper)).astype(np.int32))
        height = np.maximum(1.0, (H - bottom).astype(np.float64))
        rel_depth = np.clip((H[None, :, :] - world_y) / height[None, :, :], 0.0, 1.0)
        side_radius = radius[None, :, :] * (1.08 - 0.70 * rel_depth)
        side_radius = np.maximum(side_radius, radius[None, :, :] * 0.22)
        carve = (col[None, :, :] & (world_y >= bottom[None, :, :]) &
                 (world_y <= H[None, :, :]) &
                 (dist[None, :, :] <= side_radius))
        block_ids[carve & solid] = blocks.air

        if _rand01(cell_x, cell_z, cfg.seed, 8010) < 0.38:
            lava_col = col & (dist < radius * 0.25) & (bottom <= floor + 9)
            lava = lava_col[None, :, :] & (world_y >= bottom[None, :, :]) & (world_y <= bottom[None, :, :] + 1)
            block_ids[lava & (block_ids == blocks.air)] = blocks.lava

def _place_fluids(cfg, blocks, block_ids, X, Z, inside, world_y, floor, rng):
    # Low lava belongs in deep cave bottoms, but avoid filling every carved
    # floor as one giant flat sheet. No underground water pocket squares: those
    # looked like debug blocks when viewed through caves.
    lava_level = floor + 4
    below = np.empty_like(block_ids)
    below[1:] = block_ids[:-1]
    below[0] = blocks.bedrock
    inside3 = inside[None, :, :]
    lava_noise = noise.fbm2d(X + 141, Z - 313, cfg.seed + 9101, octaves=2, freq=1 / 36.0)
    lava_cols = lava_noise > 0.55
    lava = (inside3 & lava_cols[None, :, :] & (world_y <= lava_level) &
            (block_ids == blocks.air) & (below != blocks.air) &
            (below != blocks.water))
    block_ids[lava] = blocks.lava

def _settle_liquid_flow(cfg, blocks, block_ids, inside, floor):
    # Static liquid relaxation for generated worlds. Minecraft will normally
    # tick fluids after chunk load, but generated region files are viewed before
    # that update wave has happened. This pass precomputes the obvious flow:
    # water spreads a few blocks over solid river/cave floors and falls down
    # open cliffs; lava does the same with a much shorter, slower spread.
    inside3 = inside[None, :, :]

    if np.any((block_ids == blocks.water) & inside3):
        # Surface rivers/ponds can now live at a local per-column water surface
        # instead of one global sea level. Do not let that water free-fall into
        # every ravine it touches; it should only settle sideways on nearby solid
        # floor blocks inside this static region-file pass.
        _settle_one_liquid(block_ids, blocks, inside, blocks.water,
                           lateral_steps=2,
                           lo_y=floor + 1,
                           hi_y=MIN_Y + WORLD_HEIGHT - 2,
                           can_fall_to_y=floor + 1,
                           allow_fall=False)

    if np.any((block_ids == blocks.lava) & inside3):
        _settle_one_liquid(block_ids, blocks, inside, blocks.lava,
                           lateral_steps=3,
                           lo_y=floor + 1,
                           hi_y=floor + 18,
                           can_fall_to_y=floor + 1,
                           allow_fall=True)


def _settle_one_liquid(block_ids, blocks, inside, liquid_id, lateral_steps,
                       lo_y, hi_y, can_fall_to_y, allow_fall=True):
    lo = max(0, int(can_fall_to_y) - MIN_Y)
    hi = min(WORLD_HEIGHT - 1, int(hi_y) - MIN_Y)
    spread_lo = max(0, int(lo_y) - MIN_Y)
    spread_hi = hi
    if hi <= lo:
        return

    for _ in range(max(1, int(lateral_steps))):
        # First let lava columns fall straight down through air. Surface water is
        # intentionally not free-fallen here because finite rivers/ponds use
        # local heights; otherwise one exposed cave edge turns into a giant
        # vertical water trench.
        if allow_fall:
            _fall_liquid_columns(block_ids, blocks, inside, liquid_id, lo, hi)

        liquid = block_ids == liquid_id
        below_solid = _below_solid_mask(block_ids, blocks)
        floor_liquid = liquid & below_solid
        floor_liquid[:spread_lo] = False
        floor_liquid[spread_hi + 1:] = False

        spread = np.zeros_like(liquid, dtype=bool)
        spread[:, 1:, :] |= floor_liquid[:, :-1, :]
        spread[:, :-1, :] |= floor_liquid[:, 1:, :]
        spread[:, :, 1:] |= floor_liquid[:, :, :-1]
        spread[:, :, :-1] |= floor_liquid[:, :, 1:]

        target = (spread & (block_ids == blocks.air) & inside[None, :, :])
        target[:lo] = False
        target[hi + 1:] = False
        if not target.any():
            break
        block_ids[target] = liquid_id

    if allow_fall:
        _fall_liquid_columns(block_ids, blocks, inside, liquid_id, lo, hi)


def _fall_liquid_columns(block_ids, blocks, inside, liquid_id, lo, hi):
    # Scan top-down while carrying a per-column falling state. This avoids doing
    # one global shift per vertical block of fall distance, which would make a
    # water source over a tall ravine need 80+ whole-array iterations.
    falling = np.zeros((16, 16), dtype=bool)
    for yi in range(hi, lo - 1, -1):
        cell = block_ids[yi]
        liquid_here = (cell == liquid_id) & inside
        air_here = (cell == blocks.air) & inside

        fill = falling & air_here
        if fill.any():
            cell[fill] = liquid_id

        solid_here = _liquid_solid_surface(cell, blocks)
        falling = ((falling | liquid_here | fill) & ~solid_here) | liquid_here


def _below_solid_mask(block_ids, blocks):
    solid = np.zeros_like(block_ids, dtype=bool)
    solid[1:] = _liquid_solid_surface(block_ids[:-1], blocks)
    solid[0] = True
    return solid


def _liquid_solid_surface(arr, blocks):
    # Liquids should spread sideways from actual floors/banks, not from falling
    # columns. Water/lava below is not support here, otherwise a waterfall would
    # fan out at every Y-level and flood the whole exposed cave wall.
    return ((arr != blocks.air) & (arr != blocks.water) & (arr != blocks.lava) &
            (arr != blocks.short_grass) & (arr != blocks.fern) &
            (arr != blocks.tall_grass_lo) & (arr != blocks.tall_grass_hi) &
            (arr != blocks.dead_bush) & (arr != blocks.cactus) &
            (arr != blocks.sugar_cane) &
            (arr != blocks.vine_n) & (arr != blocks.vine_s) &
            (arr != blocks.vine_e) & (arr != blocks.vine_w))

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


def _deep_dark_ore_name(name):
    short = name.split(":", 1)[-1]
    if short.startswith("deepslate_") or not short.endswith("_ore"):
        return name
    return f"minecraft:deepslate_{short}" if ":" in name else f"deepslate_{short}"


def _place_deep_dark_ores(cfg, blocks, block_ids, H, inside, rng):
    # The deep dark is now the same cave geometry as the regular cavern layer,
    # only with a deeper material palette. Mirror the normal ore pass into that
    # lower palette after sculk/deepslate conversion so ores cannot be wiped by
    # the deep-dark skinning step. This intentionally ignores each ore's vanilla
    # y-window: the custom deep-dark extension is extra playable space and should
    # contain the same ore mix the cavern layer above has.
    deep_lo = MIN_Y + 3
    deep_hi = min(int(cfg.floor_y + 8), WORLD_HEIGHT + MIN_Y - 2)
    if deep_hi < deep_lo:
        return

    replaceable = (blocks.deepslate, blocks.tuff, blocks.cobbled_deepslate,
                   blocks.stone, blocks.andesite, blocks.diorite, blocks.granite)
    replaceable_arr = np.array(replaceable, dtype=block_ids.dtype)
    rock = np.isin(block_ids, replaceable_arr)
    air = block_ids == blocks.air

    near_air = np.zeros_like(rock, dtype=bool)
    near_air[1:] |= air[:-1]
    near_air[:-1] |= air[1:]
    near_air[:, 1:, :] |= air[:, :-1, :]
    near_air[:, :-1, :] |= air[:, 1:, :]
    near_air[:, :, 1:] |= air[:, :, :-1]
    near_air[:, :, :-1] |= air[:, :, 1:]

    y_world = (np.arange(WORLD_HEIGHT) + MIN_Y)[:, None, None]
    deep_zone = (y_world >= deep_lo) & (y_world <= deep_hi)
    inside3 = inside[None, :, :]
    exposed = rock & near_air & deep_zone & inside3
    exposed_coords = np.argwhere(exposed)

    for ore in cfg.ores:
        oid = blocks.ore(_deep_dark_ore_name(ore.name))
        veins = int(rng.poisson(max(0.0, ore.veins_per_chunk * 0.95)))
        for _ in range(veins):
            if exposed_coords.size > 0 and rng.random() < 0.72:
                iy, oz, ox = exposed_coords[int(rng.integers(0, len(exposed_coords)))]
                oy = int(iy + MIN_Y)
            else:
                ox, oz = int(rng.integers(0, 16)), int(rng.integers(0, 16))
                if not inside[oz, ox]:
                    continue
                oy = _sample_y(rng, deep_lo, deep_hi, ore.bias)
            _place_blob(block_ids, int(ox), int(oy), int(oz), ore.vein_size, oid, rng, inside,
                        replaceable)


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------

_TREE_KIND = {
    "minecraft:forest": "oak", "minecraft:plains": "oak",
    "minecraft:birch_forest": "birch",
    "minecraft:dark_forest": "dark_oak",
    "minecraft:jungle": "jungle", "minecraft:taiga": "spruce",
    "minecraft:snowy_taiga": "spruce",
}
_TREE_DENSITY = {
    "minecraft:forest": 0.030, "minecraft:plains": 0.004,
    "minecraft:birch_forest": 0.032, "minecraft:dark_forest": 0.042,
    "minecraft:jungle": 0.058, "minecraft:taiga": 0.027,
    "minecraft:snowy_taiga": 0.020,
}


def _place_trees(cfg, blocks, block_ids, H, biome_str, is_water, inside, on_wall, rng):
    for iz in range(1, 15):
        for ix in range(1, 15):
            if not inside[iz, ix] or on_wall[iz, ix] or is_water[iz, ix]:
                continue
            b = biome_str[iz, ix]
            kind = _TREE_KIND.get(b)
            if kind is None:
                continue
            if rng.random() > _TREE_DENSITY.get(b, 0.0) * cfg.tree_density:
                continue
            surf = int(H[iz, ix])
            base = surf - MIN_Y + 1
            if base <= 0 or base >= WORLD_HEIGHT - 4:
                continue
            if block_ids[base - 1, iz, ix] not in (
                    blocks.grass, blocks.grass_snow, blocks.dirt, blocks.podzol):
                continue

            if kind == "jungle" and 5 <= ix <= 10 and 5 <= iz <= 10 and rng.random() < 0.36:
                if _place_giant_jungle_tree(block_ids, blocks, iz, ix, base, rng):
                    continue

            if kind == "dark_oak" and 3 <= ix <= 12 and 3 <= iz <= 12 and rng.random() < 0.54:
                if _place_dark_oak_tree(block_ids, blocks, iz, ix, base, rng):
                    continue

            _place_normal_tree(block_ids, blocks, iz, ix, base, kind, rng)


def _place_jungle_vines_around_log(block_ids, blocks, iz, ix, y, rng, chance=1.0):
    # Vines are placed on the air block beside jungle logs, attached back to the
    # log face. Giant jungle trunks call this for every exposed trunk side.
    if y < 0 or y >= WORLD_HEIGHT:
        return
    specs = (
        (-1, 0, blocks.vine_s),
        (1, 0, blocks.vine_n),
        (0, -1, blocks.vine_e),
        (0, 1, blocks.vine_w),
    )
    for dz, dx, vine_id in specs:
        lz = iz + dz
        lx = ix + dx
        if 0 <= lz < 16 and 0 <= lx < 16 and block_ids[y, lz, lx] == blocks.air:
            if rng.random() <= chance:
                block_ids[y, lz, lx] = vine_id


def _place_normal_tree(block_ids, blocks, iz, ix, base, kind, rng):
    trunk_h = int(rng.integers(4, 7))
    if kind == "jungle":
        trunk_h += 3
    elif kind == "birch":
        trunk_h += 1
    elif kind == "spruce":
        trunk_h += 2

    log, leaf = blocks.log(kind), blocks.leaves(kind)
    top_y = base + trunk_h
    if top_y + 3 >= WORLD_HEIGHT:
        return False

    if kind == "spruce":
        for ly in range(top_y - 4, top_y + 2):
            rad = max(1, 3 - abs(top_y - 2 - ly))
            if ly >= top_y:
                rad = 1
            _leaf_disc(block_ids, leaf, iz, ix, ly, rad, corner_chance=0.55)
    else:
        for ly in range(top_y - 2, top_y + 2):
            rad = 2 if ly < top_y else 1
            _leaf_disc(block_ids, leaf, iz, ix, ly, rad, corner_chance=0.70)

    for k in range(trunk_h):
        if base + k < WORLD_HEIGHT:
            y = base + k
            block_ids[y, iz, ix] = log
            if kind == "jungle":
                _place_jungle_vines_around_log(block_ids, blocks, iz, ix, y, rng, 0.92)
    return True


def _place_giant_jungle_tree(block_ids, blocks, iz, ix, base, rng):
    kind = "jungle"
    log, leaf = blocks.log(kind), blocks.leaves(kind)
    trunk_h = int(rng.integers(18, 24))
    top_y = base + trunk_h
    if top_y + 5 >= WORLD_HEIGHT:
        return False

    for dz in (0, 1):
        for dx in (0, 1):
            if block_ids[base - 1, iz + dz, ix + dx] == 0:
                return False

    for k in range(trunk_h):
        y = base + k
        for dz in (0, 1):
            for dx in (0, 1):
                block_ids[y, iz + dz, ix + dx] = log
        _place_jungle_vines_around_log(block_ids, blocks, iz, ix, y, rng, 1.0)
        _place_jungle_vines_around_log(block_ids, blocks, iz + 1, ix, y, rng, 1.0)
        _place_jungle_vines_around_log(block_ids, blocks, iz, ix + 1, y, rng, 1.0)
        _place_jungle_vines_around_log(block_ids, blocks, iz + 1, ix + 1, y, rng, 1.0)

    center_z = iz
    center_x = ix
    for ly in range(top_y - 5, top_y + 4):
        dy = ly - top_y
        if dy <= -4:
            rad = 3
        elif dy <= -2:
            rad = 5
        elif dy <= 0:
            rad = 4
        else:
            rad = 2
        _leaf_disc(block_ids, leaf, center_z, center_x, ly, rad, corner_chance=0.42)

    # Jungle giants have side fans/branches, not just a pole with a blob.
    branch_count = int(rng.integers(4, 7))
    for b in range(branch_count):
        ang = (b / max(1, branch_count)) * np.pi * 2.0 + rng.random() * 0.65
        by = base + int(rng.integers(max(7, trunk_h // 2), trunk_h - 2))
        length = int(rng.integers(4, 8))
        x = float(center_x)
        z = float(center_z)
        for step in range(1, length + 1):
            x += np.cos(ang)
            z += np.sin(ang)
            y = by + step // 3
            lx = int(round(x))
            lz = int(round(z))
            if 1 <= lx < 15 and 1 <= lz < 15 and 0 <= y < WORLD_HEIGHT:
                block_ids[y, lz, lx] = log
        lx = int(round(x))
        lz = int(round(z))
        if 2 <= lx < 14 and 2 <= lz < 14:
            _leaf_disc(block_ids, leaf, lz, lx, min(WORLD_HEIGHT - 2, by + length // 3), 3, corner_chance=0.36)
            _leaf_disc(block_ids, leaf, lz, lx, min(WORLD_HEIGHT - 2, by + length // 3 + 1), 2, corner_chance=0.55)
    return True


def _place_dark_oak_tree(block_ids, blocks, iz, ix, base, rng):
    log, leaf = blocks.log("dark_oak"), blocks.leaves("dark_oak")
    trunk_h = int(rng.integers(6, 9))
    top_y = base + trunk_h
    if top_y + 3 >= WORLD_HEIGHT:
        return False
    for k in range(trunk_h):
        y = base + k
        for dz in (0, 1):
            for dx in (0, 1):
                block_ids[y, iz + dz, ix + dx] = log
    for ly in range(top_y - 3, top_y + 3):
        rad = 3 if ly <= top_y else 2
        _leaf_disc(block_ids, leaf, iz, ix, ly, rad, corner_chance=0.38)
    return True


def _leaf_disc(block_ids, leaf, iz, ix, ly, rad, corner_chance=0.65):
    if ly < 0 or ly >= WORLD_HEIGHT:
        return
    for dz in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            if abs(dz) == rad and abs(dx) == rad and _mix32((ix + dx) * 31 + (iz + dz) * 131 + ly * 17) / 4294967296.0 < corner_chance:
                continue
            if dx * dx + dz * dz > (rad + 0.35) * (rad + 0.35):
                continue
            lz, lx = iz + dz, ix + dx
            if 0 <= lz < 16 and 0 <= lx < 16:
                if block_ids[ly, lz, lx] == 0:
                    block_ids[ly, lz, lx] = leaf


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
                          blocks.podzol, blocks.sand, blocks.red_sand):
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

            if is_desert[iz, ix] and ground in (blocks.sand, blocks.red_sand):
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

def _build_level_dat(cfg, spawn_y, spawn_x=0, spawn_z=0, border_size=None):
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

    Data["SpawnX"] = nbt.Int(int(spawn_x))
    Data["SpawnY"] = nbt.Int(int(spawn_y))
    Data["SpawnZ"] = nbt.Int(int(spawn_z))
    Data["SpawnAngle"] = nbt.Float(0.0)
    Data["BorderCenterX"] = nbt.Double(0.0)
    Data["BorderCenterZ"] = nbt.Double(0.0)
    Data["BorderSize"] = nbt.Double(float(border_size if border_size is not None else cfg.size + 32))

    dp = nbt.Compound()
    dp["Enabled"] = nbt.List(nbt.String.tag_id, [nbt.String("vanilla")])
    dp["Disabled"] = nbt.List(0, [])
    Data["DataPacks"] = dp

    void_world = str(getattr(cfg, "generator", "vanilla")).strip().lower() == "fuhc"

    flat_settings = nbt.Compound()
    flat_settings["biome"] = nbt.String("minecraft:the_void" if void_world else "minecraft:plains")
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
# Biome auto balancing
# ---------------------------------------------------------------------------

def _quantile_or(values, q, fallback):
    arr = np.asarray(values)
    if arr.size == 0:
        return fallback
    return float(np.quantile(arr, q))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _tune_biomes(cfg, xmin, xmax):
    if cfg.biome_tuned:
        return cfg

    # Sample the whole finite world before workers start. This does not make
    # tiny noisy patches; it only picks climate thresholds so every seed gets a
    # healthy mix of snow/cold/temperate/hot/dry biomes. Workers still use the
    # continuous climate fields, so biome masses stay coherent.
    n = 176 if cfg.size >= 768 else 112
    xs = np.linspace(xmin + 8, xmax - 8, n, dtype=np.float64)
    zs = np.linspace(xmin + 8, xmax - 8, n, dtype=np.float64)
    X, Z = np.meshgrid(xs, zs)
    Hf, mnt = _terrain_height_fields(cfg, X, Z)
    temp, hum, selector, edge_wobble = _base_climate_fields(cfg, X, Z, Hf)
    temp, hum, selector, edge_wobble = _smooth_climate_grid(temp, hum, selector, edge_wobble)

    land = Hf > cfg.water_level - 4
    if not land.any():
        land = np.ones(X.shape, dtype=bool)
    temp_land = temp[land]
    hum_land = hum[land]

    frozen_cut = _quantile_or(temp_land, 0.070, cfg.biome_frozen_cut)
    cold_cut = _quantile_or(temp_land, 0.255, cfg.biome_cold_cut)
    warm_cut = _quantile_or(temp_land, 0.700, cfg.biome_warm_cut)

    cfg.biome_frozen_cut = _clamp(frozen_cut, 0.10, 0.36)
    cfg.biome_cold_cut = _clamp(max(cold_cut, cfg.biome_frozen_cut + 0.105), 0.24, 0.53)
    cfg.biome_warm_cut = _clamp(max(warm_cut, cfg.biome_cold_cut + 0.20), 0.54, 0.88)

    warm = land & (temp >= cfg.biome_warm_cut)
    warm_frac = max(0.01, float(np.count_nonzero(warm)) / float(np.count_nonzero(land)))
    desert_target = 0.165
    desert_ratio = _clamp(desert_target / warm_frac, 0.34, 0.74)
    cfg.biome_desert_hum_cut = _clamp(_quantile_or(hum[warm], desert_ratio, cfg.biome_desert_hum_cut), 0.24, 0.58)

    temperate = land & (temp >= cfg.biome_cold_cut) & (temp < cfg.biome_warm_cut)
    cfg.biome_forest_hum_cut = _clamp(_quantile_or(hum[temperate], 0.58, cfg.biome_forest_hum_cut), 0.46, 0.76)
    cfg.biome_jungle_hum_cut = _clamp(_quantile_or(hum[warm], 0.82, cfg.biome_jungle_hum_cut), 0.66, 0.93)

    # One light feedback pass accounts for selector gates and snowy peaks.
    for _ in range(2):
        biome, _, _, _ = _classify_biomes(cfg, X, Z, Hf, mnt, temp, hum, selector, edge_wobble)
        land_count = max(1, int(np.count_nonzero(land)))
        snow = float(np.count_nonzero(land & ((biome == "minecraft:snowy_plains") |
                                             (biome == "minecraft:snowy_taiga") |
                                             (biome == "minecraft:snowy_slopes")))) / land_count
        desert = float(np.count_nonzero(land & ((biome == "minecraft:desert") |
                                               (biome == "minecraft:badlands")))) / land_count
        forestish = float(np.count_nonzero(land & ((biome == "minecraft:forest") |
                                                  (biome == "minecraft:birch_forest") |
                                                  (biome == "minecraft:dark_forest") |
                                                  (biome == "minecraft:taiga") |
                                                  (biome == "minecraft:snowy_taiga") |
                                                  (biome == "minecraft:jungle")))) / land_count
        plains = float(np.count_nonzero(land & (biome == "minecraft:plains"))) / land_count

        # Keep snow/desert present but not seed-dominating. The signs here are
        # important: raising frozen_cut makes more snow, raising desert_hum_cut
        # makes more desert, lowering forest_hum_cut makes more forest.
        cfg.biome_frozen_cut = _clamp(cfg.biome_frozen_cut + (0.065 - snow) * 0.42, 0.04, cfg.biome_cold_cut - 0.08)
        cfg.biome_desert_hum_cut = _clamp(cfg.biome_desert_hum_cut + (0.165 - desert) * 0.68, 0.22, 0.70)
        cfg.biome_warm_cut = _clamp(cfg.biome_warm_cut - (0.165 - desert) * 0.34,
                                    cfg.biome_cold_cut + 0.18, 0.88)

        if forestish > 0.42:
            cfg.biome_forest_hum_cut = _clamp(cfg.biome_forest_hum_cut + 0.035, 0.46, 0.80)
        elif plains > 0.46 and forestish < 0.25:
            cfg.biome_forest_hum_cut = _clamp(cfg.biome_forest_hum_cut - 0.035, 0.42, 0.78)

    cfg.biome_tuned = True
    return cfg

# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------

def _generate_region_worker(args):
    (job_index, cfg, ow_xmin, ow_xmax, preview_xmin, preview_zmin,
     preview_w, preview_h, pscale, region_dir, rx, rz, cxmin, cxmax,
     czmin, czmax, progress_queue, progress_interval, send_preview_progress) = args

    blocks, biome_reg = _prepare_palettes(cfg)
    block_palette = blocks.palette()
    biome_palette = list(biome_reg.list)
    rw = RegionWriter(rx, rz)

    patch_x, patch_z, patch_w, patch_h = _preview_patch_rect(
        preview_xmin, preview_zmin, preview_w, preview_h, pscale,
        cxmin, cxmax, czmin, czmax)
    chunk_areas = _chunk_areas(cfg)
    n_bounds = nether_bounds(cfg) if cfg.nether else None
    preview_patch = np.zeros((patch_h, patch_w, 3), dtype=np.uint8)

    chunks = 0
    last_reported_chunks = 0
    pending_patches = []
    for cz in range(czmin, czmax + 1):
        for cx in range(cxmin, cxmax + 1):
            if not _chunk_in_any_area(cx, cz, chunk_areas):
                continue
            rng_seed = _chunk_rng_seed(cfg.seed, cx, cz)
            if n_bounds is not None:
                nxmin, nxmax, nzmin, nzmax = n_bounds
                if cx * 16 <= nxmax and cx * 16 + 15 >= nxmin and \
                        cz * 16 <= nzmax and cz * 16 + 15 >= nzmin:
                    block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_nether_chunk(
                        cfg, blocks, biome_reg, cx, cz, n_bounds, rng_seed)
                else:
                    block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_chunk(
                        cfg, blocks, biome_reg, cx, cz, ow_xmin, ow_xmax, rng_seed)
            else:
                block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_chunk(
                    cfg, blocks, biome_reg, cx, cz, ow_xmin, ow_xmax, rng_seed)

            # Some generation passes still register optional block states lazily
            # (for example custom ore names or rare decorations). Use the current
            # palette after chunk generation so local block IDs are always backed
            # by a block-state tag.
            block_palette = blocks.palette()
            root = build_chunk(cx, cz, block_ids, biome_ids, block_palette,
                               biome_palette, cfg.data_version, MIN_SECTION, N_SECTIONS,
                               block_entities=block_entities)
            rw.add_chunk(cx, cz, root)

            chunk_patch = _chunk_preview_patch(cfg, cx, cz, preview_xmin, preview_zmin,
                                               pscale, preview_w, preview_h,
                                               H, biome_str, inside, is_water)
            if chunk_patch is not None:
                _apply_preview_patch(preview_patch, (chunk_patch[0] - patch_x,
                                                     chunk_patch[1] - patch_z,
                                                     chunk_patch[2]))
                if send_preview_progress:
                    pending_patches.append(chunk_patch)

            chunks += 1
            if progress_queue is not None and chunks - last_reported_chunks >= progress_interval:
                progress_queue.put((job_index, chunks, pending_patches))
                pending_patches = []
                last_reported_chunks = chunks

    if progress_queue is not None and (chunks != last_reported_chunks or pending_patches):
        progress_queue.put((job_index, chunks, pending_patches))

    filename = rw.filename
    if region_dir is not None:
        with open(os.path.join(region_dir, filename), "wb") as fpo:
            fpo.write(rw.to_bytes())
        region_bytes = None
    else:
        region_bytes = rw.to_bytes()

    return {
        "kind": "region",
        "job_index": job_index,
        "rx": rx,
        "rz": rz,
        "filename": filename,
        "region": region_bytes,
        "patch_x": patch_x,
        "patch_z": patch_z,
        "patch": preview_patch,
        "chunks": chunks,
    }


def _generate_tile_worker(args):
    (job_index, cfg, ow_xmin, ow_xmax, preview_xmin, preview_zmin,
     preview_w, preview_h, pscale, cxmin, cxmax, czmin, czmax,
     progress_queue, progress_interval, send_preview_progress) = args

    blocks, biome_reg = _prepare_palettes(cfg)
    block_palette = blocks.palette()
    biome_palette = list(biome_reg.list)

    compressed_chunks = []
    preview_patches = []
    pending_patches = []
    chunks = 0
    chunk_areas = _chunk_areas(cfg)
    n_bounds = nether_bounds(cfg) if cfg.nether else None
    last_reported_chunks = 0

    for cz in range(czmin, czmax + 1):
        for cx in range(cxmin, cxmax + 1):
            if not _chunk_in_any_area(cx, cz, chunk_areas):
                continue
            rng_seed = _chunk_rng_seed(cfg.seed, cx, cz)
            if n_bounds is not None:
                nxmin, nxmax, nzmin, nzmax = n_bounds
                if cx * 16 <= nxmax and cx * 16 + 15 >= nxmin and \
                        cz * 16 <= nzmax and cz * 16 + 15 >= nzmin:
                    block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_nether_chunk(
                        cfg, blocks, biome_reg, cx, cz, n_bounds, rng_seed)
                else:
                    block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_chunk(
                        cfg, blocks, biome_reg, cx, cz, ow_xmin, ow_xmax, rng_seed)
            else:
                block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_chunk(
                    cfg, blocks, biome_reg, cx, cz, ow_xmin, ow_xmax, rng_seed)

            # Some generation passes still register optional block states lazily
            # (for example custom ore names or rare decorations). Use the current
            # palette after chunk generation so local block IDs are always backed
            # by a block-state tag.
            block_palette = blocks.palette()
            root = build_chunk(cx, cz, block_ids, biome_ids, block_palette,
                               biome_palette, cfg.data_version, MIN_SECTION, N_SECTIONS,
                               block_entities=block_entities)
            compressed_chunks.append((cx, cz, nbt.to_zlib("", root)))

            chunk_patch = _chunk_preview_patch(cfg, cx, cz, preview_xmin, preview_zmin,
                                               pscale, preview_w, preview_h,
                                               H, biome_str, inside, is_water)
            if chunk_patch is not None:
                preview_patches.append(chunk_patch)
                if send_preview_progress:
                    pending_patches.append(chunk_patch)

            chunks += 1
            if progress_queue is not None and chunks - last_reported_chunks >= progress_interval:
                progress_queue.put((job_index, chunks, pending_patches))
                pending_patches = []
                last_reported_chunks = chunks

    if progress_queue is not None and (chunks != last_reported_chunks or pending_patches):
        progress_queue.put((job_index, chunks, pending_patches))

    return {
        "kind": "tile",
        "job_index": job_index,
        "compressed_chunks": compressed_chunks,
        "preview_patches": preview_patches,
        "chunks": chunks,
    }


def generate_world(cfg: WorldConfig, out_dir: str,
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

    ow_xmin, ow_xmax, ow_zmin, ow_zmax = overworld_bounds(cfg)
    preview_xmin, preview_xmax, preview_zmin, preview_zmax = generation_bounds(cfg)
    cfg = _tune_biomes(cfg, ow_xmin, ow_xmax)

    # Plan biome centers + structures up front so the parallel workers can stamp
    # any structure that overlaps their chunk (structures cross chunk borders).
    report(0.0, "Mapping biomes & planning structures...")
    biome_midpoints = compute_biome_midpoints(cfg)
    layer_info = underworld.layer_starts(cfg)
    cfg.structure_plan = structures.plan_structures(cfg, biome_midpoints, layer_info)

    total_chunks = sum(1 for _ in _generation_chunks(cfg))

    region_jobs = list(_region_jobs_for_areas(cfg))
    requested_workers = os.cpu_count() or 1 if workers is None or int(workers) <= 0 else int(workers)
    use_tile_jobs = total_chunks >= 256 and len(region_jobs) < requested_workers
    tile_jobs = list(_tile_jobs_for_areas(cfg)) if use_tile_jobs else []
    active_jobs = tile_jobs if use_tile_jobs else region_jobs
    worker_count = _resolve_worker_count(workers, len(region_jobs), total_chunks,
                                         len(active_jobs))

    preview_w_blocks = preview_xmax - preview_xmin + 1
    preview_h_blocks = preview_zmax - preview_zmin + 1
    pscale = max(1, max(preview_w_blocks, preview_h_blocks) // 768)
    preview_w = (preview_w_blocks + pscale - 1) // pscale
    preview_h = (preview_h_blocks + pscale - 1) // pscale
    preview = np.zeros((preview_h, preview_w, 3), dtype=np.uint8)
    region_writers = {}

    def make_jobs(progress_queue=None, progress_interval=8):
        if use_tile_jobs:
            return [
                (job_index, cfg, ow_xmin, ow_xmax, preview_xmin, preview_zmin,
                 preview_w_blocks, preview_h_blocks, pscale, jcxmin, jcxmax,
                 jczmin, jczmax, progress_queue, progress_interval,
                 preview_progress is not None)
                for job_index, (jcxmin, jcxmax, jczmin, jczmax)
                in enumerate(tile_jobs)
            ]
        return [
            (job_index, cfg, ow_xmin, ow_xmax, preview_xmin, preview_zmin,
             preview_w_blocks, preview_h_blocks, pscale, region_dir, rx, rz,
             jcxmin, jcxmax, jczmin, jczmax, progress_queue, progress_interval,
             preview_progress is not None)
            for job_index, (rx, rz, jcxmin, jcxmax, jczmin, jczmax)
            in enumerate(region_jobs)
        ]

    worker_func = _generate_tile_worker if use_tile_jobs else _generate_region_worker
    progress_counts = [0] * len(active_jobs)
    done_chunks = 0
    last_preview_report = 0.0

    def publish_preview(force=False):
        nonlocal last_preview_report
        if preview_progress is None:
            return
        now = time.time()
        if force or now - last_preview_report >= 0.50:
            preview_progress(preview)
            last_preview_report = now

    def apply_patch_list(patches):
        if not patches:
            return False
        for patch in patches:
            _apply_preview_patch(preview, patch)
        return True

    def consume_result(result):
        if result["kind"] == "tile":
            for cx, cz, payload in result["compressed_chunks"]:
                key = (cx >> 5, cz >> 5)
                rw = region_writers.get(key)
                if rw is None:
                    rw = RegionWriter(key[0], key[1])
                    region_writers[key] = rw
                rw.add_compressed_chunk(cx, cz, payload)
            apply_patch_list(result.get("preview_patches"))
            return

        _write_region_result(region_dir, result)
        _apply_preview_patch(preview, (result["patch_x"], result["patch_z"], result["patch"]))

    def update_progress(job_index, chunks, patches):
        nonlocal done_chunks
        changed = apply_patch_list(patches)
        updated = False
        if 0 <= job_index < len(progress_counts) and chunks > progress_counts[job_index]:
            progress_counts[job_index] = chunks
            updated = True
        if updated:
            done_chunks = sum(progress_counts)
            report(0.05 + 0.83 * done_chunks / total_chunks,
                   f"Generated {done_chunks}/{total_chunks} chunks")
        if changed:
            publish_preview()

    if worker_count == 1:
        report(0.0, "Starting generation...")
        for job in make_jobs():
            result = worker_func(job)
            consume_result(result)
            progress_counts[result["job_index"]] = result["chunks"]
            done_chunks = sum(progress_counts)
            report(0.05 + 0.83 * done_chunks / total_chunks,
                   f"Generated {done_chunks}/{total_chunks} chunks")
            publish_preview()
    else:
        mode_name = "tile" if use_tile_jobs else "region"
        report(0.0, f"Starting {mode_name} generation on {worker_count} workers...")
        mp_context = mp.get_context("spawn") if threading.current_thread() is not threading.main_thread() else mp.get_context()
        if preview_progress is not None:
            progress_interval = max(1, min(4, total_chunks // max(1, worker_count * 256)))
        else:
            progress_interval = 16

        def drain_progress(progress_queue):
            while True:
                try:
                    job_index, chunks, patches = progress_queue.get_nowait()
                except queue.Empty:
                    break
                update_progress(job_index, chunks, patches)

        with mp_context.Manager() as manager:
            progress_queue = manager.Queue()
            with ProcessPoolExecutor(max_workers=worker_count, mp_context=mp_context) as pool:
                futures = [pool.submit(worker_func, job)
                           for job in make_jobs(progress_queue, progress_interval)]
                pending = set(futures)
                while pending:
                    drain_progress(progress_queue)
                    done_now = [fut for fut in pending if fut.done()]
                    if not done_now:
                        try:
                            job_index, chunks, patches = progress_queue.get(timeout=0.25)
                            update_progress(job_index, chunks, patches)
                        except queue.Empty:
                            pass
                        continue

                    for fut in done_now:
                        pending.remove(fut)
                        result = fut.result()
                        consume_result(result)
                        if result["chunks"] > progress_counts[result["job_index"]]:
                            progress_counts[result["job_index"]] = result["chunks"]
                            done_chunks = sum(progress_counts)
                            report(0.05 + 0.83 * done_chunks / total_chunks,
                                   f"Generated {done_chunks}/{total_chunks} chunks")
                        publish_preview()
                drain_progress(progress_queue)

    if region_writers:
        report(0.90, "Writing region files...")
        for key in sorted(region_writers):
            rw = region_writers[key]
            with open(os.path.join(region_dir, rw.filename), "wb") as fpo:
                fpo.write(rw.to_bytes())

    report(0.92, "Finalizing preview...")
    publish_preview(force=True)

    blocks, _ = _prepare_palettes(cfg)
    Xc, Zc = np.array([[0.0]]), np.array([[0.0]])
    spawn_y = int(_fields(cfg, blocks, Xc, Zc)["H"][0, 0]) + 1
    report(0.96, "Writing level.dat...")
    with open(os.path.join(out_dir, "level.dat"), "wb") as fpo:
        fpo.write(_build_level_dat(cfg, spawn_y))
    with open(os.path.join(out_dir, "session.lock"), "wb") as fpo:
        fpo.write(b"\xe2\x98\x83")

    report(1.0, f"Done in {time.time()-t0:.1f}s")
    publish_preview(force=True)
    return {
        "chunks": total_chunks, "regions": len(region_jobs), "workers": worker_count,
        "job_mode": "tile" if use_tile_jobs else "region",
        "spawn": (0, spawn_y, 0), "bounds": (ow_xmin, ow_xmax),
        "nether_bounds": nether_bounds(cfg) if cfg.nether else None,
        "seconds": round(time.time() - t0, 1), "preview": preview,
        "biome_midpoints": biome_midpoints,
        "structures": cfg.structure_plan or [],
        "layer_starts": layer_info,
    }


def compute_biome_midpoints(cfg, max_cells=96):
    """Coarse-sample the world and return the midpoint of every contiguous biome
    region (overworld + nether). Multiple regions of the same biome are listed
    separately. Returns [{'biome', 'x', 'y', 'z', 'blocks'}]."""
    blocks, _ = _prepare_palettes(cfg)
    xmin = -(cfg.size // 2)
    xmax = xmin + cfg.size - 1
    n = int(min(max_cells, max(8, cfg.size // 16)))
    xs = np.linspace(xmin + 0.5, xmax - 0.5, n)
    zs = np.linspace(xmin + 0.5, xmax - 0.5, n)
    X, Z = np.meshgrid(xs, zs)                      # [z, x]
    step = (xmax - xmin) / max(1, n - 1)
    cell_area = step * step
    min_cells = 3

    out = []
    f = _fields(cfg, blocks, X, Z)
    out += _region_centroids(f["biome"], X, Z, f["H"], step, cell_area, min_cells)

    if cfg.nether:
        nxmin, nxmax, nzmin, nzmax = nether_bounds(cfg)
        n2 = int(min(max_cells, max(8, NETHER_BOX_SIZE // 16)))
        xs2 = np.linspace(nxmin + 0.5, nxmax - 0.5, n2)
        zs2 = np.linspace(nzmin + 0.5, nzmax - 0.5, n2)
        X2, Z2 = np.meshgrid(xs2, zs2)
        step2 = (nxmax - nxmin) / max(1, n2 - 1)
        cell_area2 = step2 * step2
        nbm = underworld.nether_biome_map(cfg, X2, Z2)
        names = np.array(underworld.NETHER_BIOMES)
        neth_names = names[nbm]
        Hn = np.full(X2.shape, (MIN_Y + NETHER_BOX_CEILING_Y) // 2)
        out += _region_centroids(neth_names, X2, Z2, Hn, step2, cell_area2, min_cells)

    return out


def _region_centroids(biome, X, Z, H, step, cell_area, min_cells):
    """Flood-fill contiguous equal-biome cells; emit one midpoint per region."""
    n_z, n_x = biome.shape
    seen = np.zeros((n_z, n_x), dtype=bool)
    results = []
    for z0 in range(n_z):
        for x0 in range(n_x):
            if seen[z0, x0]:
                continue
            target = biome[z0, x0]
            stack = [(z0, x0)]
            seen[z0, x0] = True
            cells = []
            while stack:
                z, x = stack.pop()
                cells.append((z, x))
                for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nz, nx = z + dz, x + dx
                    if 0 <= nz < n_z and 0 <= nx < n_x and not seen[nz, nx] \
                            and biome[nz, nx] == target:
                        seen[nz, nx] = True
                        stack.append((nz, nx))
            if len(cells) < min_cells:
                continue
            czs = [c[0] for c in cells]
            cxs = [c[1] for c in cells]
            mx = int(round(float(np.mean([X[z, x] for z, x in cells]))))
            mz = int(round(float(np.mean([Z[z, x] for z, x in cells]))))
            my = int(round(float(np.mean([H[z, x] for z, x in cells]))))
            results.append({
                "biome": str(target),
                "x": mx, "y": my, "z": mz,
                "blocks": int(round(len(cells) * cell_area)),
            })
    return results


def _write_region_result(region_dir, result):
    if result.get("region") is None:
        return
    with open(os.path.join(region_dir, result["filename"]), "wb") as fpo:
        fpo.write(result["region"])


def _clamp_u8(v):
    return max(0, min(255, int(v)))


def _map_preview_color(cfg, H, biome_str, is_water, iz, ix):
    if is_water[iz, ix]:
        base = MAP_BASE_COLORS["minecraft:river"]
        depth = cfg.water_level - int(H[iz, ix])
        if depth >= 8:
            shade = 0
        elif depth >= 4:
            shade = 1
        else:
            shade = 2
    else:
        base = MAP_BASE_COLORS.get(biome_str[iz, ix], MAP_BASE_COLORS["minecraft:plains"])
        prev_h = int(H[iz - 1, ix]) if iz > 0 else int(H[iz, ix])
        delta = int(H[iz, ix]) - prev_h
        if delta > 1:
            shade = 3
        elif delta < -1:
            shade = 1
        else:
            shade = 2

    f = MAP_SHADE[shade]
    return (_clamp_u8(base[0] * f), _clamp_u8(base[1] * f), _clamp_u8(base[2] * f))


def _chunk_preview_patch(cfg, cx, cz, preview_xmin, preview_zmin, pscale,
                         preview_w_blocks, preview_h_blocks, H, biome_str,
                         inside, is_water):
    preview_w = (preview_w_blocks + pscale - 1) // pscale
    preview_h = (preview_h_blocks + pscale - 1) // pscale
    px0 = preview_w
    pz0 = preview_h
    px1 = -1
    pz1 = -1
    points = []

    for iz in range(16):
        wz = cz * 16 + iz
        pz = (wz - preview_zmin) // pscale
        if pz < 0 or pz >= preview_h:
            continue
        for ix in range(16):
            if not inside[iz, ix]:
                continue
            wx = cx * 16 + ix
            px = (wx - preview_xmin) // pscale
            if px < 0 or px >= preview_w:
                continue
            col = _map_preview_color(cfg, H, biome_str, is_water, iz, ix)
            points.append((px, pz, col))
            if px < px0:
                px0 = px
            if px > px1:
                px1 = px
            if pz < pz0:
                pz0 = pz
            if pz > pz1:
                pz1 = pz

    if not points:
        return None

    patch = np.zeros((pz1 - pz0 + 1, px1 - px0 + 1, 3), dtype=np.uint8)
    for px, pz, col in points:
        patch[pz - pz0, px - px0] = col
    return px0, pz0, patch


def _apply_preview_patch(preview, patch_info):
    if patch_info is None:
        return
    px, pz, patch = patch_info
    sub = preview[pz:pz + patch.shape[0], px:px + patch.shape[1]]
    mask = patch.any(axis=2)
    sub[mask] = patch[mask]


def _fill_preview_patch(preview, cfg, cx, cz, preview_xmin, preview_zmin,
                        pscale, patch_x, patch_z, H, biome_str, inside, is_water):
    patch_info = _chunk_preview_patch(cfg, cx, cz, preview_xmin, preview_zmin,
                                      pscale, preview.shape[1] * pscale,
                                      preview.shape[0] * pscale,
                                      H, biome_str, inside, is_water)
    if patch_info is None:
        return
    px, pz, patch = patch_info
    local_info = (px - patch_x, pz - patch_z, patch)
    _apply_preview_patch(preview, local_info)


def _fill_preview(preview, cfg, cx, cz, preview_xmin, preview_zmin, pscale,
                  H, biome_str, inside, is_water):
    patch_info = _chunk_preview_patch(cfg, cx, cz, preview_xmin, preview_zmin,
                                      pscale, preview.shape[1] * pscale,
                                      preview.shape[0] * pscale,
                                      H, biome_str, inside, is_water)
    _apply_preview_patch(preview, patch_info)
