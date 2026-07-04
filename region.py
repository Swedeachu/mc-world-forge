"""
Assemble 1.18+ chunk NBT and write Anvil (.mca) region files.

Coordinate/format facts used here (from the Minecraft Wiki):
 - A region file holds 32x32 chunks, named r.<regionX>.<regionZ>.mca where
   regionX = chunkX >> 5.
 - 8 KiB header = 4 KiB location table + 4 KiB timestamp table.
 - Each chunk payload = 4-byte big-endian length + 1 compression byte (2 = zlib)
   + zlib data, padded up to a multiple of 4096 bytes.
 - Since 1.16 the palette index array packs entries into 64-bit longs WITHOUT
   letting an entry straddle a long boundary. First entry occupies the low bits.
"""
import struct
import time
import numpy as np

import nbt


# ---------------------------------------------------------------------------
# Bit packing
# ---------------------------------------------------------------------------

def bits_for(palette_size: int, min_bits: int) -> int:
    if palette_size <= 1:
        return min_bits
    return max(min_bits, (palette_size - 1).bit_length())


def pack_indices(indices, bits: int):
    """Pack integer indices into a list of unsigned 64-bit longs (no straddle)."""
    indices = np.asarray(indices, dtype=np.uint64)
    epl = 64 // bits                       # entries per long
    n = indices.size
    n_longs = (n + epl - 1) // epl
    pad = n_longs * epl - n
    if pad:
        indices = np.concatenate([indices, np.zeros(pad, dtype=np.uint64)])
    grid = indices.reshape(n_longs, epl)
    out = np.zeros(n_longs, dtype=np.uint64)
    b = np.uint64(bits)
    for j in range(epl):
        out |= grid[:, j] << (np.uint64(j) * b)
    return out.view(np.int64)


# ---------------------------------------------------------------------------
# Chunk assembly
# ---------------------------------------------------------------------------

def _block_state_tag(state):
    """state is (name, props_dict). Build the palette compound."""
    name, props = state
    comp = nbt.Compound()
    comp["Name"] = nbt.String(name)
    if props:
        p = nbt.Compound()
        for k, v in props.items():
            p[k] = nbt.String(str(v))
        comp["Properties"] = p
    return comp


