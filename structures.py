"""
Procedural structures for the vanilla generator (original implementations -- not
copied from Minecraft; they just use the same block ids and vanilla loot-table
names so chests populate correctly in-game).

Placement is planned globally from the seed (so it is deterministic and works
with the parallel per-chunk workers): plan_structures() returns a list of
placements with a world-space bounding box, and stamp_structures() writes the
part of any placement that overlaps a given chunk, returning its block entities.

Structures covered: desert_pyramid, village, stronghold (end portal room),
ancient_city, nether_fortress, bastion.
"""
import numpy as np

import nbt

# Structure footprint half-extents (blocks) used for bbox overlap tests.
FOOTPRINT = {
    "desert_pyramid": (12, 12),
    "village": (26, 26),
    "stronghold": (10, 10),
    "ancient_city": (16, 16),
    "nether_fortress": (14, 14),
    "bastion": (12, 12),
}

LOOT = {
    "desert_pyramid": "minecraft:chests/desert_pyramid",
    "village": "minecraft:chests/village/village_weaponsmith",
    "stronghold": "minecraft:chests/stronghold_corridor",
    "ancient_city": "minecraft:chests/ancient_city",
    "nether_fortress": "minecraft:chests/nether_bridge",
    "bastion": "minecraft:chests/bastion_treasure",
}

_OPP = {"north": "south", "south": "north", "east": "west", "west": "east"}


# ---------------------------------------------------------------------------
# Builder helper
# ---------------------------------------------------------------------------

class Build:
    """Accumulates (wx, wy, wz, block_id) and block entities in world coords."""

    def __init__(self):
        self.cells = {}          # (wx,wy,wz) -> block_id  (last write wins)
        self.ents = []           # (wx,wy,wz, nbt.Compound)

    def s(self, x, y, z, b):
        self.cells[(int(x), int(y), int(z))] = int(b)

    def box(self, x0, x1, y0, y1, z0, z1, b):
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.cells[(x, y, z)] = int(b)

    def walls(self, x0, x1, y0, y1, z0, z1, b):
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    if x in (x0, x1) or z in (z0, z1):
                        self.cells[(x, y, z)] = int(b)

    def chest(self, x, y, z, bid, loot):
        self.cells[(int(x), int(y), int(z))] = int(bid)
        c = nbt.Compound()
        c["id"] = nbt.String("minecraft:chest")
        c["x"] = nbt.Int(int(x)); c["y"] = nbt.Int(int(y)); c["z"] = nbt.Int(int(z))
        c["keepPacked"] = nbt.Byte(0)
        if loot:
            c["LootTable"] = nbt.String(loot)
        else:
            c["Items"] = nbt.List(nbt.Compound.tag_id, [])
        self.ents.append((int(x), int(y), int(z), c))

    def spawner(self, x, y, z, bid, mob):
        self.cells[(int(x), int(y), int(z))] = int(bid)
        c = nbt.Compound()
        c["id"] = nbt.String("minecraft:mob_spawner")
        c["x"] = nbt.Int(int(x)); c["y"] = nbt.Int(int(y)); c["z"] = nbt.Int(int(z))
        sd = nbt.Compound(); ent = nbt.Compound(); ent["id"] = nbt.String(mob)
        sd["entity"] = ent
        c["SpawnData"] = sd
        self.ents.append((int(x), int(y), int(z), c))


# ---------------------------------------------------------------------------
# Individual structures
# ---------------------------------------------------------------------------

