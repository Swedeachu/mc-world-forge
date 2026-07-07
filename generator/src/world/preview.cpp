#include "mcwf/preview.hpp"

#include <algorithm>
#include <fstream>
#include <system_error>

#include "mcwf/area.hpp"
#include "mcwf/chunk.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/terrain.hpp"
#include "mcwf/util.hpp"

namespace {

bool isPreviewAirLike(int id, const BlockIds& ids) {
    return id == ids.air || id == ids.shortGrass || id == ids.fern ||
           id == ids.tallGrassLo || id == ids.tallGrassHi || id == ids.deadBush ||
           id == ids.cactus || id == ids.sugarCane || id == ids.vineNorth ||
           id == ids.vineSouth || id == ids.vineEast || id == ids.vineWest ||
           id == ids.lilyPad || id == ids.blueOrchid || id == ids.brownMushroom ||
           id == ids.redMushroom;
}

void shadeColor(int height, int baseHeight, uint8_t& r, uint8_t& g, uint8_t& b) {
    double local = static_cast<double>(height - baseHeight);
    double shade;
    if (local < -8.0) {
        shade = 0.71;
    } else if (local < 3.0) {
        shade = 0.86;
    } else if (local < 16.0) {
        shade = 1.0;
    } else {
        shade = 1.15;
    }
    r = static_cast<uint8_t>(clampInt(static_cast<int>(r * shade), 0, 255));
    g = static_cast<uint8_t>(clampInt(static_cast<int>(g * shade), 0, 255));
    b = static_cast<uint8_t>(clampInt(static_cast<int>(b * shade), 0, 255));
}

void previewColorFromColumn(const Config& cfg, const BlockIds& ids, int topId, int belowId, int biome, int height,
                            uint8_t& r, uint8_t& g, uint8_t& b) {
    // Streaming preview is now based on the actual generated blocks. Do not let
    // a snowy biome tag repaint exposed stone mountains as solid white; that was
    // why the web preview looked like a full snow world while the saved world was
    // mostly grass/stone in-game. Only snow-like blocks render as snow.
    if (topId == ids.lava) {
        r = 214; g = 84; b = 36;

        return;
    }
    if (topId == ids.water) {
        r = 64; g = 64; b = 255;

        return;
    }
    bool leaf = topId == ids.oakLeaves || topId == ids.birchLeaves || topId == ids.spruceLeaves ||
                topId == ids.jungleLeaves || topId == ids.darkOakLeaves;
    bool log = topId == ids.oakLog || topId == ids.birchLog || topId == ids.spruceLog ||
               topId == ids.jungleLog || topId == ids.darkOakLog;
    bool snowOnRock = topId == ids.snowLayer &&
        (belowId == ids.snowBlock || belowId == ids.stone || belowId == ids.deepslate || belowId == ids.tuff || height >= cfg.snowlineY - 4);

    if (topId == ids.snowBlock || snowOnRock) {
        r = 255; g = 255; b = 255;
    } else if (leaf) {
        if (topId == ids.spruceLeaves) { r = 54; g = 88; b = 58; }
        else if (topId == ids.jungleLeaves) { r = 45; g = 124; b = 34; }
        else if (topId == ids.birchLeaves) { r = 100; g = 142; b = 67; }
        else { r = 64; g = 112; b = 42; }
    } else if (log) {
        r = 91; g = 65; b = 38;
    } else if (topId == ids.stone || topId == ids.deepslate || topId == ids.tuff) {
        r = 116; g = 132; b = 108;
    } else if (topId == ids.mud) {
        r = 83; g = 74; b = 65;
    } else if (topId == ids.redSand || topId == ids.redSandstone || topId == ids.orangeTerracotta ||
               topId == ids.yellowTerracotta || topId == ids.brownTerracotta || topId == ids.redTerracotta ||
               topId == ids.whiteTerracotta || topId == ids.terracotta || biome == BIOME_BADLANDS) {
        r = 216; g = 127; b = 51;
    } else if (biome == BIOME_SWAMP) {
        r = 76; g = 111; b = 59;
    } else if (topId == ids.sand || topId == ids.sandstone || biome == BIOME_DESERT || biome == BIOME_RIVER) {
        r = 247; g = 233; b = 163;
    } else if (biome == BIOME_JUNGLE) {
        r = 64; g = 145; b = 35;
    } else if (biome == BIOME_FOREST) {
        r = 89; g = 125; b = 39;
    } else if (biome == BIOME_BIRCH) {
        r = 111; g = 158; b = 69;
    } else if (biome == BIOME_SWAMP) {
        r = 75; g = 112; b = 69;
    } else if (biome == BIOME_TAIGA || topId == ids.podzol) {
        r = 79; g = 110; b = 80;
    } else if (topId == ids.sculk) {
        r = 38; g = 45; b = 52;
    } else {
        r = 127; g = 178; b = 56;
    }
    shadeColor(height, cfg.baseHeight, r, g, b);
}

} // namespace