def build_chunk(cx, cz, block_ids, biome_ids, block_palette, biome_palette,
                data_version, min_section, n_sections):
    """
    block_ids : int16 ndarray (world_height, 16, 16) indexed [y, z, x] holding
                global ids into `block_palette` (list of (name, props)).
    biome_ids : int16 ndarray (16, 16) indexed [z, x] holding global ids into
                `biome_palette` (list of biome name strings). Horizontal only.
    """
    world_height = block_ids.shape[0]
    min_y = min_section * 16

    sections = []
    for s in range(n_sections):
        sec_y = min_section + s
        y0 = s * 16
        blk = block_ids[y0:y0 + 16]            # (16, 16, 16) [y, z, x]

        # --- block_states ------------------------------------------------
        local_ids, inverse = np.unique(blk, return_inverse=True)
        inverse = inverse.reshape(-1)          # YZX order because axes are y,z,x
        sec = nbt.Compound()
        sec["Y"] = nbt.Byte(sec_y)

        bs = nbt.Compound()
        bs["palette"] = nbt.List(nbt.Compound.tag_id,
                                 [_block_state_tag(block_palette[i]) for i in local_ids])
        if len(local_ids) > 1:
            bits = bits_for(len(local_ids), 4)
            bs["data"] = nbt.LongArray(pack_indices(inverse.astype(np.uint64), bits))
        sec["block_states"] = bs

        # --- biomes (4x4x4 cells, horizontal only) -----------------------
        # Downsample the 16x16 column biome map to a 4x4 grid (cell centre).
        cell = biome_ids[1::4, 1::4]           # (4, 4) [z, x]
        local_b, inv_b = np.unique(cell, return_inverse=True)
        bio = nbt.Compound()
        bio["palette"] = nbt.List(nbt.String.tag_id,
                                  [nbt.String(biome_palette[i]) for i in local_b])
        if len(local_b) > 1:
            inv_b = inv_b.reshape(4, 4)          # [z, x]
            # replicate across 4 y-layers -> YZX order (y*4 + z)*4 + x
            full = np.broadcast_to(inv_b, (4, 4, 4)).reshape(-1)
            bits_b = bits_for(len(local_b), 1)
            bio["data"] = nbt.LongArray(pack_indices(full.astype(np.uint64), bits_b))
        sec["biomes"] = bio

        sections.append(sec)

    # --- heightmaps (MOTION_BLOCKING / WORLD_SURFACE) --------------------
    solid = block_ids != 0                      # id 0 is always air (see generator)
    # highest solid y per column, else -1
    any_solid = solid.any(axis=0)
    top = np.where(any_solid,
                   world_height - 1 - np.argmax(solid[::-1], axis=0),
                   -1)                           # (z, x)
    hm_vals = np.clip(top + 1, 0, world_height)  # height above world bottom
    hm_order = hm_vals.reshape(-1)               # z*16 + x order
    hm_bits = (world_height).bit_length()        # 384 -> 9 bits
    hm_packed = nbt.LongArray(pack_indices(hm_order.astype(np.uint64), hm_bits))

    heightmaps = nbt.Compound()
    heightmaps["MOTION_BLOCKING"] = hm_packed
    heightmaps["WORLD_SURFACE"] = hm_packed

    # --- chunk root -----------------------------------------------------
    empty_list = nbt.List(0, [])                 # TAG_End typed empty list
    structures = nbt.Compound()
    structures["starts"] = nbt.Compound()
    structures["References"] = nbt.Compound()

    root = nbt.Compound()
    root["DataVersion"] = nbt.Int(data_version)
    root["xPos"] = nbt.Int(cx)
    root["zPos"] = nbt.Int(cz)
    root["yPos"] = nbt.Int(min_section)
    root["Status"] = nbt.String("minecraft:full")
    root["LastUpdate"] = nbt.Long(0)
    root["InhabitedTime"] = nbt.Long(0)
    root["sections"] = nbt.List(nbt.Compound.tag_id, sections)
    root["block_entities"] = empty_list
    root["block_ticks"] = empty_list
    root["fluid_ticks"] = empty_list
    root["PostProcessing"] = nbt.List(nbt.List.tag_id,
                                      [nbt.List(0, []) for _ in range(n_sections)])
    root["Heightmaps"] = heightmaps
    root["structures"] = structures
    root["isLightOn"] = nbt.Byte(0)              # force the game to (re)light
    return root


# ---------------------------------------------------------------------------
# Region file
# ---------------------------------------------------------------------------

SECTOR = 4096


class RegionWriter:
    """Collects compressed chunks for a single r.x.z.mca and serialises them."""

    def __init__(self, region_x, region_z):
        self.region_x = region_x
        self.region_z = region_z
        self.chunks = {}   # (local_x, local_z) -> compressed bytes (scheme 2)

    def add_chunk(self, chunk_x, chunk_z, root_compound):
        payload = nbt.to_zlib("", root_compound)
        self.add_compressed_chunk(chunk_x, chunk_z, payload)

    def add_compressed_chunk(self, chunk_x, chunk_z, payload):
        lx = chunk_x & 31
        lz = chunk_z & 31
        self.chunks[(lx, lz)] = payload

    def to_bytes(self) -> bytes:
        locations = bytearray(SECTOR)
        timestamps = bytearray(SECTOR)
        body = bytearray()
        now = int(time.time())
        next_sector = 2  # sectors 0 and 1 are the header

        for (lx, lz), payload in self.chunks.items():
            frame = bytearray()
            frame += struct.pack(">i", len(payload) + 1)  # length incl. scheme byte
            frame += bytes([2])                            # zlib
            frame += payload
            # pad frame to a whole number of sectors
            pad = (-len(frame)) % SECTOR
            frame += b"\x00" * pad
            sector_count = len(frame) // SECTOR

            idx = 4 * (lx + lz * 32)
            locations[idx] = (next_sector >> 16) & 0xFF
            locations[idx + 1] = (next_sector >> 8) & 0xFF
            locations[idx + 2] = next_sector & 0xFF
            locations[idx + 3] = sector_count & 0xFF
            struct.pack_into(">i", timestamps, idx, now)

            body += frame
            next_sector += sector_count

        return bytes(locations) + bytes(timestamps) + bytes(body)

    @property
    def filename(self):
        return f"r.{self.region_x}.{self.region_z}.mca"