def _desert_pyramid(cfg, bl, ox, oy, oz, rng):
    b = Build()
    ss, cut, chis = bl.sandstone, bl.cut_sandstone, bl.chiseled_sandstone
    r = 10
    # foundation
    b.box(ox - r, ox + r, oy - 1, oy, oz - r, oz + r, ss)
    # stepped hollow body
    for k in range(0, 9):
        hr = r - k
        y = oy + 1 + k
        for x in range(ox - hr, ox + hr + 1):
            for z in range(oz - hr, oz + hr + 1):
                if x in (ox - hr, ox + hr) or z in (oz - hr, oz + hr):
                    b.s(x, y, z, ss)
    b.box(ox - 2, ox + 2, oy + 10, oy + 10, oz - 2, oz + 2, ss)
    # two front turrets
    for sx in (ox - r, ox + r):
        b.box(sx - 1, sx + 1, oy + 1, oy + 8, oz - 1, oz + 1, ss)
        b.s(sx, oy + 9, oz, chis)
    # orange terracotta motif on the front face
    for dz in (-1, 0, 1):
        b.s(ox + dz, oy + 4, oz - r, bl.orange_terracotta)
    b.s(ox, oy + 3, oz - r, bl.orange_terracotta)
    b.s(ox, oy + 5, oz - r, bl.orange_terracotta)
    # buried treasure chamber
    fy = oy - 9
    b.box(ox - 3, ox + 3, fy - 1, fy + 4, oz - 3, oz + 3, ss)
    b.box(ox - 2, ox + 2, fy, fy + 3, oz - 2, oz + 2, bl.air)
    # blue/orange terracotta floor motif
    b.s(ox, fy, oz, bl.orange_terracotta)
    for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        b.s(ox + dx, fy, oz + dz, bl.blue_terracotta)
    b.chest(ox - 2, fy, oz, bl.chest_face["east"], LOOT["desert_pyramid"])
    b.chest(ox + 2, fy, oz, bl.chest_face["west"], LOOT["desert_pyramid"])
    b.chest(ox, fy, oz - 2, bl.chest_face["south"], LOOT["desert_pyramid"])
    b.chest(ox, fy, oz + 2, bl.chest_face["north"], LOOT["desert_pyramid"])
    return b


def _house(b, bl, ox, oy, oz, w, d, rng, put_chest=False):
    planks = rng.choice([bl.oak_planks, bl.spruce_planks])
    x0, x1 = ox - w, ox + w
    z0, z1 = oz - d, oz + d
    # cobble foundation + floor
    b.box(x0, x1, oy - 1, oy - 1, z0, z1, bl.cobblestone)
    b.box(x0 + 1, x1 - 1, oy, oy, z0 + 1, z1 - 1, planks)
    # log corners
    for cx in (x0, x1):
        for cz in (z0, z1):
            b.box(cx, cx, oy, oy + 3, cz, cz, bl.log("oak"))
    # plank walls
    for y in range(oy, oy + 4):
        for x in range(x0, x1 + 1):
            for z in (z0, z1):
                if (x, z) not in ((x0, z0), (x1, z0), (x0, z1), (x1, z1)):
                    b.s(x, y, z, planks)
        for z in range(z0, z1 + 1):
            for x in (x0, x1):
                if (x, z) not in ((x0, z0), (x1, z0), (x0, z1), (x1, z1)):
                    b.s(x, y, z, planks)
    # windows
    b.s(x0, oy + 2, oz, bl.glass); b.s(x1, oy + 2, oz, bl.glass)
    # doorway (opening) on the south wall
    b.s(ox, oy, z1, bl.air); b.s(ox, oy + 1, z1, bl.air)
    # flat plank roof + lantern
    b.box(x0, x1, oy + 4, oy + 4, z0, z1, planks)
    b.s(ox, oy + 3, oz, bl.lantern)
    if put_chest:
        b.chest(x0 + 1, oy, z0 + 1, bl.chest_face["south"], LOOT["village"])