void previewColor(const Config& cfg, int x, int z, uint8_t& r, uint8_t& g, uint8_t& b) {
    if (cfg.nether) {
        Area na = netherArea(cfg);
        if (insideArea(x, z, na)) {
            int biome = netherBiome(cfg, x, z);
            if (biome == BIOME_CRIMSON) { r = 92; g = 20; b = 28; return; }
            if (biome == BIOME_WARPED) { r = 28; g = 96; b = 86; return; }
            if (biome == BIOME_SOUL) { r = 96; g = 82; b = 68; return; }
            if (biome == BIOME_BASALT) { r = 50; g = 50; b = 55; return; }
            r = 110; g = 42; b = 36; return;
        }
    }

    Area ow = overworldArea(cfg);
    if (!insideArea(x, z, ow)) {
        r = 8; g = 10; b = 16;

        return;
    }
    TerrainSample s = sampleTerrain(cfg, x, z);
    if (s.lava) {
        r = 214; g = 84; b = 36;
    } else if (s.river || s.lake) {
        r = 64; g = 64; b = 255;
    } else if (s.snowy && (s.height >= cfg.snowlineY - 4 || s.topId == 0) && (s.height >= cfg.baseHeight + 38 || s.mountain > 0.42)) {
        r = 255; g = 255; b = 255;
    } else if (s.biome == BIOME_SNOW && (s.height >= cfg.baseHeight + 31 || s.mountain > 0.34)) {
        r = 116; g = 132; b = 108;
    } else if (s.badlands) {
        r = 216; g = 127; b = 51;
    } else if (s.desert || s.beach) {
        r = 247; g = 233; b = 163;
    } else if (s.biome == BIOME_JUNGLE) {
        r = 64; g = 145; b = 35;
    } else if (s.biome == BIOME_FOREST) {
        r = 89; g = 125; b = 39;
    } else if (s.biome == BIOME_BIRCH) {
        r = 111; g = 158; b = 69;
    } else if (s.biome == BIOME_SWAMP) {
        r = 75; g = 112; b = 69;
    } else if (s.biome == BIOME_TAIGA) {
        r = 79; g = 110; b = 80;
    } else if (s.biome == BIOME_SWAMP) {
        r = 76; g = 111; b = 59;
    } else if (s.biome == BIOME_HILLS) {
        r = 116; g = 132; b = 108;
    } else {
        r = 127; g = 178; b = 56;
    }

    double local = static_cast<double>(s.height - cfg.baseHeight);
    double shade;
    if (local < -8.0) {
        shade = 0.71;
    } else if (local < 3.0) {
        shade = 0.86;
    } else if (local < 16.0) {
        shade = 1.0;
    } else {
        shade = 1.15;
    }
    r = static_cast<uint8_t>(clampInt(static_cast<int>(r * shade), 0, 255));
    g = static_cast<uint8_t>(clampInt(static_cast<int>(g * shade), 0, 255));
    b = static_cast<uint8_t>(clampInt(static_cast<int>(b * shade), 0, 255));
}

