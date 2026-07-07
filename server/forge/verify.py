"""Self-test: parse generated region files and level.dat to confirm validity.

This does NOT need Minecraft. It re-reads what we wrote using an independent
NBT parser and checks structural invariants.
"""
import struct
import zlib
import gzip
import sys
import os

TAGS = {0: "End", 1: "Byte", 2: "Short", 3: "Int", 4: "Long", 5: "Float",
        6: "Double", 7: "ByteArray", 8: "String", 9: "List", 10: "Compound",
        11: "IntArray", 12: "LongArray"}


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def r(self, fmt, n):
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += n
        return v

    def byte(self): return self.r(">b", 1)[0]
    def ubyte(self): return self.r(">B", 1)[0]
    def short(self): return self.r(">h", 2)[0]
    def ushort(self): return self.r(">H", 2)[0]
    def int(self): return self.r(">i", 4)[0]
    def long(self): return self.r(">q", 8)[0]
    def flt(self): return self.r(">f", 4)[0]
    def dbl(self): return self.r(">d", 8)[0]

    def string(self):
        n = self.ushort()
        s = self.d[self.p:self.p + n].decode("utf-8")
        self.p += n
        return s

    def payload(self, tid):
        if tid == 1: return self.byte()
        if tid == 2: return self.short()
        if tid == 3: return self.int()
        if tid == 4: return self.long()
        if tid == 5: return self.flt()
        if tid == 6: return self.dbl()
        if tid == 7:
            n = self.int(); v = self.d[self.p:self.p + n]; self.p += n; return v
        if tid == 8: return self.string()
        if tid == 9:
            it = self.byte(); n = self.int()
            return [self.payload(it) for _ in range(n)]
        if tid == 10:
            comp = {}
            while True:
                t = self.ubyte()
                if t == 0:
                    break
                name = self.string()
                comp[name] = self.payload(t)
            return comp
        if tid == 11:
            n = self.int(); v = [self.int() for _ in range(n)]; return v
        if tid == 12:
            n = self.int(); v = [self.long() for _ in range(n)]; return v
        raise ValueError(f"bad tag {tid} at {self.p}")

    def root(self):
        t = self.ubyte()
        assert t == 10, f"root not compound: {t}"
        self.string()  # name
        return self.payload(10)


def parse_nbt_bytes(data):
    return Reader(data).root()


def read_region(path):
    with open(path, "rb") as f:
        data = f.read()
    chunks = []
    for i in range(1024):
        off = struct.unpack_from(">I", data, i * 4)[0]
        sector = off >> 8
        count = off & 0xFF
        if sector == 0:
            continue
        start = sector * 4096
        length = struct.unpack_from(">i", data, start)[0]
        scheme = data[start + 4]
        raw = data[start + 5:start + 5 + length - 1]
        if scheme == 2:
            nbt_bytes = zlib.decompress(raw)
        elif scheme == 1:
            nbt_bytes = gzip.decompress(raw)
        else:
            nbt_bytes = raw
        chunks.append((i, count, parse_nbt_bytes(nbt_bytes)))
    return chunks


def validate(world_dir):
    print(f"== validating {world_dir} ==")
    # level.dat
    with open(os.path.join(world_dir, "level.dat"), "rb") as f:
        lvl = parse_nbt_bytes(gzip.decompress(f.read()))
    D = lvl["Data"]
    assert "WorldGenSettings" in D, "missing WorldGenSettings"
    assert "minecraft:overworld" in D["WorldGenSettings"]["dimensions"]
    print("level.dat OK  LevelName=%r DataVersion=%d spawn=(%d,%d,%d) gen=%s" % (
        D["LevelName"], D["DataVersion"], D["SpawnX"], D["SpawnY"], D["SpawnZ"],
        D["WorldGenSettings"]["dimensions"]["minecraft:overworld"]["generator"]["type"]))

    rdir = os.path.join(world_dir, "region")
    total_chunks = 0
    seen_blocks = set()
    diamonds = 0
    sample_printed = False
    for fn in sorted(os.listdir(rdir)):
        chunks = read_region(os.path.join(rdir, fn))
        total_chunks += len(chunks)
        for idx, count, ch in chunks:
            assert ch["Status"] == "minecraft:full"
            assert ch["yPos"] == -4
            secs = ch["sections"]
            assert len(secs) == 24, f"sections={len(secs)}"
            for s in secs:
                for st in s["block_states"]["palette"]:
                    seen_blocks.add(st["Name"])
                    if st["Name"] == "minecraft:diamond_ore":
                        diamonds += 1
            if not sample_printed:
                # print palette of a mid section
                names = [st["Name"] for st in secs[8]["block_states"]["palette"]]
                print("sample chunk (%d,%d) section#8 palette: %s" % (
                    ch["xPos"], ch["zPos"], names[:12]))
                assert "Heightmaps" in ch and "MOTION_BLOCKING" in ch["Heightmaps"]
                assert ch["isLightOn"] == 0
                sample_printed = True
    print(f"regions parsed OK, chunks={total_chunks}")
    print(f"distinct blocks seen: {len(seen_blocks)}")
    must = {"minecraft:air", "minecraft:bedrock", "minecraft:stone",
            "minecraft:grass_block", "minecraft:diamond_ore"}
    # water is optional: small/dry worlds may legitimately have none
    if "minecraft:water" not in seen_blocks:
        print("note: no water in this world (ok for small/dry seeds)")
    missing = must - seen_blocks
    print("required blocks present:", "YES" if not missing else f"MISSING {missing}")
    print("sections containing diamond_ore palette entry:", diamonds)
    print("all blocks:", sorted(seen_blocks))
    return not missing


if __name__ == "__main__":
    ok = validate(sys.argv[1] if len(sys.argv) > 1 else "/tmp/world_small")
    print("VALIDATION", "PASSED" if ok else "FAILED")
