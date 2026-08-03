"""Minimal Bedrock 1.18.30 LevelDB writer for PocketMine-compatible worlds.

The format constants and palette layout mirror PocketMine-MP's LevelDB reader:
StorageVersion 10, NetworkVersion 503, chunk version 40 and subchunk version 9.
The database is a valid log-backed LevelDB store, so no native Python LevelDB
dependency is required.
"""

import os
import shutil
import struct
import time
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np


CHUNK_VERSION = 40
SUBCHUNK_VERSION = 9
STORAGE_VERSION = 10
NETWORK_VERSION = 503
BLOCK_STATE_VERSION = 17959425

MIN_Y = -64
MIN_SECTION = -4
N_SECTIONS = 24
WORLD_HEIGHT = 384

KEY_HEIGHTMAP_AND_3D_BIOMES = b"\x2b"
KEY_NEW_VERSION = b"\x2c"
KEY_SUBCHUNK = b"\x2f"
KEY_BLOCK_ENTITIES = b"\x31"
KEY_ENTITIES = b"\x32"
KEY_FINALIZATION = b"\x36"


# ---------------------------------------------------------------------------
# Little-endian NBT used by Bedrock world storage
# ---------------------------------------------------------------------------

def _l8(out: bytearray, value: int):
    out.append(value & 0xFF)


def _l16(out: bytearray, value: int):
    out.extend(struct.pack("<H", value & 0xFFFF))


def _l32(out: bytearray, value: int):
    out.extend(struct.pack("<i", int(value)))


def _u32(out: bytearray, value: int):
    out.extend(struct.pack("<I", int(value) & 0xFFFFFFFF))


def _l64(out: bytearray, value: int):
    out.extend(struct.pack("<q", int(value)))


def _string_payload(out: bytearray, value: str):
    raw = value.encode("utf-8")
    _l16(out, len(raw))
    out.extend(raw)


def _tag_header(out: bytearray, tag_type: int, name: str):
    _l8(out, tag_type)
    _string_payload(out, name)


def _nbt_byte(out: bytearray, name: str, value: int):
    _tag_header(out, 1, name)
    _l8(out, value)


def _nbt_int(out: bytearray, name: str, value: int):
    _tag_header(out, 3, name)
    _l32(out, value)


def _nbt_long(out: bytearray, name: str, value: int):
    _tag_header(out, 4, name)
    _l64(out, value)


def _nbt_float(out: bytearray, name: str, value: float):
    _tag_header(out, 5, name)
    out.extend(struct.pack("<f", float(value)))


def _nbt_string(out: bytearray, name: str, value: str):
    _tag_header(out, 8, name)
    _string_payload(out, value)


def _nbt_list(out: bytearray, name: str, item_type: int, count: int):
    _tag_header(out, 9, name)
    _l8(out, item_type)
    _l32(out, count)


def _nbt_compound(out: bytearray, name: str):
    _tag_header(out, 10, name)


def _nbt_root(out: bytearray):
    _l8(out, 10)
    _string_payload(out, "")


def _nbt_end(out: bytearray):
    _l8(out, 0)


# ---------------------------------------------------------------------------
# Java block state -> canonical Bedrock 1.18.30 block state
# ---------------------------------------------------------------------------

_FLOWERS = {
    "minecraft:dandelion": ("minecraft:yellow_flower", {}),
    "minecraft:poppy": ("minecraft:red_flower", {"flower_type": "poppy"}),
    "minecraft:cornflower": ("minecraft:red_flower", {"flower_type": "cornflower"}),
    "minecraft:azure_bluet": ("minecraft:red_flower", {"flower_type": "houstonia"}),
    "minecraft:oxeye_daisy": ("minecraft:red_flower", {"flower_type": "oxeye"}),
}

_TERRACOTTA_COLORS = {
    "minecraft:white_terracotta": "white",
    "minecraft:light_gray_terracotta": "silver",
    "minecraft:gray_terracotta": "gray",
    "minecraft:orange_terracotta": "orange",
    "minecraft:yellow_terracotta": "yellow",
    "minecraft:red_terracotta": "red",
    "minecraft:brown_terracotta": "brown",
    "minecraft:green_terracotta": "green",
    "minecraft:lime_terracotta": "lime",
    "minecraft:cyan_terracotta": "cyan",
}


