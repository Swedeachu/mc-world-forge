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
import underworld
import structures
from region import RegionWriter, build_chunk

# Fixed vertical layout (standard overworld dimension range).
MIN_Y = -64
WORLD_HEIGHT = 384          # -64 .. 319
MIN_SECTION = MIN_Y // 16   # -4
N_SECTIONS = WORLD_HEIGHT // 16  # 24


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
        OreCfg("coal_ore", 5, 120, 11.0, 12, "high"),
        OreCfg("iron_ore", -19, 80, 9.0, 8, "mid"),
        OreCfg("copper_ore", 0, 96, 6.0, 8, "mid"),
        OreCfg("gold_ore", -19, 32, 3.0, 6, "low"),
        OreCfg("redstone_ore", -19, 15, 7.0, 7, "low"),
        OreCfg("lapis_ore", -19, 32, 2.5, 6, "mid"),
        OreCfg("diamond_ore", -19, 8, 5.6, 5, "low"),
    ]


@dataclass
class WorldConfig:
    name: str = "Forged World"
    size: int = 1000
    seed: int = 0
    data_version: int = 3465          # 1.20.1
    version_name: str = "1.20.1"

    floor_y: int = -20                # overworld bottom; underworld/void below
    water_level: int = 130
    base_height: int = 140            # raised so the underworld layers get room below
    hill_amp: float = 12.0
    mountain_amp: float = 48.0
    mountain_amount: float = 0.5       # 0..1, higher => more/higher mountains
    extreme_mountain_amount: float = 0.35  # sparse local mountain ranges
    snowline_y: int = 172

    caves: bool = True
    cave_intensity: float = 1.0
    ravines: bool = True
    fluids: bool = True                # underground lava/water pockets

    rivers: bool = True
    river_amount: float = 0.4          # 0..1
    lakes: bool = True
    lake_amount: float = 0.4           # 0..1

    # Internal, auto-tuned per generated world so one seed does not become
    # 70% forest/plains or 50% snow. These are filled by _tune_biomes().
    biome_frozen_cut: float = 0.20
    biome_cold_cut: float = 0.36
    biome_warm_cut: float = 0.70
    biome_desert_hum_cut: float = 0.42
    biome_forest_hum_cut: float = 0.62
    biome_jungle_hum_cut: float = 0.82
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

    # Underworld layers stacked below the overworld floor (cave straight down).
    deep_dark: bool = False           # deepslate/tuff/sculk band below the caverns
    deep_dark_height: int = 22
    nether: bool = False              # netherrack "hell" band at the very bottom
    nether_height: int = 40
    structures: bool = False          # scatter recognizable structures
    height_mode: str = "compact"      # "compact" (std range) | "tall" (datapack)
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




