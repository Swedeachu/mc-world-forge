#include "mcwf/nbt.hpp"

#include <algorithm>
#include <string>
#include <utility>

#include "mcwf/blocks.hpp"
#include "mcwf/chunk.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/util.hpp"

void putU8(std::vector<uint8_t>& out, uint8_t v) {
    out.push_back(v);
}

void putI16(std::vector<uint8_t>& out, int v) {
    out.push_back(static_cast<uint8_t>((v >> 8) & 0xff));
    out.push_back(static_cast<uint8_t>(v & 0xff));
}

void putI32(std::vector<uint8_t>& out, int32_t v) {
    uint32_t u = static_cast<uint32_t>(v);
    out.push_back(static_cast<uint8_t>((u >> 24) & 0xff));
    out.push_back(static_cast<uint8_t>((u >> 16) & 0xff));
    out.push_back(static_cast<uint8_t>((u >> 8) & 0xff));
    out.push_back(static_cast<uint8_t>(u & 0xff));
}

void putI64(std::vector<uint8_t>& out, uint64_t v) {
    for (int i = 7; i >= 0; --i) {
        out.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xff));
    }
}

void putStringPayload(std::vector<uint8_t>& out, const std::string& s) {
    putI16(out, static_cast<int>(s.size()));
    out.insert(out.end(), s.begin(), s.end());
}

void putTagHeader(std::vector<uint8_t>& out, uint8_t type, const std::string& name) {
    putU8(out, type);
    putStringPayload(out, name);
}

void nbtByte(std::vector<uint8_t>& out, const std::string& name, int v) {
    putTagHeader(out, 1, name);
    putU8(out, static_cast<uint8_t>(v & 0xff));
}

void nbtInt(std::vector<uint8_t>& out, const std::string& name, int32_t v) {
    putTagHeader(out, 3, name);
    putI32(out, v);
}

void nbtLong(std::vector<uint8_t>& out, const std::string& name, uint64_t v) {
    putTagHeader(out, 4, name);
    putI64(out, v);
}

void nbtString(std::vector<uint8_t>& out, const std::string& name, const std::string& v) {
    putTagHeader(out, 8, name);
    putStringPayload(out, v);
}

void nbtBeginList(std::vector<uint8_t>& out, const std::string& name, uint8_t itemType, int32_t count) {
    putTagHeader(out, 9, name);
    putU8(out, itemType);
    putI32(out, count);
}

void nbtListPayloadHeader(std::vector<uint8_t>& out, uint8_t itemType, int32_t count) {
    putU8(out, itemType);
    putI32(out, count);
}

void nbtBeginCompound(std::vector<uint8_t>& out, const std::string& name) {
    putTagHeader(out, 10, name);
}

void nbtEndCompound(std::vector<uint8_t>& out) {
    putU8(out, 0);
}

void nbtLongArray(std::vector<uint8_t>& out, const std::string& name, const std::vector<uint64_t>& arr) {
    putTagHeader(out, 12, name);
    putI32(out, static_cast<int32_t>(arr.size()));

    for (uint64_t v : arr) {
        putI64(out, v);
    }
}

int bitsFor(int paletteSize, int minBits) {
    if (paletteSize <= 1) {
        return minBits;
    }
    int bits = 0;
    int v = paletteSize - 1;

    while (v > 0) {
        ++bits;
        v >>= 1;
    }

    return std::max(minBits, bits);
}

std::vector<uint64_t> packIndices(const std::vector<uint32_t>& indices, int bits) {
    int epl = 64 / bits;
    int nLongs = static_cast<int>((indices.size() + epl - 1) / epl);
    std::vector<uint64_t> out(static_cast<size_t>(nLongs), 0);

    for (size_t i = 0; i < indices.size(); ++i) {
        int li = static_cast<int>(i / epl);
        int off = static_cast<int>(i % epl) * bits;
        out[static_cast<size_t>(li)] |= static_cast<uint64_t>(indices[i]) << off;
    }

    return out;
}

void writeBlockPaletteEntry(std::vector<uint8_t>& out, const std::string& rawName) {
    std::string name = rawName;
    std::string props;
    size_t pipe = rawName.find('|');
    if (pipe != std::string::npos) {
        name = rawName.substr(0, pipe);
        props = rawName.substr(pipe + 1);
    }

    nbtString(out, "Name", name);
    if (!props.empty()) {
        std::vector<std::pair<std::string, std::string>> values;

        for (const std::string& part : split(props, ',')) {
            size_t eq = part.find('=');
            if (eq == std::string::npos) {
                continue;
            }
            std::string key = trim(part.substr(0, eq));
            std::string value = trim(part.substr(eq + 1));
            if (!key.empty() && !value.empty()) {
                values.push_back({key, value});
            }
        }
        if (!values.empty()) {
            nbtBeginCompound(out, "Properties");

            for (const auto& kv : values) {
                nbtString(out, kv.first, kv.second);
            }
            nbtEndCompound(out);
        }
    }
    nbtEndCompound(out);
}