def _canonical_block_state(state: Tuple[str, dict]) -> Tuple[str, Dict[str, object]]:
    name, java_props = state
    props: Dict[str, object] = {}

    if name == "minecraft:grass_block":
        return "minecraft:grass", props
    if name == "minecraft:coarse_dirt":
        return "minecraft:dirt", {"dirt_type": "coarse"}
    if name == "minecraft:dirt":
        return "minecraft:dirt", {"dirt_type": "normal"}
    if name == "minecraft:red_sand":
        return "minecraft:sand", {"sand_type": "red"}
    if name == "minecraft:sand":
        return "minecraft:sand", {"sand_type": "normal"}
    if name in ("minecraft:sandstone", "minecraft:smooth_sandstone", "minecraft:cut_sandstone"):
        kind = {"minecraft:sandstone": "default", "minecraft:smooth_sandstone": "smooth",
                "minecraft:cut_sandstone": "cut"}[name]
        return "minecraft:sandstone", {"sand_stone_type": kind}
    if name in ("minecraft:red_sandstone", "minecraft:smooth_red_sandstone",
                "minecraft:cut_red_sandstone"):
        kind = {"minecraft:red_sandstone": "default", "minecraft:smooth_red_sandstone": "smooth",
                "minecraft:cut_red_sandstone": "cut"}[name]
        return "minecraft:red_sandstone", {"sand_stone_type": kind}
    if name == "minecraft:terracotta":
        return "minecraft:hardened_clay", props
    if name in _TERRACOTTA_COLORS:
        return "minecraft:stained_hardened_clay", {"color": _TERRACOTTA_COLORS[name]}
    if name.startswith("minecraft:") and name.endswith("_concrete"):
        color = name[len("minecraft:"):-len("_concrete")]
        return "minecraft:concrete", {"color": "silver" if color == "light_gray" else color}
    if name.startswith("minecraft:") and name.endswith("_wool"):
        color = name[len("minecraft:"):-len("_wool")]
        return "minecraft:wool", {"color": "silver" if color == "light_gray" else color}
    if name in ("minecraft:andesite", "minecraft:diorite", "minecraft:granite"):
        return "minecraft:stone", {"stone_type": name.split(":", 1)[1]}
    if name == "minecraft:stone":
        return "minecraft:stone", {"stone_type": "stone"}
    if name == "minecraft:rooted_dirt":
        return "minecraft:dirt_with_roots", props
    if name == "minecraft:moss_block":
        # The target PocketMine fork advertises the 1.18.30 palette but its
        # runtime block-state deserializer does not register moss_block. Grass
        # is the closest supported green surface and keeps woodland/jungle
        # islands readable without making the provider reject their chunks.
        return "minecraft:grass", props
    if name == "minecraft:deepslate":
        return name, {"pillar_axis": java_props.get("axis", "y")}
    if name == "minecraft:snow_block":
        return "minecraft:snow", props
    if name == "minecraft:snow":
        layers = max(1, min(8, int(java_props.get("layers", "1"))))
        return "minecraft:snow_layer", {"covered_bit": 0, "height": layers - 1}
    if name == "minecraft:water":
        return name, {"liquid_depth": 0}
    if name in ("minecraft:grass", "minecraft:short_grass"):
        return "minecraft:tallgrass", {"tall_grass_type": "tall"}
    if name == "minecraft:fern":
        return "minecraft:tallgrass", {"tall_grass_type": "fern"}
    if name == "minecraft:dead_bush":
        return "minecraft:deadbush", props
    if name == "minecraft:cactus":
        return name, {"age": 0}
    if name in _FLOWERS:
        return _FLOWERS[name]
    if name in ("minecraft:chest", "minecraft:ender_chest"):
        facing = {"north": 2, "south": 3, "west": 4, "east": 5}.get(
            java_props.get("facing", "north"), 2)
        return name, {"facing_direction": facing}

    for wood in ("oak", "spruce", "birch", "jungle", "dark_oak"):
        if name == f"minecraft:{wood}_log":
            axis = java_props.get("axis", "y")
            if wood == "dark_oak":
                return "minecraft:log2", {"new_log_type": wood, "pillar_axis": axis}
            return "minecraft:log", {"old_log_type": wood, "pillar_axis": axis}
        if name == f"minecraft:{wood}_leaves":
            persistent = 1 if java_props.get("persistent", "false") == "true" else 0
            if wood == "dark_oak":
                return "minecraft:leaves2", {
                    "new_leaf_type": wood, "persistent_bit": persistent, "update_bit": 0}
            return "minecraft:leaves", {
                "old_leaf_type": wood, "persistent_bit": persistent, "update_bit": 0}

    # Blocks introduced before or during 1.18 with no state properties use the
    # same identifier in both editions (ores, tuff, calcite, ice, etc.).
    return name, props