def _smoothstep01(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _temperature_height(cfg, Hf):
    return np.clip((Hf - cfg.water_level) / 360.0, 0.0, 0.26)


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
    temp_detail = (noise.fbm2d(CX + 333, CZ - 777, s + 31, octaves=3, freq=1 / 210.0) - 0.5) * 0.15
    edge_wobble = (noise.fbm2d(X + 7077, Z - 1212, s + 74, octaves=3, freq=1 / 135.0) - 0.5)
    temp = 0.5 + wave_temp + (tw - 0.5) * 0.78 + temp_detail + edge_wobble * 0.055
    temp -= _temperature_height(cfg, Hf)

    mw = noise.fbm2d(CX - 2000, CZ + 2000, s + 4, octaves=5, freq=1 / (size_scale * 0.82))
    hum_wave = (np.sin((rz * 0.95 - rx * 0.28) / size_scale * np.pi * 2.05 + phase_b * 0.7) * 0.16)
    hum_detail = (noise.fbm2d(CX - 444, CZ + 666, s + 41, octaves=3, freq=1 / 195.0) - 0.5) * 0.16
    hum = 0.5 + (mw - 0.5) * 1.10 + hum_wave + hum_detail - edge_wobble * 0.045

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
    temp_band = np.clip(temp + edge_wobble * 0.045, 0.0, 1.0)
    hum_band = np.clip(hum + (noise.fbm2d(X - 818, Z + 181, cfg.seed + 75, octaves=2, freq=1 / 145.0) - 0.5) * 0.065, 0.0, 1.0)
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
    dry_hot = warm & (hum_band < cfg.biome_desert_hum_cut)
    biome[dry_hot] = "minecraft:desert"
    mesa = dry_hot & (badlands_strength > 0.36)
    biome[mesa] = "minecraft:badlands"
    biome[warm & (hum_band >= max(cfg.biome_forest_hum_cut, 0.58)) & (selector > 0.62)] = "minecraft:forest"
    biome[warm & (hum_band >= cfg.biome_jungle_hum_cut)] = "minecraft:jungle"

    is_arid = (biome == "minecraft:desert") | (biome == "minecraft:badlands")
    high = mnt > 0.55
    snowy_peak = high & ((Hf >= cfg.snowline_y + 15) | (temp < cfg.biome_cold_cut - 0.08)) & ~is_arid
    biome[snowy_peak] = "minecraft:snowy_slopes"
    return biome, temp_band, hum_band, badlands_strength

def _build_river_nodes(cfg, cell_x, cell_z, spacing):
    ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5102, 32.0, spacing - 32.0)
    oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 5103, 32.0, spacing - 32.0)
    angle = _rand_range(cell_x, cell_z, cfg.seed, 5104, 0.0, np.pi * 2.0)
    bias = _rand_range(cell_x, cell_z, cfg.seed, 5105, -0.17, 0.17)
    segs = 6 + int(_rand01(cell_x, cell_z, cfg.seed, 5106) * 7.0)
    nodes = [(ox, oz)]
    x = ox
    z = oz
    for i in range(1, segs + 1):
        turn = _rand_range(cell_x, cell_z, cfg.seed, 5110 + i, -0.64, 0.64)
        turn += np.sin(i * 1.81 + _rand_range(cell_x, cell_z, cfg.seed, 5111, 0.0, np.pi * 2.0)) * 0.18
        angle += turn + bias
        step = _rand_range(cell_x, cell_z, cfg.seed, 5120 + i, 34.0, 66.0)
        if i % 5 == 0:
            step *= 0.72
        x += np.cos(angle) * step
        z += np.sin(angle) * step
        nodes.append((x, z))
    return nodes


