#include "mcwf/wilderness.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <functional>
#include <queue>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/blocks.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/terrain.hpp"
#include "mcwf/util.hpp"

namespace {

constexpr int TELEPORT_Y_OFFSET = 6;

struct SamplePoint {
    bool valid = false;
    int x = 0;
    int z = 0;
    int surfaceY = 0;
    int biome = BIOME_PLAINS;
    std::string key;
    std::string minecraftBiome;
};

struct BiomeSection {
    std::string dimension;
    std::string key;
    std::string minecraftBiome;
    int index = 0;
    int sampleCount = 0;
    int minX = 0;
    int maxX = 0;
    int minZ = 0;
    int maxZ = 0;
    int centerX = 0;
    int centerZ = 0;
    int surfaceY = 0;
    int teleportY = 0;
    int radius = 0;
    int approxAreaBlocks = 0;
};

std::string jsonEscape(const std::string& in) {
    std::string out;
    out.reserve(in.size() + 8);

    for (char ch : in) {
        switch (ch) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    out += " ";
                } else {
                    out += ch;
                }
                break;
        }
    }

    return out;
}

std::string safeMetadataFileName(const std::string& name) {
    std::string out;
    out.reserve(name.size() + 5);

    for (char ch : name) {
        bool ok = (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
                  (ch >= '0' && ch <= '9') || ch == ' ' || ch == '_' || ch == '-';
        if (ok) {
            out += ch;
        } else {
            out += '_';
        }
    }
    out = trim(out);
    if (out.empty()) {
        out = "Forged World";
    }

    return out + ".json";
}

std::string shortBiomeName(int biome) {
    if (biome >= 0 && biome < static_cast<int>(BIOMES.size())) {
        const std::string& full = BIOMES[static_cast<size_t>(biome)];
        size_t colon = full.find(':');
        if (colon != std::string::npos) {
            return full.substr(colon + 1);
        }

        return full;
    }

    return "unknown";
}

std::string minecraftBiomeName(int biome) {
    if (biome >= 0 && biome < static_cast<int>(BIOMES.size())) {
        return BIOMES[static_cast<size_t>(biome)];
    }

    return "minecraft:plains";
}

std::string overworldWildernessKey(const Config& cfg, const TerrainSample& sample) {
    if (sample.biome == BIOME_SNOW) {
        bool hills = sample.height >= cfg.baseHeight + 31 || sample.mountain > 0.34;

        return hills ? "snowy_hills" : "snowy_plains";
    }
    if (sample.biome == BIOME_BIRCH) {
        return "birch_forest";
    }
    if (sample.biome == BIOME_BADLANDS) {
        return "badlands";
    }
    if (sample.biome == BIOME_SWAMP) {
        return "swamp";
    }
    if (sample.biome == BIOME_TAIGA) {
        return "taiga";
    }
    if (sample.biome == BIOME_JUNGLE) {
        return "jungle";
    }
    if (sample.biome == BIOME_DESERT) {
        return "desert";
    }
    if (sample.biome == BIOME_FOREST) {
        return "forest";
    }
    if (sample.biome == BIOME_SAVANNA) {
        return "savanna";
    }
    if (sample.biome == BIOME_HILLS) {
        return "hills";
    }

    return "plains";
}

bool isTeleportableOverworld(const TerrainSample& sample) {
    if (sample.lava) {
        return false;
    }
    if (sample.waterSurface > sample.height) {
        return false;
    }
    if (sample.biome == BIOME_RIVER || sample.biome == BIOME_DEEP_DARK || sample.beach) {
        return false;
    }

    return true;
}

int sampleStepFor(const Area& area) {
    int width = area.xmax - area.xmin + 1;
    if (width <= 1536) {
        return 8;
    }
    if (width <= 3072) {
        return 12;
    }

    return 16;
}

int clampToArea(int value, int minValue, int maxValue) {
    return std::max(minValue, std::min(maxValue, value));
}

std::vector<BiomeSection> buildSections(const std::string& dimension, const Area& area, int step,
                                        const std::function<SamplePoint(int, int)>& sampler) {
    int gridW = std::max(1, (area.xmax - area.xmin + 1 + step - 1) / step);
    int gridH = std::max(1, (area.zmax - area.zmin + 1 + step - 1) / step);
    std::vector<SamplePoint> samples(static_cast<size_t>(gridW * gridH));
    std::vector<uint8_t> visited(static_cast<size_t>(gridW * gridH), 0);
    auto idx = [&](int gx, int gz) -> size_t {
        return static_cast<size_t>(gz * gridW + gx);
    };

    for (int gz = 0; gz < gridH; ++gz) {
        for (int gx = 0; gx < gridW; ++gx) {
            int x = clampToArea(area.xmin + gx * step + step / 2, area.xmin, area.xmax);
            int z = clampToArea(area.zmin + gz * step + step / 2, area.zmin, area.zmax);
            samples[idx(gx, gz)] = sampler(x, z);
        }
    }

    std::vector<BiomeSection> sections;
    const int dirs[4][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};

    for (int gz = 0; gz < gridH; ++gz) {
        for (int gx = 0; gx < gridW; ++gx) {
            size_t startIndex = idx(gx, gz);
            if (visited[startIndex] || !samples[startIndex].valid) {
                continue;
            }

            std::vector<size_t> component;
            std::queue<std::pair<int, int>> queue;
            visited[startIndex] = 1;
            queue.push({gx, gz});
            const std::string key = samples[startIndex].key;

            while (!queue.empty()) {
                auto [cx, cz] = queue.front();
                queue.pop();
                size_t cur = idx(cx, cz);
                component.push_back(cur);

                for (const auto& d : dirs) {
                    int nx = cx + d[0];
                    int nz = cz + d[1];
                    if (nx < 0 || nx >= gridW || nz < 0 || nz >= gridH) {
                        continue;
                    }
                    size_t ni = idx(nx, nz);
                    if (visited[ni] || !samples[ni].valid || samples[ni].key != key) {
                        continue;
                    }
                    visited[ni] = 1;
                    queue.push({nx, nz});
                }
            }

            int64_t sumX = 0;
            int64_t sumZ = 0;
            int minX = samples[component.front()].x;
            int maxX = minX;
            int minZ = samples[component.front()].z;
            int maxZ = minZ;

            for (size_t sampleIndex : component) {
                const SamplePoint& p = samples[sampleIndex];
                sumX += p.x;
                sumZ += p.z;
                minX = std::min(minX, p.x);
                maxX = std::max(maxX, p.x);
                minZ = std::min(minZ, p.z);
                maxZ = std::max(maxZ, p.z);
            }

            double centerXF = static_cast<double>(sumX) / static_cast<double>(component.size());
            double centerZF = static_cast<double>(sumZ) / static_cast<double>(component.size());
            size_t bestIndex = component.front();
            double bestDist = 1.0e100;

            for (size_t sampleIndex : component) {
                const SamplePoint& p = samples[sampleIndex];
                double dx = static_cast<double>(p.x) - centerXF;
                double dz = static_cast<double>(p.z) - centerZF;
                double dist = dx * dx + dz * dz;
                if (dist < bestDist) {
                    bestDist = dist;
                    bestIndex = sampleIndex;
                }
            }

            const SamplePoint& center = samples[bestIndex];
            BiomeSection section;
            section.dimension = dimension;
            section.key = key;
            section.minecraftBiome = center.minecraftBiome;
            section.sampleCount = static_cast<int>(component.size());
            section.minX = minX;
            section.maxX = maxX;
            section.minZ = minZ;
            section.maxZ = maxZ;
            section.centerX = center.x;
            section.centerZ = center.z;
            section.surfaceY = center.surfaceY;
            section.teleportY = clampInt(center.surfaceY + TELEPORT_Y_OFFSET, MIN_Y + 2, MIN_Y + WORLD_HEIGHT - 2);
            section.radius = std::max(0, std::min(maxX - minX, maxZ - minZ) / 2);
            section.approxAreaBlocks = section.sampleCount * step * step;
            sections.push_back(section);
        }
    }

    std::unordered_map<std::string, int> sectionCounts;
    for (BiomeSection& section : sections) {
        section.index = sectionCounts[section.dimension + ":" + section.key]++;
    }

    std::sort(sections.begin(), sections.end(), [](const BiomeSection& a, const BiomeSection& b) {
        if (a.dimension != b.dimension) return a.dimension < b.dimension;
        if (a.key != b.key) return a.key < b.key;
        if (a.sampleCount != b.sampleCount) return a.sampleCount > b.sampleCount;
        if (a.centerX != b.centerX) return a.centerX < b.centerX;

        return a.centerZ < b.centerZ;
    });

    return sections;
}

SamplePoint sampleOverworldPoint(const Config& cfg, int x, int z) {
    TerrainSample sample = sampleTerrain(cfg, x, z);
    if (!isTeleportableOverworld(sample)) {
        return {};
    }

    SamplePoint point;
    point.valid = true;
    point.x = x;
    point.z = z;
    point.surfaceY = sample.height;
    point.biome = sample.biome;
    point.key = overworldWildernessKey(cfg, sample);
    point.minecraftBiome = minecraftBiomeName(sample.biome);

    return point;
}

SamplePoint sampleNetherPoint(const Config& cfg, int x, int z) {
    int biome = netherBiome(cfg, x, z);
    SamplePoint point;
    point.valid = true;
    point.x = x;
    point.z = z;
    point.surfaceY = 64;
    point.biome = biome;
    point.key = shortBiomeName(biome);
    point.minecraftBiome = minecraftBiomeName(biome);

    return point;
}

void writePosition(std::ofstream& out, int x, int y, int z, int surfaceY) {
    out << "{\"x\": " << x << ", \"y\": " << y << ", \"z\": " << z << ", \"surface_y\": " << surfaceY << "}";
}

void writeSection(std::ofstream& out, const BiomeSection& section, int ordinal, bool comma) {
    out << "    {\n";
    out << "      \"id\": \"" << jsonEscape(section.dimension + ":" + section.key + ":" + std::to_string(section.index)) << "\",\n";
    out << "      \"dimension\": \"" << jsonEscape(section.dimension) << "\",\n";
    out << "      \"biome\": \"" << jsonEscape(section.key) << "\",\n";
    out << "      \"minecraft_biome\": \"" << jsonEscape(section.minecraftBiome) << "\",\n";
    out << "      \"section_index\": " << section.index << ",\n";
    out << "      \"ordinal\": " << ordinal << ",\n";
    out << "      \"midpoint\": ";
    writePosition(out, section.centerX, section.teleportY, section.centerZ, section.surfaceY);
    out << ",\n";
    out << "      \"bounds\": {\"min_x\": " << section.minX << ", \"max_x\": " << section.maxX
        << ", \"min_z\": " << section.minZ << ", \"max_z\": " << section.maxZ << "},\n";
    out << "      \"sample_count\": " << section.sampleCount << ",\n";
    out << "      \"approx_area_blocks\": " << section.approxAreaBlocks << ",\n";
    out << "      \"approx_radius_blocks\": " << section.radius << "\n";
    out << "    }";
    if (comma) {
        out << ",";
    }
    out << "\n";
}

void reindexSections(std::vector<BiomeSection>& sections) {
    std::unordered_map<std::string, int> sectionCounts;

    for (BiomeSection& section : sections) {
        section.index = sectionCounts[section.dimension + ":" + section.key]++;
    }
}

void writeGroupedSections(std::ofstream& out, const std::vector<BiomeSection>& sections) {
    std::vector<std::string> keys;
    std::unordered_map<std::string, std::vector<size_t>> byKey;

    for (size_t i = 0; i < sections.size(); ++i) {
        std::string key = sections[i].dimension + ":" + sections[i].key;
        if (byKey.find(key) == byKey.end()) {
            keys.push_back(key);
        }
        byKey[key].push_back(i);
    }

    std::sort(keys.begin(), keys.end());
    out << "  \"biomes\": {\n";

    for (size_t ki = 0; ki < keys.size(); ++ki) {
        const std::string& key = keys[ki];
        const std::vector<size_t>& list = byKey[key];
        out << "    \"" << jsonEscape(key) << "\": [\n";
        for (size_t li = 0; li < list.size(); ++li) {
            const BiomeSection& section = sections[list[li]];
            out << "      {\"id\": \"" << jsonEscape(section.dimension + ":" + section.key + ":" + std::to_string(section.index))
                << "\", \"x\": " << section.centerX << ", \"y\": " << section.teleportY << ", \"z\": " << section.centerZ
                << ", \"surface_y\": " << section.surfaceY << ", \"approx_radius_blocks\": " << section.radius
                << ", \"approx_area_blocks\": " << section.approxAreaBlocks << "}";
            if (li + 1 < list.size()) {
                out << ",";
            }
            out << "\n";
        }
        out << "    ]";
        if (ki + 1 < keys.size()) {
            out << ",";
        }
        out << "\n";
    }

    out << "  },\n";
}

}  // namespace