def _block_state_nbt(state: Tuple[str, dict]) -> bytes:
    name, states = _canonical_block_state(state)
    out = bytearray()
    _nbt_root(out)
    _nbt_string(out, "name", name)
    _nbt_compound(out, "states")
    for key in sorted(states):
        value = states[key]
        if isinstance(value, str):
            _nbt_string(out, key, value)
        elif isinstance(value, int) and key.endswith("_bit"):
            _nbt_byte(out, key, value)
        else:
            _nbt_int(out, key, int(value))
    _nbt_end(out)
    _nbt_int(out, "version", BLOCK_STATE_VERSION)
    _nbt_end(out)
    return bytes(out)


# ---------------------------------------------------------------------------
# Chunk palettes and LevelDB entries
# ---------------------------------------------------------------------------

def _bits_for_palette(size: int) -> int:
    if size <= 1:
        return 0
    if size <= 2:
        return 1
    if size <= 4:
        return 2
    if size <= 8:
        return 3
    if size <= 16:
        return 4
    if size <= 32:
        return 5
    if size <= 64:
        return 6
    if size <= 256:
        return 8
    return 16


def _pack_indices(out: bytearray, indices: Sequence[int], bits: int):
    if bits == 0:
        return
    per_word = 32 // bits
    for start in range(0, len(indices), per_word):
        word = 0
        for offset, index in enumerate(indices[start:start + per_word]):
            word |= int(index) << (offset * bits)
        _u32(out, word)


def _local_palette(values: Iterable[int]) -> Tuple[List[int], List[int]]:
    local_ids: List[int] = []
    local_map: Dict[int, int] = {}
    inverse: List[int] = []
    for raw in values:
        value = int(raw)
        local = local_map.get(value)
        if local is None:
            local = len(local_ids)
            local_map[value] = local
            local_ids.append(value)
        inverse.append(local)
    return local_ids, inverse


def _write_block_palette(out: bytearray, values: Iterable[int], block_palette):
    local_ids, inverse = _local_palette(values)
    bits = _bits_for_palette(len(local_ids))
    _l8(out, bits << 1)
    _pack_indices(out, inverse, bits)
    if bits:
        _l32(out, len(local_ids))
    for block_id in local_ids:
        out.extend(_block_state_nbt(block_palette[block_id]))


def _write_biome_palette(out: bytearray, values: Iterable[int]):
    local_ids, inverse = _local_palette(values)
    bits = _bits_for_palette(len(local_ids))
    _l8(out, bits << 1)
    _pack_indices(out, inverse, bits)
    if bits:
        _l32(out, len(local_ids))
    for biome_id in local_ids:
        _l32(out, biome_id)


def _bedrock_biome_id(name: str) -> int:
    return {
        "minecraft:the_void": 1,
        "minecraft:plains": 1,
        "minecraft:forest": 4,
        "minecraft:birch_forest": 27,
        "minecraft:dark_forest": 29,
        "minecraft:taiga": 5,
        "minecraft:jungle": 21,
        "minecraft:savanna": 35,
        "minecraft:snowy_plains": 12,
        "minecraft:snowy_taiga": 30,
        "minecraft:desert": 2,
        "minecraft:badlands": 37,
    }.get(name, 1)


def _subchunk(section: np.ndarray, section_y: int, block_palette) -> bytes:
    out = bytearray((SUBCHUNK_VERSION, 1, section_y & 0xFF))
    # Bedrock paletted arrays are XZY; NumPy storage here is YZX.
    _write_block_palette(out, section.transpose(2, 1, 0).reshape(-1), block_palette)
    return bytes(out)


def _heightmap_and_biomes(block_ids: np.ndarray, biome_ids: np.ndarray,
                          biome_palette: Sequence[str]) -> bytes:
    out = bytearray(512)
    solid = block_ids != 0
    any_solid = solid.any(axis=0)
    top_index = np.where(any_solid, WORLD_HEIGHT - 1 - np.argmax(solid[::-1], axis=0), -1)
    top_y = np.maximum(0, MIN_Y + top_index + 1).astype(np.uint16)
    out[:] = top_y.astype("<u2").tobytes(order="C")

    column_biomes = np.empty((16, 16), dtype=np.int16)
    for z in range(16):
        for x in range(16):
            column_biomes[z, x] = _bedrock_biome_id(biome_palette[int(biome_ids[z, x])])

    # v40 stores exactly 24 palettes. Each palette covers 4x4x4 biome cells;
    # repeating each column through Y matches the generator's 2D biome model.
    values = []
    for x in range(16):
        for z in range(16):
            values.extend([int(column_biomes[z, x])] * 16)
    # The generator has column biomes, so every vertical section uses the exact
    # same 4x4x4 palette. Encode it once and copy the serialized bytes instead
    # of rebuilding an identical 4096-value local palette 24 times per chunk.
    encoded_palette = bytearray()
    _write_biome_palette(encoded_palette, values)
    out.extend(encoded_palette * N_SECTIONS)
    return bytes(out)