def _finite_river_field(cfg, X, Z, mountain_mask, temp=None, hum=None):
    if not cfg.rivers or cfg.river_amount <= 0.0:
        zeros = np.zeros(X.shape, dtype=np.float64)
        return zeros, zeros

    # Cheap vanilla-layer style rivers: a heavily domain-warped contour layer,
    # gated by a broad basin layer so they form coherent meanders instead of
    # every zero-contour becoming a world-spanning stripe. This is much closer
    # to old Minecraft's river-biome feel than the previous finite spline pass.
    s = cfg.seed
    amount = np.clip(cfg.river_amount, 0.0, 1.0)
    warp_scale = 360.0
    warp_x = (noise.fbm2d(X + 14321, Z - 9182, s + 5201, octaves=3, freq=1 / warp_scale) - 0.5) * 135.0
    warp_z = (noise.fbm2d(X - 2421, Z + 11017, s + 5202, octaves=3, freq=1 / warp_scale) - 0.5) * 135.0
    warp_x += (noise.fbm2d(X + 991, Z + 222, s + 5203, octaves=2, freq=1 / 145.0) - 0.5) * 32.0
    warp_z += (noise.fbm2d(X - 133, Z - 771, s + 5204, octaves=2, freq=1 / 145.0) - 0.5) * 32.0
    RX = X + warp_x
    RZ = Z + warp_z

    river_noise = noise.fbm2d(RX, RZ, s + 5210, octaves=5, freq=1 / 250.0)
    center_dist = np.abs(river_noise - 0.5) * 2.0

    basin = noise.fbm2d(RX + 3111, RZ - 1999, s + 5220, octaves=3, freq=1 / 760.0)
    gate = _smoothstep01((basin - (0.35 - amount * 0.08)) / 0.19)

    # Breaker gate avoids one unbroken line crossing the whole map, but it is
    # broad/soft so the river does not look chopped into tiny blue worms.
    breaker = noise.fbm2d(RX - 8080, RZ + 4040, s + 5230, octaves=2, freq=1 / 420.0)
    breaker_gate = _smoothstep01((breaker - 0.28) / 0.24)

    width = 0.030 + amount * 0.021
    core = _smoothstep01((width - center_dist) / max(width, 0.0001))
    valley = _smoothstep01((width * 3.4 - center_dist) / max(width * 3.4, 0.0001))

    # Low-frequency wobble fattens/thins banks naturally instead of a constant
    # blue rail. The channel remains continuous inside the valley.
    width_wobble = 0.78 + 0.42 * noise.fbm2d(RX + 71, RZ - 91, s + 5240, octaves=2, freq=1 / 88.0)
    core = np.clip(core * width_wobble, 0.0, 1.0)
    valley = np.clip(valley * (0.80 + 0.30 * width_wobble), 0.0, 1.0)

    core *= gate * breaker_gate
    valley *= gate * (0.55 + 0.45 * breaker_gate)

    if temp is not None:
        cold_suppression = np.where(temp < cfg.biome_frozen_cut + 0.03, 0.28, 1.0)
        core *= cold_suppression
        valley *= np.maximum(cold_suppression, 0.68)
    if hum is not None:
        dry_suppression = np.where(hum < 0.20, 0.72, 1.0)
        core *= dry_suppression
        valley *= np.maximum(dry_suppression, 0.80)

    mountain = mountain_mask > 0.56
    core[mountain] *= 0.15
    valley[mountain] *= 0.42
    return np.clip(core, 0.0, 1.0), np.clip(valley, 0.0, 1.0)

def _coord_grids(cx, cz):
    xs = cx * 16 + np.arange(16)
    zs = cz * 16 + np.arange(16)
    X, Z = np.meshgrid(xs, zs)   # [z, x]
    return X.astype(np.float64), Z.astype(np.float64)


def _terrain_height_fields(cfg, X, Z):
    s = cfg.seed
    hills = (noise.fbm2d(X, Z, s + 1, octaves=4, freq=1 / 72.0) - 0.5) * 2.0
    detail = (noise.fbm2d(X, Z, s + 11, octaves=3, freq=1 / 28.0) - 0.5) * 2.0
    mnt_mask = noise.fbm2d(X, Z, s + 2, octaves=4, freq=1 / 330.0)
    lo = 0.66 - 0.14 * cfg.mountain_amount
    mnt = np.clip((mnt_mask - lo) / (0.83 - lo), 0.0, 1.0) ** 1.8
    ridge = 1.0 - np.abs(noise.fbm2d(X, Z, s + 12, octaves=3, freq=1 / 125.0) - 0.5) * 2.0

    range_noise = noise.fbm2d(X - 1700, Z + 2600, s + 14, octaves=4, freq=1 / 620.0)
    range_gate = _smoothstep01((range_noise - (0.67 - cfg.extreme_mountain_amount * 0.10)) / 0.18)
    range_ridge = 1.0 - np.abs(noise.fbm2d(X + 6400, Z - 3100, s + 15, octaves=4, freq=1 / 155.0) - 0.5) * 2.0
    range_ridge = np.clip(range_ridge, 0.0, 1.0) ** 2.2
    range_detail = (noise.fbm2d(X - 9100, Z + 4200, s + 16, octaves=3, freq=1 / 48.0) - 0.5) * 2.0
    extreme = range_gate * (range_ridge * 0.82 + np.maximum(0.0, range_detail) * 0.18)

    # Rocky detail that only shows up on the mountains (keeps flats calm) so peaks
    # read as craggy stone instead of smooth grey domes.
    mnt_rough = (noise.fbm2d(X + 4100, Z - 2700, s + 21, octaves=4, freq=1 / 20.0) - 0.5) * 2.0
    mnt_medium = (noise.fbm2d(X - 6200, Z + 1500, s + 22, octaves=3, freq=1 / 46.0) - 0.5) * 2.0
    mnt_shape = np.clip(np.maximum(mnt, extreme * 0.92), 0.0, 1.0)

    Hf = (cfg.base_height
          + hills * cfg.hill_amp
          + detail * 2.6
          + mnt * cfg.mountain_amp
          + (mnt ** 1.3) * ridge * cfg.mountain_amp * 0.5
          + extreme * cfg.mountain_amp * 0.95
          + mnt_shape * (mnt_medium * cfg.mountain_amp * 0.22
                         + mnt_rough * cfg.mountain_amp * 0.12))
    mnt = mnt_shape
    return Hf, mnt


