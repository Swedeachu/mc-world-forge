"""Decode exact block values at world coordinates to confirm structural
invariants: bedrock wall ring, y=floor bedrock floor, void below floor,
diamonds concentrated at/below y=5. Independent of the generator internals.

Usage: python spatial_check.py <world_dir> [size] [floor_y] [wall_min] [wall_max]
"""
import struct
import zlib
import sys
import os

from verify import parse_nbt_bytes  # reuse the independent NBT reader

MIN_Y = -64


def load_region(path):
    with open(path, "rb") as f:
        data = f.read()
    chunks = {}
    for i in range(1024):
        off = struct.unpack_from(">I", data, i * 4)[0]
        sector = off >> 8
        if sector == 0:
            continue
        start = sector * 4096
        length = struct.unpack_from(">i", data, start)[0]
        scheme = data[start + 4]
        raw = data[start + 5:start + 5 + length - 1]
        nbt = parse_nbt_bytes(zlib.decompress(raw) if scheme == 2 else raw)
        chunks[(nbt["xPos"], nbt["zPos"])] = nbt
    return chunks


class World:
    def __init__(self, world_dir):
        self.chunks = {}
        rdir = os.path.join(world_dir, "region")
        for fn in os.listdir(rdir):
            self.chunks.update(load_region(os.path.join(rdir, fn)))

    def _unpack_section(self, sec):
        pal = [p["Name"] for p in sec["block_states"]["palette"]]
        data = sec["block_states"].get("data")
        if data is None:
            return [0] * 4096, pal  # single-block section: all index 0
        bits = max(4, (len(pal) - 1).bit_length())
        per = 64 // bits
        mask = (1 << bits) - 1
        out = []
        for lng in data:
            u = lng & 0xFFFFFFFFFFFFFFFF
            for k in range(per):
                if len(out) >= 4096:
                    break
                out.append((u >> (k * bits)) & mask)
        return out, pal

    def block(self, x, y, z):
        cx, cz = x >> 4, z >> 4
        ch = self.chunks.get((cx, cz))
        if ch is None:
            return None  # ungenerated -> void
        sy = y >> 4
        sec = next((s for s in ch["sections"] if s["Y"] == sy), None)
        if sec is None or "palette" not in sec["block_states"]:
            return "minecraft:air"
        idxs, pal = self._unpack_section(sec)
        lx, ly, lz = x & 15, y & 15, z & 15
        return pal[idxs[ly * 256 + lz * 16 + lx]]


def run(world_dir, size, floor_y, wall_min, wall_max):
    w = World(world_dir)
    xmin = -(size // 2)
    xmax = xmin + size - 1
    fails = []

    def check(cond, msg):
        print(("  ok " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    print("== spatial checks ==")
    for (bx, bz, label) in [(xmin, 0, "west edge"), (xmax, 0, "east edge"),
                            (0, xmin, "north edge"), (0, xmax, "south edge"),
                            (xmin, xmin, "corner")]:
        col_ok = all(w.block(bx, y, bz) == "minecraft:bedrock"
                     for y in range(wall_min, wall_max + 1, 8))
        check(col_ok, f"wall column at {label} ({bx},{bz}) solid bedrock "
                      f"y={wall_min}..{wall_max}")

    inside_col = [w.block(xmin + 1, y, 0) for y in range(80, 140)]
    check(any(b == "minecraft:air" for b in inside_col),
          "column one block inside wall has air above terrain")

    for (sx, sz) in [(0, 0), (10, -7), (-20, 15)]:
        check(w.block(sx, floor_y, sz) == "minecraft:bedrock",
              f"floor at ({sx},{floor_y},{sz}) is bedrock")
        below = w.block(sx, floor_y - 1, sz)
        check(below in ("minecraft:air", None),
              f"below floor at ({sx},{floor_y-1},{sz}) is void (got {below})")
        check(w.block(sx, MIN_Y, sz) in ("minecraft:air", None),
              f"world bottom y={MIN_Y} at ({sx},{sz}) is void")

    lo = hi = 0
    for ch in w.chunks.values():
        if not (xmin // 16 < ch["xPos"] < xmax // 16):
            continue
        for sec in ch["sections"]:
            if sec["Y"] > 8:
                continue
            names = [p["Name"] for p in sec["block_states"]["palette"]]
            if "minecraft:diamond_ore" not in names:
                continue
            idxs, pal = w._unpack_section(sec)
            di = pal.index("minecraft:diamond_ore")
            for li, v in enumerate(idxs):
                if v == di:
                    wy = sec["Y"] * 16 + (li // 256)
                    if wy <= 5:
                        lo += 1
                    else:
                        hi += 1
    print(f"  diamond blocks: y<=5 -> {lo}, y 6..8 -> {hi}")
    check(lo > 0, "diamonds present at or below y=5")
    check(lo >= hi, "diamonds concentrated at/below y=5")

    print("SPATIAL", "PASSED" if not fails else f"FAILED ({len(fails)})")
    return not fails


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/world_small"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 160
    floor = int(sys.argv[3]) if len(sys.argv) > 3 else -20
    wmin = int(sys.argv[4]) if len(sys.argv) > 4 else -64
    wmax = int(sys.argv[5]) if len(sys.argv) > 5 else 319
    run(d, size, floor, wmin, wmax)