def _block_entities_nbt(entities: Sequence[Tuple[str, int, int, int]]) -> bytes:
    out = bytearray()
    for kind, x, y, z in entities:
        _nbt_root(out)
        _nbt_string(out, "id", "Chest" if kind == "chest" else "EnderChest")
        _nbt_int(out, "x", x)
        _nbt_int(out, "y", y)
        _nbt_int(out, "z", z)
        _nbt_byte(out, "isMovable", 1)
        if kind == "chest":
            _nbt_list(out, "Items", 10, 0)
        _nbt_end(out)
    return bytes(out)


def build_chunk_entries(cx: int, cz: int, block_ids: np.ndarray, biome_ids: np.ndarray,
                        block_palette, biome_palette: Sequence[str],
                        block_entities: Sequence[Tuple[str, int, int, int]] = ()):
    base = struct.pack("<ii", cx, cz)
    entries = [
        (base + KEY_NEW_VERSION, bytes((CHUNK_VERSION,))),
        (base + KEY_FINALIZATION, b"\x02"),
        (base + KEY_HEIGHTMAP_AND_3D_BIOMES,
         _heightmap_and_biomes(block_ids, biome_ids, biome_palette)),
        (base + KEY_ENTITIES, b""),
    ]
    entity_payload = _block_entities_nbt(block_entities)
    entries.append((base + KEY_BLOCK_ENTITIES, entity_payload))

    for section_index in range(N_SECTIONS):
        section = block_ids[section_index * 16:(section_index + 1) * 16]
        if np.any(section != 0):
            section_y = MIN_SECTION + section_index
            entries.append((base + KEY_SUBCHUNK + bytes((section_y & 0xFF,)),
                            _subchunk(section, section_y, block_palette)))
    return entries


# ---------------------------------------------------------------------------
# Log-backed LevelDB store
# ---------------------------------------------------------------------------

_LOG_BLOCK_SIZE = 32768
_LOG_HEADER_SIZE = 7
_CRC_MASK_DELTA = 0xA282EAD8


def _build_crc32c_table():
    table = []
    for value in range(256):
        crc = value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
        table.append(crc & 0xFFFFFFFF)
    return tuple(table)


_CRC32C_TABLE = _build_crc32c_table()


def _varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _slice(value: bytes) -> bytes:
    return _varint(len(value)) + value


def _crc32c(data: bytes, crc=0) -> int:
    crc = (~crc) & 0xFFFFFFFF
    for value in data:
        crc = _CRC32C_TABLE[(crc ^ value) & 0xFF] ^ (crc >> 8)
    return (~crc) & 0xFFFFFFFF


def _masked_crc(record_type: int, data: bytes) -> int:
    crc = _crc32c(bytes((record_type,)))
    crc = _crc32c(data, crc)
    return (((crc >> 15) | ((crc << 17) & 0xFFFFFFFF)) + _CRC_MASK_DELTA) & 0xFFFFFFFF


def _write_log_record(fp, record: bytes, block_offset: int) -> int:
    offset = 0
    begin = True
    while offset < len(record) or (begin and not record):
        leftover = _LOG_BLOCK_SIZE - block_offset
        if leftover < _LOG_HEADER_SIZE:
            fp.write(b"\x00" * leftover)
            block_offset = 0
            leftover = _LOG_BLOCK_SIZE
        available = leftover - _LOG_HEADER_SIZE
        fragment = record[offset:offset + available]
        end = offset + len(fragment) >= len(record)
        record_type = 1 if begin and end else (2 if begin else (4 if end else 3))
        fp.write(struct.pack("<IHB", _masked_crc(record_type, fragment), len(fragment), record_type))
        fp.write(fragment)
        block_offset += _LOG_HEADER_SIZE + len(fragment)
        offset += len(fragment)
        begin = False
        if not record:
            break
    return block_offset


def _manifest_edit(count: int) -> bytes:
    return (b"\x01" + _slice(b"leveldb.BytewiseComparator") +
            b"\x02" + _varint(3) + b"\x03" + _varint(4) +
            b"\x04" + _varint(count))


def _write_batch(entries, sequence: int) -> bytes:
    out = bytearray(struct.pack("<QI", sequence, len(entries)))
    for key, value in entries:
        out.append(1)
        out.extend(_slice(key))
        out.extend(_slice(value))
    return bytes(out)