def _village(cfg, bl, ox, oy, oz, rng):
    b = Build()
    # central well
    b.box(ox - 1, ox + 1, oy - 1, oy - 1, oz - 1, oz + 1, bl.cobblestone)
    b.walls(ox - 1, ox + 1, oy, oy + 1, oz - 1, oz + 1, bl.cobblestone)
    b.s(ox, oy, oz, bl.water)
    for cx in (ox - 1, ox + 1):
        for cz in (oz - 1, oz + 1):
            b.box(cx, cx, oy, oy + 2, cz, cz, bl.oak_planks)
    b.box(ox - 1, ox + 1, oy + 3, oy + 3, oz - 1, oz + 1, bl.oak_planks)
    # houses around the well
    spots = [(-12, -10, 3, 3, True), (11, -8, 3, 2, False),
             (-10, 11, 2, 3, True), (12, 10, 3, 3, False), (0, -16, 4, 3, False)]
    for dx, dz, w, d, chest in spots:
        _house(b, bl, ox + dx, oy, oz + dz, w, d, rng, chest)
        # cobble path toward the well
        px = ox + (dx // 2)
        pz = oz + (dz // 2)
        b.s(px, oy - 1, pz, bl.cobblestone)
    # gravel/cobble paths radiating out
    for t in range(2, 12):
        b.s(ox, oy - 1, oz + t, bl.cobblestone)
        b.s(ox + t, oy - 1, oz, bl.cobblestone)
    return b


def _stronghold(cfg, bl, ox, oy, oz, rng):
    b = Build()
    sb = bl.stone_bricks
    # room shell
    b.box(ox - 5, ox + 5, oy - 1, oy - 1, oz - 4, oz + 4, sb)
    b.walls(ox - 5, ox + 5, oy, oy + 5, oz - 4, oz + 4, sb)
    b.box(ox - 5, ox + 5, oy + 5, oy + 5, oz - 4, oz + 4, sb)
    # weathering
    for _ in range(60):
        x = rng.integers(ox - 5, ox + 6); y = rng.integers(oy, oy + 6)
        z = rng.choice([oz - 4, oz + 4])
        b.s(x, y, z, rng.choice([bl.mossy_stone_bricks, bl.cracked_stone_bricks, sb]))
    # end portal frame ring around a lava pit (portal left inactive)
    b.box(ox - 1, ox + 1, oy - 2, oy - 1, oz - 1, oz + 1, bl.lava)
    for x in (ox - 1, ox, ox + 1):
        b.s(x, oy, oz - 2, bl.portal_frame["south"])
        b.s(x, oy, oz + 2, bl.portal_frame["north"])
    for z in (oz - 1, oz, oz + 1):
        b.s(ox - 2, oy, z, bl.portal_frame["east"])
        b.s(ox + 2, oy, z, bl.portal_frame["west"])
    # trims + hazards + loot
    b.s(ox - 4, oy + 1, oz - 3, bl.torch)
    b.s(ox + 4, oy + 1, oz + 3, bl.torch)
    b.s(ox + 3, oy + 3, oz - 3, bl.cobweb)
    b.spawner(ox - 4, oy, oz + 3, bl.spawner, "minecraft:silverfish")
    b.chest(ox + 4, oy, oz, bl.chest_face["west"], LOOT["stronghold"])
    b.s(ox - 3, oy, oz - 3, bl.bookshelf)
    b.s(ox - 3, oy + 1, oz - 3, bl.bookshelf)
    return b


def _ancient_city(cfg, bl, ox, oy, oz, rng):
    b = Build()
    db, tiles, bricks = bl.deepslate, bl.deepslate_tiles, bl.deepslate_bricks
    # sunken platform
    b.box(ox - 12, ox + 12, oy - 1, oy - 1, oz - 12, oz + 12, tiles)
    for _ in range(140):
        x = rng.integers(ox - 12, ox + 13); z = rng.integers(oz - 12, oz + 13)
        b.s(x, oy - 1, z, rng.choice([tiles, bricks, bl.cobbled_deepslate]))
    # reinforced-deepslate corner frames
    for cx in (ox - 12, ox + 12):
        for cz in (oz - 12, oz + 12):
            b.box(cx, cx, oy, oy + 6, cz, cz, bl.reinforced_deepslate)
    # central dais
    b.box(ox - 3, ox + 3, oy - 1, oy, oz - 3, oz + 3, bricks)
    b.box(ox - 2, ox + 2, oy + 1, oy + 1, oz - 2, oz + 2, bl.reinforced_deepslate)
    b.s(ox, oy + 1, oz, bl.sculk)
    # sculk spread + soul lanterns; generated cities intentionally avoid
    # sensors/shriekers/catalysts for this world style.
    for _ in range(120):
        x = rng.integers(ox - 11, ox + 12); z = rng.integers(oz - 11, oz + 12)
        b.s(x, oy - 1, z, bl.sculk)
    for dx, dz in ((-8, -8), (8, -8), (-8, 8), (8, 8), (0, 0)):
        b.s(ox + dx, oy + 3, oz + dz, bl.soul_lantern)
    b.chest(ox - 5, oy, oz, bl.chest_face["east"], LOOT["ancient_city"])
    b.chest(ox + 5, oy, oz, bl.chest_face["west"], LOOT["ancient_city"])
    return b


def _nether_fortress(cfg, bl, ox, oy, oz, rng):
    b = Build()
    nb = bl.nether_bricks
    # main platform + a bridge to the east
    b.box(ox - 4, ox + 4, oy - 1, oy - 1, oz - 4, oz + 4, nb)
    b.box(ox + 4, ox + 12, oy - 1, oy - 1, oz - 1, oz + 1, nb)
    # walls with fence railings
    b.walls(ox - 4, ox + 4, oy, oy + 3, oz - 4, oz + 4, nb)
    for x in range(ox - 4, ox + 5):
        b.s(x, oy, oz - 4, bl.nether_brick_fence)
        b.s(x, oy, oz + 4, bl.nether_brick_fence)
    for x in range(ox + 5, ox + 13):
        b.s(x, oy, oz - 1, bl.nether_brick_fence)
        b.s(x, oy, oz + 1, bl.nether_brick_fence)
    # doorway + roof arches
    b.box(ox, ox, oy, oy + 2, oz + 4, oz + 4, bl.air)
    b.box(ox - 4, ox + 4, oy + 4, oy + 4, oz - 4, oz + 4, nb)
    # nether wart farm on soul sand
    for dx in (-2, -1, 0, 1, 2):
        b.s(ox + dx, oy, oz + 2, bl.soul_sand)
        b.s(ox + dx, oy + 1, oz + 2, bl.nether_wart_plant)
    # blaze spawner + loot
    b.spawner(ox, oy + 1, oz - 2, bl.spawner, "minecraft:blaze")
    b.chest(ox - 3, oy, oz - 3, bl.chest_face["south"], LOOT["nether_fortress"])
    return b


def _bastion(cfg, bl, ox, oy, oz, rng):
    b = Build()
    pbb, bs = bl.polished_blackstone_bricks, bl.blackstone
    b.box(ox - 9, ox + 9, oy - 1, oy - 1, oz - 9, oz + 9, pbb)
    b.walls(ox - 9, ox + 9, oy, oy + 6, oz - 9, oz + 9, pbb)
    # ramparts
    b.box(ox - 9, ox + 9, oy + 6, oy + 6, oz - 9, oz + 9, bs)
    # weathering + gilded blackstone flecks
    for _ in range(120):
        x = rng.integers(ox - 9, ox + 10); y = rng.integers(oy, oy + 7)
        z = rng.choice([oz - 9, oz + 9])
        r = rng.random()
        b.s(x, y, z, bl.gilded_blackstone if r < 0.08 else
            (bl.blackstone if r < 0.4 else pbb))
    # inner keep + gold hoard
    b.box(ox - 3, ox + 3, oy, oy + 4, oz - 3, oz + 3, pbb)
    b.box(ox - 2, ox + 2, oy, oy + 3, oz - 2, oz + 2, bl.air)
    b.s(ox, oy, oz, bl.gold_block)
    b.s(ox, oy + 1, oz, bl.chiseled_polished_blackstone)
    b.box(ox, ox, oy, oy + 2, oz + 3, oz + 3, bl.air)   # doorway
    b.chest(ox - 2, oy, oz - 2, bl.chest_face["south"], LOOT["bastion"])
    b.chest(ox + 2, oy, oz + 2, bl.chest_face["north"], LOOT["bastion"])
    return b


BUILDERS = {
    "desert_pyramid": _desert_pyramid,
    "village": _village,
    "stronghold": _stronghold,
    "ancient_city": _ancient_city,
    "nether_fortress": _nether_fortress,
    "bastion": _bastion,
}


# ---------------------------------------------------------------------------
# Planning + stamping
# ---------------------------------------------------------------------------

def _rng_for(cfg, kind, x, z):
    seed = (int(cfg.seed) ^ (int(x) * 0x9E3779B1) ^ (int(z) * 0x85157AF5)
            ^ (hash(kind) & 0xFFFFFFFF)) & 0x7FFFFFFF
    return np.random.default_rng(seed)


def _surface_y(cfg, x, z):
    import generator
    blocks, _ = generator._prepare_palettes(cfg)
    X = np.array([[float(x)]]); Z = np.array([[float(z)]])
    return int(generator._fields(cfg, blocks, X, Z)["H"][0, 0])


def plan_structures(cfg, biome_regions, layer_starts):
    """Choose one placement of each applicable structure. `biome_regions` is the
    output of generator.compute_biome_midpoints; `layer_starts` maps
    'deep_dark'/'nether' -> top Y (or None)."""
    if not cfg.structures:
        return []
    xmin = -(cfg.size // 2)
    xmax = xmin + cfg.size - 1

    def regions_of(*names):
        return [r for r in biome_regions if r["biome"] in names]

    def far_from_spawn(regs):
        # prefer a region whose center is not right on top of world spawn
        regs = sorted(regs, key=lambda r: -(abs(r["x"]) + abs(r["z"])))
        return regs

    plan = []

    def add(kind, x, y, z):
        hx, hz = FOOTPRINT[kind]
        x = int(max(xmin + hx + 2, min(xmax - hx - 2, x)))
        z = int(max(xmin + hz + 2, min(xmax - hz - 2, z)))
        plan.append({"type": kind, "x": x, "y": int(y), "z": int(z),
                     "x0": x - hx, "x1": x + hx, "z0": z - hz, "z1": z + hz})

    # Desert pyramid -> a desert region
    deserts = far_from_spawn(regions_of("minecraft:desert"))
    if deserts:
        r = deserts[0]
        add("desert_pyramid", r["x"], _surface_y(cfg, r["x"], r["z"]), r["z"])

    # Village -> a flat, temperate region
    villages = far_from_spawn(regions_of(
        "minecraft:plains", "minecraft:savanna", "minecraft:taiga",
        "minecraft:snowy_plains", "minecraft:desert"))
    if villages:
        r = villages[0]
        add("village", r["x"], _surface_y(cfg, r["x"], r["z"]), r["z"])

    # Stronghold -> underground in the regular cavern layer, offset from spawn
    sx, sz = xmin + (xmax - xmin) // 4, xmin + (xmax - xmin) * 3 // 4
    sy = max(cfg.floor_y + 6, min(cfg.base_height - 24, cfg.floor_y + 10))
    add("stronghold", sx, sy, sz)

    # Ancient city -> deep dark band
    if layer_starts.get("deep_dark") is not None:
        deep_dark_height = int(getattr(cfg, "deep_dark_height", 0) or (layer_starts["deep_dark"] - (-64)))
        ay = layer_starts["deep_dark"] - max(10, deep_dark_height // 3)
        add("ancient_city", xmin + (xmax - xmin) * 3 // 5,
            ay, xmin + (xmax - xmin) * 2 // 5)

    # Nether fortress + bastion -> nether band, separated
    if layer_starts.get("nether") is not None:
        ny = layer_starts["nether"] - max(6, cfg.nether_height // 3)
        add("nether_fortress", xmin + (xmax - xmin) // 3, ny,
            xmin + (xmax - xmin) // 3)
        add("bastion", xmin + (xmax - xmin) * 2 // 3, ny,
            xmin + (xmax - xmin) * 2 // 3)

    return plan


def stamp_structures(cfg, blocks, block_ids, cx, cz, min_y, world_height):
    """Write any planned structure blocks that fall inside this chunk. Returns a
    list of nbt.Compound block entities for the chunk."""
    plan = getattr(cfg, "structure_plan", None)
    if not plan:
        return []
    wx0, wz0 = cx * 16, cz * 16
    wx1, wz1 = wx0 + 15, wz0 + 15
    entities = []
    for p in plan:
        if p["x1"] < wx0 or p["x0"] > wx1 or p["z1"] < wz0 or p["z0"] > wz1:
            continue
        builder = BUILDERS.get(p["type"])
        if builder is None:
            continue
        rng = _rng_for(cfg, p["type"], p["x"], p["z"])
        b = builder(cfg, blocks, p["x"], p["y"], p["z"], rng)
        for (wx, wy, wz), bid in b.cells.items():
            if wx0 <= wx <= wx1 and wz0 <= wz <= wz1:
                yi = wy - min_y
                if 0 <= yi < world_height:
                    block_ids[yi, wz & 15, wx & 15] = bid
        for (wx, wy, wz, comp) in b.ents:
            if wx0 <= wx <= wx1 and wz0 <= wz <= wz1 and 0 <= wy - min_y < world_height:
                entities.append(comp)
    return entities