def _fields(cfg, blocks, X, Z):
    """Return everything the column needs. All arrays are [z, x]."""
    s = cfg.seed
    wl = cfg.water_level

    # --- base terrain height -------------------------------------------
    Hf, mnt = _terrain_height_fields(cfg, X, Z)

    # --- climate --------------------------------------------------------
    temp, hum, selector, edge_wobble = _base_climate_fields(cfg, X, Z, Hf)
    biome, temp_band, hum_band, badlands_strength = _classify_biomes(cfg, X, Z, Hf, mnt, temp, hum, selector, edge_wobble)

    # Jungles get rougher, more chaotic ground instead of reading like plains
    # with green trees. Badlands get dry eroded ribs without taking over every
    # normal hill. Keep this before rivers so channels still cut through it.
    jungle = biome == "minecraft:jungle"
    if jungle.any():
        jungle_roll = (noise.fbm2d(X + 3210, Z - 5410, s + 86, octaves=4, freq=1 / 58.0) - 0.5) * 2.0
        jungle_mounds = _smoothstep01((noise.fbm2d(X - 8100, Z + 2200, s + 87, octaves=3, freq=1 / 135.0) - 0.47) / 0.28)
        Hf += jungle * (jungle_roll * 5.8 + jungle_mounds * 10.5)
        mnt = np.clip(np.maximum(mnt, jungle_mounds * 0.24), 0.0, 1.0)

    badlands = biome == "minecraft:badlands"
    mesa_lift = badlands_strength
    if np.any(mesa_lift > 0.035):
        mesa_ribs = 1.0 - np.abs(noise.fbm2d(X - 4400, Z + 8850, s + 88, octaves=3, freq=1 / 72.0) - 0.5) * 2.0
        mesa_ribs = np.clip(mesa_ribs, 0.0, 1.0) ** 2.0
        mesa_terrace = np.floor((Hf + (noise.fbm2d(X + 1024, Z - 2048, s + 89, octaves=2, freq=1 / 54.0) - 0.5) * 9.0) / 3.0) * 3.0 - Hf
        Hf += mesa_lift * (mesa_ribs * 10.5 + mesa_terrace * 0.32)
        mnt = np.clip(np.maximum(mnt, mesa_lift * 0.28), 0.0, 1.0)

    # Plains read best when they are calm and open: pull their surface toward the
    # base height so they roll gently instead of bumping around like forest. The
    # deviation is only softened (not flattened to a slab), so borders with other
    # biomes still transition naturally and it keeps a vanilla look.
    is_plainsy = (biome == "minecraft:plains") | (biome == "minecraft:snowy_plains")
    if is_plainsy.any():
        softened = cfg.base_height + (Hf - cfg.base_height) * 0.55
        Hf = np.where(is_plainsy, softened, Hf)

    is_desert = biome == "minecraft:desert"
    is_badlands = biome == "minecraft:badlands"
    is_arid = is_desert | is_badlands

    # --- finite rivers + small lakes -----------------------------------
    depress = np.zeros(X.shape, dtype=np.float64)  # 0..1 downward carve factor
    river_core, river_valley = _finite_river_field(cfg, X, Z, mnt, temp_band, hum_band)
    river_depress = np.clip(river_core * 1.25 + river_valley * 0.32, 0.0, 1.0)
    depress = np.maximum(depress, river_depress)
    is_river = river_core > 0.30
    high = mnt > 0.45

    if cfg.lakes:
        # small ponds: higher frequency = smaller features, sparse gate
        ln = noise.fbm2d(X + 500, Z + 500, s + 6, octaves=2, freq=1 / 46.0)
        lgate = noise.fbm2d(X - 700, Z + 900, s + 61, octaves=2, freq=1 / 220.0)
        cut = 0.11 + 0.05 * cfg.lake_amount
        lf = np.clip((cut - ln) / 0.10, 0.0, 1.0)
        lf = lf * np.clip((lgate - 0.48) / 0.22, 0.0, 1.0)
        lf[high] = 0.0
        depress = np.maximum(depress, lf)

    # carve terrain smoothly toward a bed (gentle banks, no cliff slices)
    bed = wl - (2.0 + 3.2 * depress)
    lowered = Hf * (1.0 - depress) + bed * depress
    Hf = np.minimum(Hf, lowered)

    H = np.clip(Hf, cfg.floor_y + 3, MIN_Y + WORLD_HEIGHT - 40).astype(np.int32)

    # water wherever the surface sits below sea level
    is_water = H <= wl - 1
    is_river = is_river & is_water
    biome[is_river] = "minecraft:river"

    # beaches: shoreline band on land next to water (sand ring)
    is_beach = (~is_water) & (H <= wl + 1) & (H >= wl - 2) & ~is_arid
    biome[is_beach & (biome != "minecraft:snowy_plains") &
          (biome != "minecraft:snowy_taiga") & (biome != "minecraft:snowy_slopes")] = "minecraft:beach"

    # --- snow ----------------------------------------------------------
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
        "is_desert": is_desert, "is_badlands": is_badlands,
        "is_snowy": is_snowy, "mnt": mnt,
        "badlands_strength": badlands_strength,
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
    is_desert = f["is_desert"]; is_badlands = f["is_badlands"]; is_snowy = f["is_snowy"]

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

    if is_badlands.any():
        _paint_badlands_bands(cfg, blocks, block_ids, X, Z, H, is_badlands, inside, world_y, floor)

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

    if is_water.any():
        _seal_water_beds(cfg, blocks, block_ids, H, is_water, is_river, inside,
                         on_wall, floor)

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

    # --- underground fluids + static liquid settling -------------------
    if cfg.fluids:
        _place_fluids(cfg, blocks, block_ids, X, Z, inside, world_y, floor, rng)

    # Minecraft will not tick freshly written region files before the player
    # sees them. Precompute bounded water/lava flow so river edges become
    # waterfalls and cave lava pools look settled instead of frozen source
    # sheets. This is chunk-local by design, but it catches the visible cases
    # inside generated ravines/caves.
    _settle_liquid_flow(cfg, blocks, block_ids, inside, floor)

    # --- ores (replace stone, stone variants, or badlands terracotta) ---
    replaceable = (blocks.stone, blocks.andesite, blocks.diorite,
                   blocks.granite, blocks.terracotta, blocks.orange_terracotta,
                   blocks.yellow_terracotta, blocks.brown_terracotta,
                   blocks.red_terracotta, blocks.white_terracotta)
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
        _decorate(cfg, blocks, block_ids, H, biome_str, is_water,
                  is_desert | is_badlands, is_snowy, inside, on_wall, rng)

    # --- snow layer -----------------------------------------------------
    if is_snowy.any():
        cols = inside & ~on_wall & is_snowy & ~is_water & (H > floor + 3)
        sy = (H - MIN_Y) + 1
        valid = cols & (sy < WORLD_HEIGHT)
        syy, szz, sxx = sy[valid], zz[valid], xx[valid]
        below = block_ids[syy - 1, szz, sxx]
        ok = (block_ids[syy, szz, sxx] == 0) & (below != 0) & (below != blocks.water)
        block_ids[syy[ok], szz[ok], sxx[ok]] = blocks.snow_layer

    # --- underworld: deep dark + nether below the overworld floor -------
    if (cfg.deep_dark or cfg.nether) and inside.any():
        underworld.apply_underworld(cfg, blocks, block_ids, X, Z, inside, on_wall, rng)

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
    value = int(np.clip(Hf, cfg.floor_y + 3, MIN_Y + WORLD_HEIGHT - 40)[0, 0])
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
    # Rivers/lakes are generated before cave carving. Without this cap, later
    # cave systems can cut directly under the river bed and leave floating
    # water sheets like the screenshot. Keep a shallow natural plug under every
    # water column, deeper for rivers because they are long features.
    for iz in range(16):
        for ix in range(16):
            if not inside[iz, ix] or on_wall[iz, ix] or not is_water[iz, ix]:
                continue
            surf = int(H[iz, ix])
            depth = 34 if is_river[iz, ix] else 16
            y0 = max(floor + 1, surf - depth)
            y1 = min(surf, MIN_Y + WORLD_HEIGHT - 2)
            for wy in range(y0, y1 + 1):
                yi = wy - MIN_Y
                if block_ids[yi, iz, ix] == blocks.air:
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
        if surf <= cfg.water_level + 1 and _rand01(cell_x, cell_z, cfg.seed, 7004) > 0.28:
            continue

        start_y = min(surf - 2, cfg.water_level + 52)
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
                r *= 1.82
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
                   world_y, floor):
    # Old implementation used an infinite noise contour, which can generate a
    # world-spanning laser-cut canyon. This is finite segment carving with
    # tapered ends, sloped walls, and sparse lava only in tiny bottom pools.
    spacing = 300
    reach = 260
    chance = 0.11
    solid = _solid_carve_mask(block_ids, blocks)

    for cell_x, cell_z in _iter_feature_cells(X, Z, spacing, reach):
        if _rand01(cell_x, cell_z, cfg.seed, 8001) > chance:
            continue

        ox = cell_x * spacing + _rand_range(cell_x, cell_z, cfg.seed, 8002, 45.0, spacing - 45.0)
        oz = cell_z * spacing + _rand_range(cell_x, cell_z, cfg.seed, 8003, 45.0, spacing - 45.0)
        angle = _rand_range(cell_x, cell_z, cfg.seed, 8004, 0.0, np.pi * 2.0)
        length = _rand_range(cell_x, cell_z, cfg.seed, 8005, 82.0, 185.0)
        width = _rand_range(cell_x, cell_z, cfg.seed, 8006, 5.0, 10.5)
        depth = _rand_range(cell_x, cell_z, cfg.seed, 8007, 28.0, 72.0)
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
        col = (dist < radius) & inside & ~on_wall & (taper > 0.04)
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
        _settle_one_liquid(block_ids, blocks, inside, blocks.water,
                           lateral_steps=7,
                           lo_y=floor + 1,
                           hi_y=max(cfg.water_level + 4, floor + 8),
                           can_fall_to_y=floor + 1)

    if np.any((block_ids == blocks.lava) & inside3):
        _settle_one_liquid(block_ids, blocks, inside, blocks.lava,
                           lateral_steps=3,
                           lo_y=floor + 1,
                           hi_y=floor + 18,
                           can_fall_to_y=floor + 1)