def _write_leveldb(db_dir: str, entries):
    if os.path.isdir(db_dir):
        shutil.rmtree(db_dir)
    os.makedirs(db_dir, exist_ok=True)
    entries = sorted(entries, key=lambda item: item[0])

    with open(os.path.join(db_dir, "MANIFEST-000002"), "wb") as fp:
        _write_log_record(fp, _manifest_edit(len(entries)), 0)
    with open(os.path.join(db_dir, "CURRENT"), "wb") as fp:
        fp.write(b"MANIFEST-000002\n")
    open(os.path.join(db_dir, "LOCK"), "wb").close()
    with open(os.path.join(db_dir, "LOG"), "wb") as fp:
        fp.write(b"mc-world-forge created this PocketMine-compatible LevelDB store.\n")

    with open(os.path.join(db_dir, "000003.log"), "wb") as fp:
        sequence = 1
        block_offset = 0
        begin = 0
        while begin < len(entries):
            end = begin
            approx_bytes = 12
            while (end < len(entries) and end - begin < 2048 and
                   approx_bytes < 4 * 1024 * 1024):
                approx_bytes += len(entries[end][0]) + len(entries[end][1]) + 16
                end += 1
            block_offset = _write_log_record(
                fp, _write_batch(entries[begin:end], sequence), block_offset)
            sequence += end - begin
            begin = end


def _level_dat(cfg, spawn) -> bytes:
    void_layers = ('{"biome_id":1,"block_layers":[],"encoding_version":6,'
                   '"structure_options":null,"world_version":"version.post_1_18"}')
    out = bytearray()
    _nbt_root(out)
    _nbt_int(out, "DayCycleStopTime", -1)
    _nbt_int(out, "Difficulty", 2)
    _nbt_byte(out, "ForceGameType", 0)
    _nbt_int(out, "GameType", cfg.game_type)
    _nbt_int(out, "Generator", 2)
    _nbt_long(out, "LastPlayed", int(time.time()))
    _nbt_string(out, "LevelName", cfg.name)
    _nbt_int(out, "NetworkVersion", NETWORK_VERSION)
    _nbt_long(out, "RandomSeed", cfg.seed)
    _nbt_int(out, "SpawnX", int(spawn[0]))
    _nbt_int(out, "SpawnY", int(spawn[1]))
    _nbt_int(out, "SpawnZ", int(spawn[2]))
    _nbt_int(out, "StorageVersion", STORAGE_VERSION)
    _nbt_long(out, "Time", 0)
    _nbt_byte(out, "eduLevel", 0)
    _nbt_byte(out, "falldamage", 1)
    _nbt_byte(out, "firedamage", 1)
    _nbt_byte(out, "hasBeenLoadedInCreative", 1)
    _nbt_byte(out, "immutableWorld", 0)
    _nbt_float(out, "lightningLevel", 0.0)
    _nbt_int(out, "lightningTime", 0)
    _nbt_byte(out, "pvp", 1)
    _nbt_float(out, "rainLevel", 0.0)
    _nbt_int(out, "rainTime", 0)
    _nbt_byte(out, "spawnMobs", 0)
    _nbt_byte(out, "texturePacksRequired", 0)
    _nbt_byte(out, "commandsEnabled", 1 if cfg.allow_commands else 0)
    _nbt_list(out, "lastOpenedWithVersion", 3, 4)
    for value in (1, 18, 30, 4):
        _l32(out, value)
    _nbt_string(out, "FlatWorldLayers", void_layers)
    _nbt_string(out, "generatorName", "flat")
    # PocketMine validates this legacy preset itself; one air layer remains void.
    _nbt_string(out, "generatorOptions", "2;air;1;")
    _nbt_end(out)
    return struct.pack("<II", STORAGE_VERSION, len(out)) + bytes(out)


def write_world(out_dir: str, entries, cfg, spawn):
    os.makedirs(out_dir, exist_ok=True)
    region_dir = os.path.join(out_dir, "region")
    if os.path.isdir(region_dir):
        shutil.rmtree(region_dir)
    for stale in ("session.lock",):
        path = os.path.join(out_dir, stale)
        if os.path.exists(path):
            os.remove(path)
    with open(os.path.join(out_dir, "level.dat"), "wb") as fp:
        fp.write(_level_dat(cfg, spawn))
    with open(os.path.join(out_dir, "levelname.txt"), "wb") as fp:
        fp.write(cfg.name.encode("utf-8"))
    _write_leveldb(os.path.join(out_dir, "db"), entries)