PreviewFrame createPreviewFrame(const Config& cfg, const fs::path& outDir) {
    std::vector<Area> areas = generationAreas(cfg);
    PreviewFrame frame;
    frame.xmin = areas[0].xmin;
    frame.xmax = areas[0].xmax;
    frame.zmin = areas[0].zmin;
    frame.zmax = areas[0].zmax;

    for (const Area& a : areas) {
        frame.xmin = std::min(frame.xmin, a.xmin);
        frame.xmax = std::max(frame.xmax, a.xmax);
        frame.zmin = std::min(frame.zmin, a.zmin);
        frame.zmax = std::max(frame.zmax, a.zmax);
    }
    int wBlocks = frame.xmax - frame.xmin + 1;
    int hBlocks = frame.zmax - frame.zmin + 1;
    frame.scale = std::max(1, std::max(wBlocks, hBlocks) / 768);
    frame.width = (wBlocks + frame.scale - 1) / frame.scale;
    frame.height = (hBlocks + frame.scale - 1) / frame.scale;
    frame.path = outDir / "preview.ppm";
    frame.image.assign(static_cast<size_t>(frame.width * frame.height * 3), 0);
    clearPreviewFrame(frame);

    return frame;
}

void clearPreviewFrame(PreviewFrame& frame) {
    std::lock_guard<std::mutex> lock(frame.mutex);

    for (int py = 0; py < frame.height; ++py) {
        for (int px = 0; px < frame.width; ++px) {
            size_t idx = static_cast<size_t>((py * frame.width + px) * 3);
            frame.image[idx + 0] = 12;
            frame.image[idx + 1] = 15;
            frame.image[idx + 2] = 24;
        }
    }
}

void updatePreviewForChunk(const Config& cfg, PreviewFrame& frame, int cx, int cz) {
    int chunkX0 = cx * 16;
    int chunkX1 = chunkX0 + 15;
    int chunkZ0 = cz * 16;
    int chunkZ1 = chunkZ0 + 15;

    int px0 = clampInt((chunkX0 - frame.xmin) / frame.scale, 0, frame.width - 1);
    int px1 = clampInt((chunkX1 - frame.xmin + frame.scale - 1) / frame.scale, 0, frame.width - 1);
    int py0 = clampInt((chunkZ0 - frame.zmin) / frame.scale, 0, frame.height - 1);
    int py1 = clampInt((chunkZ1 - frame.zmin + frame.scale - 1) / frame.scale, 0, frame.height - 1);

    std::lock_guard<std::mutex> lock(frame.mutex);

    for (int py = py0; py <= py1; ++py) {
        int z = frame.zmin + py * frame.scale;
        if (z < chunkZ0 || z > chunkZ1) {
            continue;
        }

        for (int px = px0; px <= px1; ++px) {
            int x = frame.xmin + px * frame.scale;
            if (x < chunkX0 || x > chunkX1) {
                continue;
            }
            uint8_t r, g, b;
            previewColor(cfg, x, z, r, g, b);
            size_t idx = static_cast<size_t>((py * frame.width + px) * 3);
            frame.image[idx + 0] = r;
            frame.image[idx + 1] = g;
            frame.image[idx + 2] = b;
        }
    }
}