def _settle_one_liquid(block_ids, blocks, inside, liquid_id, lateral_steps,
                       lo_y, hi_y, can_fall_to_y):
    lo = max(0, int(can_fall_to_y) - MIN_Y)
    hi = min(WORLD_HEIGHT - 1, int(hi_y) - MIN_Y)
    spread_lo = max(0, int(lo_y) - MIN_Y)
    spread_hi = hi
    if hi <= lo:
        return

    for _ in range(max(1, int(lateral_steps))):
        # First let every liquid column fall straight down through air. This is
        # what turns a river edge above a cave/ravine into an actual waterfall.
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

    land = Hf > cfg.water_level - 4
    if not land.any():
        land = np.ones(X.shape, dtype=bool)
    temp_land = temp[land]
    hum_land = hum[land]

    frozen_cut = _quantile_or(temp_land, 0.105, cfg.biome_frozen_cut)
    cold_cut = _quantile_or(temp_land, 0.305, cfg.biome_cold_cut)
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
        cfg.biome_frozen_cut = _clamp(cfg.biome_frozen_cut + (0.120 - snow) * 0.42, 0.06, cfg.biome_cold_cut - 0.08)
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
    (job_index, cfg, xmin, xmax, pscale, preview_size, region_dir,
     rx, rz, cxmin, cxmax, czmin, czmax, progress_queue, progress_interval,
     send_preview_progress) = args

    blocks, biome_reg = _prepare_palettes(cfg)
    block_palette = blocks.palette()
    biome_palette = list(biome_reg.list)
    rw = RegionWriter(rx, rz)

    patch_x, patch_z, patch_w, patch_h = _preview_patch_rect(
        cfg, xmin, xmax, pscale, preview_size, cxmin, cxmax, czmin, czmax)
    preview_patch = np.zeros((patch_h, patch_w, 3), dtype=np.uint8)

    chunks = 0
    last_reported_chunks = 0
    pending_patches = []
    for cz in range(czmin, czmax + 1):
        for cx in range(cxmin, cxmax + 1):
            rng_seed = _chunk_rng_seed(cfg.seed, cx, cz)
            block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_chunk(
                cfg, blocks, biome_reg, cx, cz, xmin, xmax, rng_seed)

            root = build_chunk(cx, cz, block_ids, biome_ids, block_palette,
                               biome_palette, cfg.data_version, MIN_SECTION, N_SECTIONS,
                               block_entities=block_entities)
            rw.add_chunk(cx, cz, root)

            chunk_patch = _chunk_preview_patch(cfg, cx, cz, xmin, pscale, preview_size,
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
    (job_index, cfg, xmin, xmax, pscale, preview_size,
     cxmin, cxmax, czmin, czmax, progress_queue, progress_interval,
     send_preview_progress) = args

    blocks, biome_reg = _prepare_palettes(cfg)
    block_palette = blocks.palette()
    biome_palette = list(biome_reg.list)

    compressed_chunks = []
    preview_patches = []
    pending_patches = []
    chunks = 0
    last_reported_chunks = 0

    for cz in range(czmin, czmax + 1):
        for cx in range(cxmin, cxmax + 1):
            rng_seed = _chunk_rng_seed(cfg.seed, cx, cz)
            block_ids, biome_ids, H, biome_str, inside, is_water, block_entities = generate_chunk(
                cfg, blocks, biome_reg, cx, cz, xmin, xmax, rng_seed)

            root = build_chunk(cx, cz, block_ids, biome_ids, block_palette,
                               biome_palette, cfg.data_version, MIN_SECTION, N_SECTIONS,
                               block_entities=block_entities)
            compressed_chunks.append((cx, cz, nbt.to_zlib("", root)))

            chunk_patch = _chunk_preview_patch(cfg, cx, cz, xmin, pscale, preview_size,
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

    xmin = -(cfg.size // 2)
    xmax = xmin + cfg.size - 1
    cfg = _tune_biomes(cfg, xmin, xmax)

    # Plan biome centers + structures up front so the parallel workers can stamp
    # any structure that overlaps their chunk (structures cross chunk borders).
    report(0.0, "Mapping biomes & planning structures...")
    biome_midpoints = compute_biome_midpoints(cfg)
    layer_info = underworld.layer_starts(cfg)
    cfg.structure_plan = structures.plan_structures(cfg, biome_midpoints, layer_info)

    cxmin, cxmax = xmin >> 4, xmax >> 4
    czmin, czmax = xmin >> 4, xmax >> 4
    total_chunks = (cxmax - cxmin + 1) * (czmax - czmin + 1)

    region_jobs = list(_region_jobs(cxmin, cxmax, czmin, czmax))
    requested_workers = os.cpu_count() or 1 if workers is None or int(workers) <= 0 else int(workers)
    use_tile_jobs = total_chunks >= 256 and len(region_jobs) < requested_workers
    tile_jobs = list(_tile_jobs(cxmin, cxmax, czmin, czmax)) if use_tile_jobs else []
    active_jobs = tile_jobs if use_tile_jobs else region_jobs
    worker_count = _resolve_worker_count(workers, len(region_jobs), total_chunks,
                                         len(active_jobs))

    pscale = max(1, cfg.size // 768)
    preview_size = (cfg.size + pscale - 1) // pscale
    preview = np.zeros((preview_size, preview_size, 3), dtype=np.uint8)
    region_writers = {}

    def make_jobs(progress_queue=None, progress_interval=8):
        if use_tile_jobs:
            return [
                (job_index, cfg, xmin, xmax, pscale, preview_size,
                 jcxmin, jcxmax, jczmin, jczmax, progress_queue, progress_interval,
                 preview_progress is not None)
                for job_index, (jcxmin, jcxmax, jczmin, jczmax)
                in enumerate(tile_jobs)
            ]
        return [
            (job_index, cfg, xmin, xmax, pscale, preview_size, region_dir,
             rx, rz, jcxmin, jcxmax, jczmin, jczmax, progress_queue, progress_interval,
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
        "spawn": (0, spawn_y, 0), "bounds": (xmin, xmax),
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
        nbm = underworld.nether_biome_map(cfg, X, Z)
        names = np.array(underworld.NETHER_BIOMES)
        neth_names = names[nbm]
        segs = underworld._segments(cfg)
        neth_y = cfg.floor_y - cfg.deep_dark_height // 2
        for seg in segs:
            if seg[0] == "nether":
                neth_y = (seg[1] + seg[2]) // 2
        Hn = np.full(X.shape, neth_y)
        out += _region_centroids(neth_names, X, Z, Hn, step, cell_area, min_cells)

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


def _chunk_preview_patch(cfg, cx, cz, xmin, pscale, preview_size, H, biome_str, inside, is_water):
    px0 = preview_size
    pz0 = preview_size
    px1 = -1
    pz1 = -1
    points = []

    for iz in range(16):
        wz = cz * 16 + iz
        pz = (wz - xmin) // pscale
        if pz < 0 or pz >= preview_size:
            continue
        for ix in range(16):
            if not inside[iz, ix]:
                continue
            wx = cx * 16 + ix
            px = (wx - xmin) // pscale
            if px < 0 or px >= preview_size:
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


def _fill_preview_patch(preview, cfg, cx, cz, xmin, pscale, patch_x, patch_z,
                        H, biome_str, inside, is_water):
    patch_info = _chunk_preview_patch(cfg, cx, cz, xmin, pscale,
                                      preview.shape[0] + max(patch_x, patch_z),
                                      H, biome_str, inside, is_water)
    if patch_info is None:
        return
    px, pz, patch = patch_info
    local_info = (px - patch_x, pz - patch_z, patch)
    _apply_preview_patch(preview, local_info)


def _fill_preview(preview, cfg, cx, cz, xmin, pscale, H, biome_str, inside, is_water):
    patch_info = _chunk_preview_patch(cfg, cx, cz, xmin, pscale, preview.shape[0],
                                      H, biome_str, inside, is_water)
    _apply_preview_patch(preview, patch_info)
