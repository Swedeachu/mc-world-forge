#include "mcwf/bedrock.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <ctime>
#include <cstring>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>

#include "mcwf/chunk.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/leveldb_log.hpp"
#include "mcwf/terrain.hpp"
#include "mcwf/util.hpp"

namespace {

constexpr uint8_t BEDROCK_CHUNK_VERSION_1_18_30 = 40;
constexpr uint8_t BEDROCK_SUBCHUNK_VERSION = 9;
constexpr uint8_t BEDROCK_FINALIZATION_DONE = 2;
constexpr int BEDROCK_STORAGE_VERSION = 10;
constexpr int BEDROCK_PROTOCOL_1_18_30 = 503;
constexpr int BEDROCK_BLOCK_STATE_VERSION_1_18_30 = 17959425;

constexpr char KEY_HEIGHTMAP_AND_3D_BIOMES = '\x2b';
constexpr char KEY_NEW_VERSION = '\x2c';
constexpr char KEY_LEGACY_VERSION = '\x76';
constexpr char KEY_SUBCHUNK = '\x2f';
constexpr char KEY_BLOCK_ENTITIES = '\x31';
constexpr char KEY_ENTITIES = '\x32';
constexpr char KEY_FINALIZATION = '\x36';

void putL8(std::vector<uint8_t>& out, uint8_t v) {
    out.push_back(v);
}

void putL16(std::vector<uint8_t>& out, int v) {
    out.push_back(static_cast<uint8_t>(v & 0xff));
    out.push_back(static_cast<uint8_t>((v >> 8) & 0xff));
}

void putL32(std::vector<uint8_t>& out, int32_t v) {
    uint32_t u = static_cast<uint32_t>(v);
    out.push_back(static_cast<uint8_t>(u & 0xff));
    out.push_back(static_cast<uint8_t>((u >> 8) & 0xff));
    out.push_back(static_cast<uint8_t>((u >> 16) & 0xff));
    out.push_back(static_cast<uint8_t>((u >> 24) & 0xff));
}

void putL64(std::vector<uint8_t>& out, int64_t v) {
    uint64_t u = static_cast<uint64_t>(v);
    for (int i = 0; i < 8; ++i) {
        out.push_back(static_cast<uint8_t>((u >> (i * 8)) & 0xff));
    }
}

void putLFloat(std::vector<uint8_t>& out, float v) {
    uint32_t u;
    static_assert(sizeof(float) == sizeof(uint32_t), "float size mismatch");
    std::memcpy(&u, &v, sizeof(uint32_t));
    putL32(out, static_cast<int32_t>(u));
}

void putStringPayload(std::vector<uint8_t>& out, const std::string& s) {
    putL16(out, static_cast<int>(s.size()));
    out.insert(out.end(), s.begin(), s.end());
}

void putTagHeader(std::vector<uint8_t>& out, uint8_t type, const std::string& name) {
    putL8(out, type);
    putStringPayload(out, name);
}

void nbtByte(std::vector<uint8_t>& out, const std::string& name, int v) {
    putTagHeader(out, 1, name);
    putL8(out, static_cast<uint8_t>(v & 0xff));
}

void nbtInt(std::vector<uint8_t>& out, const std::string& name, int32_t v) {
    putTagHeader(out, 3, name);
    putL32(out, v);
}

void nbtLong(std::vector<uint8_t>& out, const std::string& name, int64_t v) {
    putTagHeader(out, 4, name);
    putL64(out, v);
}

void nbtFloat(std::vector<uint8_t>& out, const std::string& name, float v) {
    putTagHeader(out, 5, name);
    putLFloat(out, v);
}

void nbtString(std::vector<uint8_t>& out, const std::string& name, const std::string& v) {
    putTagHeader(out, 8, name);
    putStringPayload(out, v);
}

void nbtBeginList(std::vector<uint8_t>& out, const std::string& name, uint8_t itemType, int32_t count) {
    putTagHeader(out, 9, name);
    putL8(out, itemType);
    putL32(out, count);
}

void nbtBeginCompound(std::vector<uint8_t>& out, const std::string& name) {
    putTagHeader(out, 10, name);
}

void nbtEndCompound(std::vector<uint8_t>& out) {
    putL8(out, 0);
}

void writeRootCompoundStart(std::vector<uint8_t>& out) {
    putL8(out, 10);
    putStringPayload(out, "");
}

std::string baseBlockName(const std::string& rawName) {
    std::string name = rawName;
    size_t pipe = name.find('|');
    if (pipe != std::string::npos) {
        name = name.substr(0, pipe);
    }

    return name;
}

bool hasBlockProperty(const std::string& rawName, const std::string& prop) {
    size_t pipe = rawName.find('|');
    if (pipe == std::string::npos) {
        return false;
    }

    return rawName.find(prop, pipe + 1) != std::string::npos;
}

std::string bedrockBlockName(const std::string& rawName) {
    std::string name = baseBlockName(rawName);

    // hard coded and bad, not scalable, in the future we are going to want to data drive as much as possible with mappings
    if (name == "minecraft:grass_block") return "minecraft:grass";
    if (name == "minecraft:grass") return "minecraft:tallgrass";
    if (name == "minecraft:snow") return "minecraft:snow_layer";
    if (name == "minecraft:snow_block") return "minecraft:snow";
    if (name == "minecraft:short_grass") return "minecraft:tallgrass";
    if (name == "minecraft:fern") return "minecraft:tallgrass";
    if (name == "minecraft:tall_grass") return "minecraft:double_plant";
    if (name == "minecraft:lily_pad") return "minecraft:waterlily";
    if (name == "minecraft:sugar_cane") return "minecraft:reeds";
    if (name == "minecraft:dead_bush") return "minecraft:deadbush";
    if (name == "minecraft:magma_block") return "minecraft:magma";
    if (name == "minecraft:oak_log") return "minecraft:log";
    if (name == "minecraft:spruce_log") return "minecraft:log";
    if (name == "minecraft:birch_log") return "minecraft:log";
    if (name == "minecraft:jungle_log") return "minecraft:log";
    if (name == "minecraft:dark_oak_log") return "minecraft:log2";
    if (name == "minecraft:oak_leaves") return "minecraft:leaves";
    if (name == "minecraft:spruce_leaves") return "minecraft:leaves";
    if (name == "minecraft:birch_leaves") return "minecraft:leaves";
    if (name == "minecraft:jungle_leaves") return "minecraft:leaves";
    if (name == "minecraft:dark_oak_leaves") return "minecraft:leaves2";

    return name;
}

int bedrockBiomeId(uint16_t biomeId) {
    // Legacy biome IDs are still what the 1.18.30 LevelDB biome palette stores.
    static const std::array<int, 18> ids = {
        1,   // plains
        4,   // forest
        27,  // birch forest
        2,   // desert
        35,  // savanna
        21,  // jungle
        12,  // snowy plains / ice plains
        5,   // taiga
        37,  // badlands / mesa
        7,   // river
        6,   // swamp
        3,   // windswept hills / extreme hills
        178, // deep dark
        8,   // nether wastes
        179, // crimson forest
        180, // warped forest
        178, // soul sand valley fallback on 1.18-era PMMP tables if needed
        181  // basalt deltas
    };

    if (biomeId < ids.size()) {
        return ids[biomeId];
    }

    return 1;
}

void writeBlockStateProperties(std::vector<uint8_t>& out, const std::string& rawName) {
    std::string name = baseBlockName(rawName);

    nbtBeginCompound(out, "states");

    if (name == "minecraft:bedrock") {
        nbtByte(out, "infiniburn_bit", 0);
    } else if (name == "minecraft:deepslate" || name == "minecraft:basalt") {
        nbtString(out, "pillar_axis", "y");
    } else if (name == "minecraft:lava" || name == "minecraft:water") {
        nbtInt(out, "liquid_depth", 0);
    } else if (name == "minecraft:cactus" || name == "minecraft:sugar_cane") {
        nbtInt(out, "age", 0);
    } else if (name == "minecraft:oak_log") {
        nbtString(out, "old_log_type", "oak");
        nbtString(out, "pillar_axis", "y");
    } else if (name == "minecraft:spruce_log") {
        nbtString(out, "old_log_type", "spruce");
        nbtString(out, "pillar_axis", "y");
    } else if (name == "minecraft:birch_log") {
        nbtString(out, "old_log_type", "birch");
        nbtString(out, "pillar_axis", "y");
    } else if (name == "minecraft:jungle_log") {
        nbtString(out, "old_log_type", "jungle");
        nbtString(out, "pillar_axis", "y");
    } else if (name == "minecraft:dark_oak_log") {
        nbtString(out, "new_log_type", "dark_oak");
        nbtString(out, "pillar_axis", "y");
    } else if (name == "minecraft:oak_leaves") {
        nbtString(out, "old_leaf_type", "oak");
        nbtByte(out, "persistent_bit", 0);
        nbtByte(out, "update_bit", 0);
    } else if (name == "minecraft:spruce_leaves") {
        nbtString(out, "old_leaf_type", "spruce");
        nbtByte(out, "persistent_bit", 0);
        nbtByte(out, "update_bit", 0);
    } else if (name == "minecraft:birch_leaves") {
        nbtString(out, "old_leaf_type", "birch");
        nbtByte(out, "persistent_bit", 0);
        nbtByte(out, "update_bit", 0);
    } else if (name == "minecraft:jungle_leaves") {
        nbtString(out, "old_leaf_type", "jungle");
        nbtByte(out, "persistent_bit", 0);
        nbtByte(out, "update_bit", 0);
    } else if (name == "minecraft:dark_oak_leaves") {
        nbtString(out, "new_leaf_type", "dark_oak");
        nbtByte(out, "persistent_bit", 0);
        nbtByte(out, "update_bit", 0);
    } else if (name == "minecraft:tall_grass") {
        nbtString(out, "double_plant_type", "grass");
        nbtByte(out, "upper_block_bit", hasBlockProperty(rawName, "half=upper") ? 1 : 0);
    } else if (name == "minecraft:grass" || name == "minecraft:short_grass") {
        nbtString(out, "tall_grass_type", "tall");
    } else if (name == "minecraft:fern") {
        nbtString(out, "tall_grass_type", "fern");
    } else if (name == "minecraft:snow") {
        nbtByte(out, "covered_bit", 0);
        nbtInt(out, "height", 0);
    } else if (name == "minecraft:vine") {
        int bits = 0;
        if (rawName.find("north=true") != std::string::npos) bits |= 4;
        if (rawName.find("south=true") != std::string::npos) bits |= 1;
        if (rawName.find("east=true") != std::string::npos) bits |= 8;
        if (rawName.find("west=true") != std::string::npos) bits |= 2;
        nbtInt(out, "vine_direction_bits", bits == 0 ? 1 : bits);
    }

    nbtEndCompound(out);
}

std::vector<uint8_t> buildBlockStateNbt(const std::string& rawName) {
    std::vector<uint8_t> out;
    writeRootCompoundStart(out);
    nbtString(out, "name", bedrockBlockName(rawName));
    writeBlockStateProperties(out, rawName);
    nbtInt(out, "version", BEDROCK_BLOCK_STATE_VERSION_1_18_30);
    nbtEndCompound(out);
    return out;
}

int bitsForPalette(int paletteSize) {
    if (paletteSize <= 1) return 0;
    if (paletteSize <= 2) return 1;
    if (paletteSize <= 4) return 2;
    if (paletteSize <= 8) return 3;
    if (paletteSize <= 16) return 4;
    if (paletteSize <= 32) return 5;
    if (paletteSize <= 64) return 6;
    if (paletteSize <= 256) return 8;
    return 16;
}

void writePackedIndices(std::vector<uint8_t>& out, const std::vector<uint32_t>& indices, int bits) {
    if (bits == 0) {
        return;
    }

    int perWord = 32 / bits;
    size_t words = (indices.size() + static_cast<size_t>(perWord) - 1) / static_cast<size_t>(perWord);

    for (size_t w = 0; w < words; ++w) {
        uint32_t word = 0;

        for (int i = 0; i < perWord; ++i) {
            size_t index = w * static_cast<size_t>(perWord) + static_cast<size_t>(i);
            if (index >= indices.size()) {
                break;
            }
            word |= indices[index] << (i * bits);
        }

        putL32(out, static_cast<int32_t>(word));
    }
}

void writeBlockPalette(std::vector<uint8_t>& out, const std::vector<uint32_t>& indices, const std::vector<uint16_t>& localIds,
                       const Blocks& blocks) {
    int bits = bitsForPalette(static_cast<int>(localIds.size()));
    putL8(out, static_cast<uint8_t>(bits << 1));
    writePackedIndices(out, indices, bits);

    if (bits != 0) {
        putL32(out, static_cast<int32_t>(localIds.size()));
    }

    for (uint16_t gid : localIds) {
        std::vector<uint8_t> tag = buildBlockStateNbt(blocks.names[gid]);
        out.insert(out.end(), tag.begin(), tag.end());
    }
}

void writeBiomePalette(std::vector<uint8_t>& out, const std::vector<uint32_t>& indices, const std::vector<int>& localIds) {
    int bits = bitsForPalette(static_cast<int>(localIds.size()));
    putL8(out, static_cast<uint8_t>(bits << 1));
    writePackedIndices(out, indices, bits);

    if (bits != 0) {
        putL32(out, static_cast<int32_t>(localIds.size()));
    }

    for (int id : localIds) {
        putL32(out, id);
    }
}

bool sectionHasBlocks(const std::vector<uint16_t>& blockIds, int sectionIndex) {
    int baseY = sectionIndex * 16;

    for (int y = 0; y < 16; ++y) {
        for (int z = 0; z < 16; ++z) {
            for (int x = 0; x < 16; ++x) {
                if (blockIds[static_cast<size_t>(blockIndex(baseY + y, z, x))] != 0) {
                    return true;
                }
            }
        }
    }

    return false;
}

std::vector<uint8_t> buildSubChunk(const Blocks& blocks, const std::vector<uint16_t>& blockIds, int sectionIndex) {
    std::vector<uint8_t> out;
    putL8(out, BEDROCK_SUBCHUNK_VERSION);
    putL8(out, 1); // one block storage layer
    putL8(out, static_cast<uint8_t>((MIN_SECTION + sectionIndex) & 0xff));

    std::vector<int32_t> localMap(blocks.names.size(), -1);
    std::vector<uint16_t> localIds;
    std::vector<uint32_t> inverse;
    inverse.reserve(4096);
    int baseY = sectionIndex * 16;

    for (int x = 0; x < 16; ++x) {
        for (int z = 0; z < 16; ++z) {
            for (int y = 0; y < 16; ++y) {
                uint16_t gid = blockIds[static_cast<size_t>(blockIndex(baseY + y, z, x))];
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

    writeBlockPalette(out, inverse, localIds, blocks);
    return out;
}

std::vector<uint8_t> buildHeightmapAnd3dBiomes(const std::vector<uint16_t>& blockIds, const std::vector<uint16_t>& biomeIds) {
    std::vector<uint8_t> out;
    out.resize(512, 0);

    for (int z = 0; z < 16; ++z) {
        for (int x = 0; x < 16; ++x) {
            int top = -64;

            for (int gy = WORLD_HEIGHT - 1; gy >= 0; --gy) {
                if (blockIds[static_cast<size_t>(blockIndex(gy, z, x))] != 0) {
                    top = MIN_Y + gy + 1;
                    break;
                }
            }

            int off = (z * 16 + x) * 2;
            uint16_t v = static_cast<uint16_t>(std::max(0, top));
            out[static_cast<size_t>(off)] = static_cast<uint8_t>(v & 0xff);
            out[static_cast<size_t>(off + 1)] = static_cast<uint8_t>((v >> 8) & 0xff);
        }
    }

    for (int sectionIndex = 0; sectionIndex < N_SECTIONS; ++sectionIndex) {
        std::vector<int32_t> localMap(256, -1);
        std::vector<int> localIds;
        std::vector<uint32_t> inverse;
        inverse.reserve(4096);

        for (int x = 0; x < 16; ++x) {
            for (int z = 0; z < 16; ++z) {
                int biome = bedrockBiomeId(biomeIds[static_cast<size_t>(z * 16 + x)]);
                int32_t lid = localMap[static_cast<size_t>(std::clamp(biome, 0, 255))];
                if (lid < 0) {
                    lid = static_cast<int32_t>(localIds.size());
                    localMap[static_cast<size_t>(std::clamp(biome, 0, 255))] = lid;
                    localIds.push_back(biome);
                }

                for (int y = 0; y < 16; ++y) {
                    (void)y;
                    inverse.push_back(static_cast<uint32_t>(lid));
                }
            }
        }

        writeBiomePalette(out, inverse, localIds);
    }

    return out;
}

std::string chunkKeyBase(int cx, int cz) {
    std::string key;
    key.reserve(8);
    for (int i = 0; i < 4; ++i) key.push_back(static_cast<char>((static_cast<uint32_t>(cx) >> (i * 8)) & 0xff));
    for (int i = 0; i < 4; ++i) key.push_back(static_cast<char>((static_cast<uint32_t>(cz) >> (i * 8)) & 0xff));
    return key;
}

void putEntry(std::vector<BedrockKv>& out, std::string key, std::vector<uint8_t>&& value) {
    out.push_back({std::move(key), std::move(value)});
}

void putEntry(std::vector<BedrockKv>& out, std::string key, uint8_t value) {
    out.push_back({std::move(key), std::vector<uint8_t>{value}});
}

std::vector<uint8_t> buildBedrockLevelDat(const Config& cfg) {
    std::vector<uint8_t> nbt;
    writeRootCompoundStart(nbt);
    nbtInt(nbt, "DayCycleStopTime", -1);
    nbtInt(nbt, "Difficulty", 2);
    nbtByte(nbt, "ForceGameType", 0);
    nbtInt(nbt, "GameType", cfg.gameType);
    nbtInt(nbt, "Generator", 1);
    nbtLong(nbt, "LastPlayed", static_cast<int64_t>(std::time(nullptr)));
    nbtString(nbt, "LevelName", cfg.name);
    nbtInt(nbt, "NetworkVersion", BEDROCK_PROTOCOL_1_18_30);
    nbtLong(nbt, "RandomSeed", cfg.seed);
    nbtInt(nbt, "SpawnX", 0);
    nbtInt(nbt, "SpawnY", sampleTerrain(cfg, 0, 0).height + 1);
    nbtInt(nbt, "SpawnZ", 0);
    nbtInt(nbt, "StorageVersion", BEDROCK_STORAGE_VERSION);
    nbtLong(nbt, "Time", 0);
    nbtByte(nbt, "eduOffer", 0);
    nbtByte(nbt, "FallDamage", 1);
    nbtByte(nbt, "FireDamage", 1);
    nbtByte(nbt, "hasBeenLoadedInCreative", 1);
    nbtByte(nbt, "ImmutableWorld", 0);
    nbtFloat(nbt, "lightningLevel", 0.0f);
    nbtInt(nbt, "lightningTime", 0);
    nbtByte(nbt, "pvp", 1);
    nbtFloat(nbt, "rainLevel", 0.0f);
    nbtInt(nbt, "rainTime", 0);
    nbtByte(nbt, "spawnMobs", 1);
    nbtByte(nbt, "texturePacksRequired", 0);
    nbtByte(nbt, "commandsEnabled", cfg.allowCommands ? 1 : 0);
    nbtBeginList(nbt, "lastOpenedWithVersion", 3, 4);
    putL32(nbt, 1);
    putL32(nbt, 18);
    putL32(nbt, 30);
    putL32(nbt, 4);
    nbtString(nbt, "generatorName", "default");
    nbtString(nbt, "generatorOptions", "");
    nbtEndCompound(nbt);

    std::vector<uint8_t> out;
    putL32(out, BEDROCK_STORAGE_VERSION);
    putL32(out, static_cast<int32_t>(nbt.size()));
    out.insert(out.end(), nbt.begin(), nbt.end());
    return out;
}

void writeFileBytes(const fs::path& path, const std::vector<uint8_t>& bytes) {
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        throw std::runtime_error("could not write " + path.string());
    }
    out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
}

} // namespace

void appendBedrockChunk(const Config& cfg, const Blocks& blocks, int cx, int cz,
                        const std::vector<uint16_t>& blockIds,
                        const std::vector<uint16_t>& biomeIds,
                        std::vector<BedrockKv>& out) {
    (void)cfg;
    std::string base = chunkKeyBase(cx, cz);
    putEntry(out, base + KEY_NEW_VERSION, BEDROCK_CHUNK_VERSION_1_18_30);
    putEntry(out, base + KEY_LEGACY_VERSION, static_cast<uint8_t>(7));
    putEntry(out, base + KEY_FINALIZATION, BEDROCK_FINALIZATION_DONE);
    putEntry(out, base + KEY_HEIGHTMAP_AND_3D_BIOMES, buildHeightmapAnd3dBiomes(blockIds, biomeIds));
    putEntry(out, base + KEY_BLOCK_ENTITIES, std::vector<uint8_t>{});
    putEntry(out, base + KEY_ENTITIES, std::vector<uint8_t>{});

    for (int sectionIndex = 0; sectionIndex < N_SECTIONS; ++sectionIndex) {
        if (!sectionHasBlocks(blockIds, sectionIndex)) {
            continue;
        }

        std::string key = base + KEY_SUBCHUNK;
        key.push_back(static_cast<char>((MIN_SECTION + sectionIndex) & 0xff));
        putEntry(out, std::move(key), buildSubChunk(blocks, blockIds, sectionIndex));
    }
}

void writeBedrockWorld(const Config& cfg, const fs::path& outDir, std::vector<BedrockKv>&& entries) {
    fs::remove_all(outDir / "region");
    fs::create_directories(outDir);
    writeFileBytes(outDir / "level.dat", buildBedrockLevelDat(cfg));

    {
        std::ofstream name(outDir / "levelname.txt", std::ios::binary);
        name << cfg.name;
    }

    std::vector<LevelDbLogEntry> logEntries;
    logEntries.reserve(entries.size());
    for (BedrockKv& entry : entries) {
        logEntries.push_back({std::move(entry.key), std::move(entry.value)});
    }

    writeLevelDbLogStore(outDir / "db", std::move(logEntries));
}