fs::path writeWildernessJson(const Config& cfg, const fs::path& outDir) {
    Area ow = overworldArea(cfg);
    int overworldStep = sampleStepFor(ow);
    std::vector<BiomeSection> sections = buildSections("overworld", ow, overworldStep, [&](int x, int z) {
        return sampleOverworldPoint(cfg, x, z);
    });

    int netherStep = 16;
    if (cfg.nether) {
        Area nether = netherArea(cfg);
        std::vector<BiomeSection> netherSections = buildSections("nether", nether, netherStep, [&](int x, int z) {
            return sampleNetherPoint(cfg, x, z);
        });
        sections.insert(sections.end(), netherSections.begin(), netherSections.end());
    }

    std::sort(sections.begin(), sections.end(), [](const BiomeSection& a, const BiomeSection& b) {
        if (a.dimension != b.dimension) return a.dimension < b.dimension;
        if (a.key != b.key) return a.key < b.key;
        if (a.sampleCount != b.sampleCount) return a.sampleCount > b.sampleCount;
        if (a.centerX != b.centerX) return a.centerX < b.centerX;

        return a.centerZ < b.centerZ;
    });
    reindexSections(sections);

    fs::path fileName = safeMetadataFileName(cfg.name);
    fs::path path = outDir / fileName;
    std::ofstream out(path);

    int owCenterX = (ow.xmin + ow.xmax) / 2;
    int owCenterZ = (ow.zmin + ow.zmax) / 2;
    TerrainSample owCenter = sampleTerrain(cfg, owCenterX, owCenterZ);
    int owCenterY = clampInt(owCenter.height + TELEPORT_Y_OFFSET, MIN_Y + 2, MIN_Y + WORLD_HEIGHT - 2);

    out << "{\n";
    out << "  \"format\": \"mc_world_forge_wilderness_points_v1\",\n";
    out << "  \"world\": \"" << jsonEscape(cfg.name) << "\",\n";
    out << "  \"seed\": " << cfg.seed << ",\n";
    out << "  \"output_format\": \"" << jsonEscape(cfg.outputFormat) << "\",\n";
    out << "  \"teleport_y_offset\": " << TELEPORT_Y_OFFSET << ",\n";
    out << "  \"sample_step\": {\"overworld\": " << overworldStep << ", \"nether\": " << netherStep << "},\n";
    out << "  \"world_midpoints\": {\n";
    out << "    \"overworld\": {\"bounds\": {\"min_x\": " << ow.xmin << ", \"max_x\": " << ow.xmax
        << ", \"min_z\": " << ow.zmin << ", \"max_z\": " << ow.zmax << "}, \"midpoint\": ";
    writePosition(out, owCenterX, owCenterY, owCenterZ, owCenter.height);
    out << "}";

    if (cfg.nether) {
        Area nether = netherArea(cfg);
        int nx = (nether.xmin + nether.xmax) / 2;
        int nz = (nether.zmin + nether.zmax) / 2;
        out << ",\n";
        out << "    \"nether\": {\"bounds\": {\"min_x\": " << nether.xmin << ", \"max_x\": " << nether.xmax
            << ", \"min_z\": " << nether.zmin << ", \"max_z\": " << nether.zmax
            << "}, \"midpoint\": {\"x\": " << nx << ", \"y\": 64, \"z\": " << nz
            << ", \"floor_y\": " << NETHER_BOX_FLOOR_Y
            << ", \"lava_sea_y\": " << NETHER_LAVA_SEA_Y
            << ", \"ceiling_y\": " << NETHER_BOX_CEILING_Y << "}}\n";
    } else {
        out << ",\n";
        out << "    \"nether\": null\n";
    }
    out << "  },\n";

    writeGroupedSections(out, sections);

    out << "  \"biome_sections\": [\n";
    for (size_t i = 0; i < sections.size(); ++i) {
        writeSection(out, sections[i], static_cast<int>(i), i + 1 < sections.size());
    }
    out << "  ]\n";
    out << "}\n";

    return fileName;
}
