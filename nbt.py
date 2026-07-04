"""
Minimal Named Binary Tag (NBT) writer for Minecraft Java Edition.

Only the *writing* side is implemented because this tool only produces worlds.
Big-endian, signed integers, per the Java Edition NBT spec.

Tag type IDs:
    0 End, 1 Byte, 2 Short, 3 Int, 4 Long, 5 Float, 6 Double,
    7 ByteArray, 8 String, 9 List, 10 Compound, 11 IntArray, 12 LongArray
"""
import struct
import gzip
import zlib

# --- Tag wrapper types -------------------------------------------------------
# Python's native int/float/str/list/dict are ambiguous about their NBT type,
# so we wrap values in tiny marker classes. `Compound` is a plain dict subclass
# and `String` is just `str`, for ergonomic tree construction.


class _Tag:
    __slots__ = ("value",)
    tag_id = 0

    def __init__(self, value):
        self.value = value


class Byte(_Tag):
    tag_id = 1


class Short(_Tag):
    tag_id = 2


class Int(_Tag):
    tag_id = 3


class Long(_Tag):
    tag_id = 4


class Float(_Tag):
    tag_id = 5


class Double(_Tag):
    tag_id = 6


class ByteArray(_Tag):
    tag_id = 7


class IntArray(_Tag):
    tag_id = 11


class LongArray(_Tag):
    tag_id = 12


class String(str):
    tag_id = 8


class List:
    """An NBT list. All elements must be the same tag type."""
    tag_id = 9

    def __init__(self, item_type_id, items):
        self.item_type_id = item_type_id
        self.items = items


class Compound(dict):
    tag_id = 10


# --- Encoding ----------------------------------------------------------------

def _tag_id_of(value):
    if isinstance(value, bool):
        # bool is a subclass of int; treat as Byte (0/1)
        return Byte.tag_id
    if isinstance(value, _Tag):
        return value.tag_id
    if isinstance(value, String):
        return String.tag_id
    if isinstance(value, str):
        return String.tag_id
    if isinstance(value, List):
        return List.tag_id
    if isinstance(value, Compound):
        return Compound.tag_id
    if isinstance(value, dict):
        return Compound.tag_id
    raise TypeError(f"Cannot infer NBT tag id for {type(value)}")


def _write_payload(out: bytearray, value):
    """Write the payload (no type byte, no name) of a single tag."""
    if isinstance(value, bool):
        out += struct.pack(">b", 1 if value else 0)
        return
    if isinstance(value, Byte):
        out += struct.pack(">b", int(value.value))
    elif isinstance(value, Short):
        out += struct.pack(">h", int(value.value))
    elif isinstance(value, Int):
        out += struct.pack(">i", int(value.value))
    elif isinstance(value, Long):
        out += struct.pack(">q", _to_signed64(int(value.value)))
    elif isinstance(value, Float):
        out += struct.pack(">f", float(value.value))
    elif isinstance(value, Double):
        out += struct.pack(">d", float(value.value))
    elif isinstance(value, ByteArray):
        data = bytes(bytearray((b & 0xFF) for b in value.value))
        out += struct.pack(">i", len(data))
        out += data
    elif isinstance(value, IntArray):
        arr = value.value
        out += struct.pack(">i", len(arr))
        out += struct.pack(">%di" % len(arr), *[_to_signed32(int(v)) for v in arr])
    elif isinstance(value, LongArray):
        arr = value.value
        out += struct.pack(">i", len(arr))
        out += struct.pack(">%dq" % len(arr), *[_to_signed64(int(v)) for v in arr])
    elif isinstance(value, String) or isinstance(value, str):
        data = str(value).encode("utf-8")
        out += struct.pack(">H", len(data))
        out += data
    elif isinstance(value, List):
        out += struct.pack(">b", value.item_type_id)
        out += struct.pack(">i", len(value.items))
        for item in value.items:
            _write_payload(out, item)
    elif isinstance(value, (Compound, dict)):
        for name, child in value.items():
            tid = _tag_id_of(child)
            out += struct.pack(">b", tid)
            nb = name.encode("utf-8")
            out += struct.pack(">H", len(nb))
            out += nb
            _write_payload(out, child)
        out += b"\x00"  # End tag
    else:
        raise TypeError(f"Cannot encode NBT payload for {type(value)}")


def _to_signed64(v):
    v &= 0xFFFFFFFFFFFFFFFF
    if v >= 0x8000000000000000:
        v -= 0x10000000000000000
    return v


def _to_signed32(v):
    v &= 0xFFFFFFFF
    if v >= 0x80000000:
        v -= 0x100000000
    return v


def write_named_compound(root_name: str, compound) -> bytes:
    """Serialize a root compound with the given (usually empty) name."""
    out = bytearray()
    out += struct.pack(">b", Compound.tag_id)
    nb = root_name.encode("utf-8")
    out += struct.pack(">H", len(nb))
    out += nb
    _write_payload(out, compound)
    return bytes(out)


def to_gzip(root_name: str, compound) -> bytes:
    """level.dat and .dat files are gzip-compressed NBT."""
    return gzip.compress(write_named_compound(root_name, compound))


def to_zlib(root_name: str, compound) -> bytes:
    """Region-file chunks use zlib (compression scheme 2)."""
    return zlib.compress(write_named_compound(root_name, compound))
