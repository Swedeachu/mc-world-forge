#include "mcwf/region.hpp"

#include <ctime>
#include <fstream>
#include <iostream>
#include <string>

#include "mcwf/area.hpp"
#include "mcwf/chunk.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/nbt.hpp"
#include "mcwf/terrain.hpp"
#include "mcwf/util.hpp"

std::mutex g_coutMutex;

RegionWriter::RegionWriter(int regionX, int regionZ) : rx(regionX), rz(regionZ) {}

void RegionWriter::addChunk(int cx, int cz, std::vector<uint8_t>&& nbtPayload) {
    int lx = cx & 31;
    int lz = cz & 31;
    chunks[lx + lz * 32] = std::move(nbtPayload);
}

void RegionWriter::write(const fs::path& dir) const {
    std::vector<uint8_t> locations(SECTOR_BYTES, 0);
    std::vector<uint8_t> timestamps(SECTOR_BYTES, 0);
    std::vector<uint8_t> body;
    int nextSector = 2;
    int now = static_cast<int>(std::time(nullptr));

    for (const auto& entry : chunks) {
        int idx = entry.first;
        const std::vector<uint8_t>& payload = entry.second;
        std::vector<uint8_t> frame;
        frame.reserve(payload.size() + 16);
        putI32(frame, static_cast<int32_t>(payload.size() + 1));
        putU8(frame, 3); // 3 = uncompressed NBT payload. Keeps the native build dependency-free.
        frame.insert(frame.end(), payload.begin(), payload.end());
        size_t pad = (SECTOR_BYTES - (frame.size() % SECTOR_BYTES)) % SECTOR_BYTES;
        frame.insert(frame.end(), pad, 0);
        int sectorCount = static_cast<int>(frame.size() / SECTOR_BYTES);

        int off = idx * 4;
        locations[static_cast<size_t>(off + 0)] = static_cast<uint8_t>((nextSector >> 16) & 0xff);
        locations[static_cast<size_t>(off + 1)] = static_cast<uint8_t>((nextSector >> 8) & 0xff);
        locations[static_cast<size_t>(off + 2)] = static_cast<uint8_t>(nextSector & 0xff);
        locations[static_cast<size_t>(off + 3)] = static_cast<uint8_t>(sectorCount & 0xff);
        timestamps[static_cast<size_t>(off + 0)] = static_cast<uint8_t>((now >> 24) & 0xff);
        timestamps[static_cast<size_t>(off + 1)] = static_cast<uint8_t>((now >> 16) & 0xff);
        timestamps[static_cast<size_t>(off + 2)] = static_cast<uint8_t>((now >> 8) & 0xff);
        timestamps[static_cast<size_t>(off + 3)] = static_cast<uint8_t>(now & 0xff);

        body.insert(body.end(), frame.begin(), frame.end());
        nextSector += sectorCount;
    }

    fs::create_directories(dir);
    fs::path path = dir / ("r." + std::to_string(rx) + "." + std::to_string(rz) + ".mca");
    std::ofstream out(path, std::ios::binary);
    out.write(reinterpret_cast<const char*>(locations.data()), static_cast<std::streamsize>(locations.size()));
    out.write(reinterpret_cast<const char*>(timestamps.data()), static_cast<std::streamsize>(timestamps.size()));
    out.write(reinterpret_cast<const char*>(body.data()), static_cast<std::streamsize>(body.size()));
}

bool regionChunkInAnyArea(int cx, int cz, const std::vector<Area>& areas, const Area** outArea) {
    for (const Area& area : areas) {
        if (chunkIntersectsArea(cx, cz, area)) {
            if (outArea) {
                *outArea = &area;
            }

            return true;
        }
    }

    return false;
}

std::vector<uint8_t> generateChunkPayload(const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                                          const Area& area, int cx, int cz,
                                          std::vector<uint16_t>& blockIds, std::vector<uint16_t>& biomeIds) {
    if (area.kind == "nether") {
        generateNetherChunk(cfg, area, blocks, ids, cx, cz, blockIds, biomeIds);
    } else {
        generateOverworldChunk(cfg, area, blocks, ids, cx, cz, blockIds, biomeIds);
    }

    return buildChunkNbt(cfg, blocks, cx, cz, blockIds, biomeIds);
}

int generateRegion(const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                          const std::vector<Area>& areas, const RegionJob& job, const fs::path& regionDir,
                          const std::function<void(int, int)>& onChunkDone) {
    RegionWriter writer(job.rx, job.rz);
    std::vector<uint16_t> blockIds(static_cast<size_t>(WORLD_HEIGHT * 16 * 16));
    std::vector<uint16_t> biomeIds(static_cast<size_t>(16 * 16));
    int chunks = 0;

    int cBaseX = job.rx * 32;
    int cBaseZ = job.rz * 32;

    for (int dz = 0; dz < 32; ++dz) {
        for (int dx = 0; dx < 32; ++dx) {
            int cx = cBaseX + dx;
            int cz = cBaseZ + dz;
            const Area* area = nullptr;
            if (!regionChunkInAnyArea(cx, cz, areas, &area)) {
                continue;
            }
            if (area->kind == "nether") {
                generateNetherChunk(cfg, *area, blocks, ids, cx, cz, blockIds, biomeIds);
            } else {
                generateOverworldChunk(cfg, *area, blocks, ids, cx, cz, blockIds, biomeIds);
            }
            writer.addChunk(cx, cz, buildChunkNbt(cfg, blocks, cx, cz, blockIds, biomeIds));
            ++chunks;
            if (onChunkDone) {
                onChunkDone(cx, cz);
            }
        }
    }

    if (chunks > 0) {
        writer.write(regionDir);
    }

    return chunks;
}

void reportProgress(double p, const std::string& msg) {
    std::lock_guard<std::mutex> lock(g_coutMutex);
    std::cout << "PROGRESS " << clampDouble(p, 0.0, 1.0) << " " << msg << std::endl;
}

void reportPreviewReady() {
    std::lock_guard<std::mutex> lock(g_coutMutex);
    std::cout << "PREVIEW_READY" << std::endl;
}

int spawnY(const Config& cfg) {
    return sampleTerrain(cfg, 0, 0).height + 1;
}

void writeResultJson(const Config& cfg, const fs::path& outDir, int chunks, int regions, int workers, double seconds) {
    Area ow = overworldArea(cfg);
    std::ofstream out(outDir / "generator_result.json");
    out << "{\n";
    out << "  \"chunks\": " << chunks << ",\n";
    out << "  \"regions\": " << regions << ",\n";
    out << "  \"workers\": " << workers << ",\n";
    out << "  \"job_mode\": \"native_chunk_grid\",\n";
    out << "  \"spawn\": [0, " << spawnY(cfg) << ", 0],\n";
    out << "  \"bounds\": [" << ow.xmin << ", " << ow.xmax << "],\n";
    if (cfg.nether) {
        Area na = netherArea(cfg);
        out << "  \"nether_bounds\": [" << na.xmin << ", " << na.xmax << ", " << na.zmin << ", " << na.zmax << "],\n";
    } else {
        out << "  \"nether_bounds\": null,\n";
    }
    out << "  \"seconds\": " << seconds << ",\n";
    out << "  \"biome_midpoints\": [],\n";
    out << "  \"structures\": [],\n";
    out << "  \"layer_starts\": {\"deep_dark\": ";
    if (cfg.deepDark) out << cfg.floorY; else out << "null";
    out << ", \"nether\": ";
    if (cfg.nether) out << NETHER_BOX_CEILING_Y; else out << "null";
    out << "}\n";
    out << "}\n";
}