std::vector<uint8_t> buildChunkNbt(const Config& cfg, const Blocks& blocks, int cx, int cz,
                                          const std::vector<uint16_t>& blockIds,
                                          const std::vector<uint16_t>& biomeIds) {
    std::vector<uint8_t> out;
    nbtBeginCompound(out, "");
    nbtInt(out, "DataVersion", cfg.dataVersion);
    nbtInt(out, "xPos", cx);
    nbtInt(out, "zPos", cz);
    nbtInt(out, "yPos", MIN_SECTION);
    nbtString(out, "Status", "minecraft:full");
    nbtLong(out, "LastUpdate", 0);
    nbtLong(out, "InhabitedTime", 0);

    nbtBeginList(out, "sections", 10, N_SECTIONS);

    for (int s = 0; s < N_SECTIONS; ++s) {
        int secY = MIN_SECTION + s;
        nbtByte(out, "Y", secY);

        std::vector<int32_t> localMap(blocks.names.size(), -1);
        std::vector<uint16_t> localIds;
        std::vector<uint32_t> inverse;
        inverse.reserve(4096);

        for (int y = 0; y < 16; ++y) {
            int gy = s * 16 + y;

            for (int z = 0; z < 16; ++z) {
                for (int x = 0; x < 16; ++x) {
                    uint16_t gid = blockIds[static_cast<size_t>(blockIndex(gy, z, x))];
                    int32_t lid = localMap[static_cast<size_t>(gid)];
                    if (lid < 0) {
                        lid = static_cast<int32_t>(localIds.size());
                        localMap[static_cast<size_t>(gid)] = lid;
                        localIds.push_back(gid);
                    }
                    inverse.push_back(static_cast<uint32_t>(lid));
                }
            }
        }

        nbtBeginCompound(out, "block_states");
        nbtBeginList(out, "palette", 10, static_cast<int32_t>(localIds.size()));

        for (uint16_t gid : localIds) {
            writeBlockPaletteEntry(out, blocks.names[static_cast<size_t>(gid)]);
        }
        if (localIds.size() > 1) {
            nbtLongArray(out, "data", packIndices(inverse, bitsFor(static_cast<int>(localIds.size()), 4)));
        }
        nbtEndCompound(out);

        std::vector<int32_t> localBioMap(BIOMES.size(), -1);
        std::vector<uint16_t> localBioIds;
        std::vector<uint32_t> bioInverse;
        bioInverse.reserve(64);

        for (int by = 0; by < 4; ++by) {
            (void)by;

            for (int bz = 0; bz < 4; ++bz) {
                for (int bx = 0; bx < 4; ++bx) {
                    int sx = bx * 4 + 1;
                    int sz = bz * 4 + 1;
                    uint16_t gid = biomeIds[static_cast<size_t>(sz * 16 + sx)];
                    int32_t lid = localBioMap[static_cast<size_t>(gid)];
                    if (lid < 0) {
                        lid = static_cast<int32_t>(localBioIds.size());
                        localBioMap[static_cast<size_t>(gid)] = lid;
                        localBioIds.push_back(gid);
                    }
                    bioInverse.push_back(static_cast<uint32_t>(lid));
                }
            }
        }

        nbtBeginCompound(out, "biomes");
        nbtBeginList(out, "palette", 8, static_cast<int32_t>(localBioIds.size()));

        for (uint16_t gid : localBioIds) {
            putStringPayload(out, BIOMES[static_cast<size_t>(gid)]);
        }
        if (localBioIds.size() > 1) {
            nbtLongArray(out, "data", packIndices(bioInverse, bitsFor(static_cast<int>(localBioIds.size()), 1)));
        }
        nbtEndCompound(out);

        nbtEndCompound(out);
    }

    std::vector<uint32_t> hm;
    hm.reserve(256);

    for (int z = 0; z < 16; ++z) {
        for (int x = 0; x < 16; ++x) {
            int top = -1;

            for (int gy = WORLD_HEIGHT - 1; gy >= 0; --gy) {
                if (blockIds[static_cast<size_t>(blockIndex(gy, z, x))] != 0) {
                    top = gy;
                    break;
                }
            }
            hm.push_back(static_cast<uint32_t>(std::max(0, top + 1)));
        }
    }
    std::vector<uint64_t> hmPacked = packIndices(hm, 9);

    nbtBeginList(out, "block_entities", 0, 0);
    nbtBeginList(out, "block_ticks", 0, 0);
    nbtBeginList(out, "fluid_ticks", 0, 0);
    nbtBeginList(out, "PostProcessing", 9, N_SECTIONS);

    for (int i = 0; i < N_SECTIONS; ++i) {
        nbtListPayloadHeader(out, 0, 0);
    }
    nbtBeginCompound(out, "Heightmaps");
    nbtLongArray(out, "MOTION_BLOCKING", hmPacked);
    nbtLongArray(out, "WORLD_SURFACE", hmPacked);
    nbtEndCompound(out);

    nbtBeginCompound(out, "structures");
    nbtBeginCompound(out, "starts");
    nbtEndCompound(out);
    nbtBeginCompound(out, "References");
    nbtEndCompound(out);
    nbtEndCompound(out);

    nbtByte(out, "isLightOn", 0);
    nbtEndCompound(out);

    return out;
}