void updatePreviewFromChunkBlocks(const Config& cfg, const BlockIds& ids, PreviewFrame& frame, int cx, int cz,
                                  const std::vector<uint16_t>& blockIds, const std::vector<uint16_t>& biomeIds) {
    int chunkX0 = cx * 16;
    int chunkX1 = chunkX0 + 15;
    int chunkZ0 = cz * 16;
    int chunkZ1 = chunkZ0 + 15;

    int px0 = clampInt((chunkX0 - frame.xmin) / frame.scale, 0, frame.width - 1);
    int px1 = clampInt((chunkX1 - frame.xmin + frame.scale - 1) / frame.scale, 0, frame.width - 1);
    int py0 = clampInt((chunkZ0 - frame.zmin) / frame.scale, 0, frame.height - 1);
    int py1 = clampInt((chunkZ1 - frame.zmin + frame.scale - 1) / frame.scale, 0, frame.height - 1);

    std::vector<uint8_t> pixels(static_cast<size_t>((px1 - px0 + 1) * (py1 - py0 + 1) * 3), 0);
    std::vector<uint8_t> valid(static_cast<size_t>((px1 - px0 + 1) * (py1 - py0 + 1)), 0);
    int localW = px1 - px0 + 1;

    for (int py = py0; py <= py1; ++py) {
        int z = frame.zmin + py * frame.scale;
        if (z < chunkZ0 || z > chunkZ1) {
            continue;
        }
        int lz = z - chunkZ0;

        for (int px = px0; px <= px1; ++px) {
            int x = frame.xmin + px * frame.scale;
            if (x < chunkX0 || x > chunkX1) {
                continue;
            }
            int lx = x - chunkX0;
            int topId = ids.air;
            int belowId = ids.air;
            int topY = MIN_Y;

            for (int y = MIN_Y + WORLD_HEIGHT - 1; y >= MIN_Y; --y) {
                int id = blockIds[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))];
                if (!isPreviewAirLike(id, ids)) {
                    topId = id;
                    topY = y;
                    belowId = (y > MIN_Y) ? blockIds[static_cast<size_t>(blockIndex(yIndex(y - 1), lz, lx))] : ids.air;
                    break;
                }
            }
            int biome = BIOME_PLAINS;
            if (!biomeIds.empty()) {
                biome = static_cast<int>(biomeIds[static_cast<size_t>(lz * 16 + lx)]);
            }
            uint8_t r = 12, g = 15, b = 24;
            previewColorFromColumn(cfg, ids, topId, belowId, biome, topY, r, g, b);
            size_t li = static_cast<size_t>((py - py0) * localW + (px - px0));
            pixels[li * 3 + 0] = r;
            pixels[li * 3 + 1] = g;
            pixels[li * 3 + 2] = b;
            valid[li] = 1;
        }
    }

    std::lock_guard<std::mutex> lock(frame.mutex);

    for (int py = py0; py <= py1; ++py) {
        for (int px = px0; px <= px1; ++px) {
            size_t li = static_cast<size_t>((py - py0) * localW + (px - px0));
            if (!valid[li]) {
                continue;
            }
            size_t di = static_cast<size_t>((py * frame.width + px) * 3);
            frame.image[di + 0] = pixels[li * 3 + 0];
            frame.image[di + 1] = pixels[li * 3 + 1];
            frame.image[di + 2] = pixels[li * 3 + 2];
        }
    }
}

void flushPreviewFrame(PreviewFrame& frame) {
    std::lock_guard<std::mutex> lock(frame.mutex);
    fs::path tmp = frame.path;
    tmp += ".tmp";
    {
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        out << "P6\n" << frame.width << " " << frame.height << "\n255\n";
        out.write(reinterpret_cast<const char*>(frame.image.data()), static_cast<std::streamsize>(frame.image.size()));
    }
    std::error_code ec;
    fs::rename(tmp, frame.path, ec);
    if (ec) {
        fs::remove(frame.path, ec);
        ec.clear();
        fs::rename(tmp, frame.path, ec);
    }
}

void writePreview(const Config& cfg, const fs::path& outDir) {
    PreviewFrame frame = createPreviewFrame(cfg, outDir);

    for (int cz = floorDiv(frame.zmin, 16); cz <= floorDiv(frame.zmax, 16); ++cz) {
        for (int cx = floorDiv(frame.xmin, 16); cx <= floorDiv(frame.xmax, 16); ++cx) {
            updatePreviewForChunk(cfg, frame, cx, cz);
        }
    }
    flushPreviewFrame(frame);
}
