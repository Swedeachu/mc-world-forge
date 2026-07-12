#include "mcwf/area.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <utility>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/fuhc.hpp"
#include "mcwf/util.hpp"

namespace {

int generationPadding(const Area& area) {
    return area.kind == "fuhc" ? FUHC_BARRIER_LAYERS : 0;
}

void generationBounds(const Area& area, int& xmin, int& xmax, int& zmin, int& zmax) {
    int padding = generationPadding(area);
    xmin = area.xmin - padding;
    xmax = area.xmax + padding;
    zmin = area.zmin - padding;
    zmax = area.zmax + padding;
}

}  // namespace

Area overworldArea(const Config& cfg) {
    int xmin = -(cfg.size / 2);
    int xmax = xmin + cfg.size - 1;
    Area area;
    area.kind = "overworld";
    area.xmin = xmin;
    area.xmax = xmax;
    area.zmin = xmin;
    area.zmax = xmax;

    return area;
}

Area netherArea(const Config& cfg) {
    Area ow = overworldArea(cfg);
    int nxmin = ow.xmax + 1 + NETHER_BOX_GAP;
    int nxmax = nxmin + NETHER_BOX_SIZE - 1;
    int nzmin = -(NETHER_BOX_SIZE / 2);
    int nzmax = nzmin + NETHER_BOX_SIZE - 1;
    Area area;
    area.kind = "nether";
    area.xmin = nxmin;
    area.xmax = nxmax;
    area.zmin = nzmin;
    area.zmax = nzmax;

    return area;
}

std::vector<Area> fuhcAreas(const Config& cfg, bool buildBiomeMaps) {
    struct ArenaSpec {
        int size = 50;
        int glassWallHeight = 10;
    };

    std::vector<ArenaSpec> specs;
    int maxSize = 16;

    for (const FuhcBatchCfg& batch : cfg.fuhcBatches) {
        for (int i = 0; i < batch.count; ++i) {
            specs.push_back({batch.size, batch.glassWallHeight});
            maxSize = std::max(maxSize, batch.size);
        }
    }
    if (specs.empty()) {
        specs.push_back({50, cfg.fuhcGlassWallHeight});
        maxSize = 50;
    }

    int count = static_cast<int>(specs.size());
    int columns = static_cast<int>(std::ceil(std::sqrt(static_cast<double>(count))));
    int rows = (count + columns - 1) / columns;
    int pitch = maxSize + cfg.fuhcArenaGap;
    int firstCenterX = -((columns - 1) * pitch) / 2;
    int firstCenterZ = -((rows - 1) * pitch) / 2;
    std::vector<FuhcBiomeAssignment> biomes = planFuhcBiomes(cfg, count);

    std::vector<Area> out;
    out.reserve(specs.size());

    for (int i = 0; i < count; ++i) {
        int column = i % columns;
        int row = i / columns;
        const ArenaSpec& spec = specs[static_cast<size_t>(i)];
        int size = spec.size;
        int centerX = firstCenterX + column * pitch;
        int centerZ = firstCenterZ + row * pitch;
        int xmin = centerX - size / 2;
        int zmin = centerZ - size / 2;
        int xmax = xmin + size - 1;
        int zmax = zmin + size - 1;
        const FuhcBiomeAssignment& biome = biomes[static_cast<size_t>(i)];
        Area area;
        area.kind = "fuhc";
        area.xmin = xmin;
        area.xmax = xmax;
        area.zmin = zmin;
        area.zmax = zmax;
        area.index = i;
        area.size = size;
        area.fuhcGlassWallHeight = spec.glassWallHeight;
        area.centerX = centerX;
        area.centerZ = centerZ;
        area.biomeGroup = biome.group;
        area.biome = biome.biome;
        if (buildBiomeMaps) {
            prepareFuhcBiomeBlend(cfg, area);
        }
        out.push_back(std::move(area));
    }

    return out;
}

std::vector<Area> generationAreas(const Config& cfg, bool buildFuhcBiomeMaps) {
    if (cfg.generator == "fuhc") {
        return fuhcAreas(cfg, buildFuhcBiomeMaps);
    }

    std::vector<Area> out;
    out.push_back(overworldArea(cfg));
    if (cfg.nether) {
        out.push_back(netherArea(cfg));
    }

    return out;
}

bool chunkIntersectsArea(int cx, int cz, const Area& area) {
    int x0 = cx * 16;
    int x1 = x0 + 15;
    int z0 = cz * 16;
    int z1 = z0 + 15;
    int xmin, xmax, zmin, zmax;
    generationBounds(area, xmin, xmax, zmin, zmax);

    return x1 >= xmin && x0 <= xmax && z1 >= zmin && z0 <= zmax;
}

bool insideArea(int x, int z, const Area& area) {
    return x >= area.xmin && x <= area.xmax && z >= area.zmin && z <= area.zmax;
}

bool atAreaWall(int x, int z, const Area& area) {
    return x == area.xmin || x == area.xmax || z == area.zmin || z == area.zmax;
}

std::vector<RegionJob> buildRegionJobs(const Config& cfg) {
    std::set<std::pair<int, int>> seen;

    for (const Area& a : generationAreas(cfg, false)) {
        int xmin, xmax, zmin, zmax;
        generationBounds(a, xmin, xmax, zmin, zmax);
        int cXMin = floorDiv(xmin, 16);
        int cXMax = floorDiv(xmax, 16);
        int cZMin = floorDiv(zmin, 16);
        int cZMax = floorDiv(zmax, 16);

        for (int rx = floorDiv(cXMin, 32); rx <= floorDiv(cXMax, 32); ++rx) {
            for (int rz = floorDiv(cZMin, 32); rz <= floorDiv(cZMax, 32); ++rz) {
                seen.insert({rx, rz});
            }
        }
    }
    std::vector<RegionJob> jobs;

    for (auto [rx, rz] : seen) {
        jobs.push_back({rx, rz});
    }

    return jobs;
}

int totalChunks(const Config& cfg) {
    int total = 0;

    for (const Area& a : generationAreas(cfg, false)) {
        int xmin, xmax, zmin, zmax;
        generationBounds(a, xmin, xmax, zmin, zmax);
        int cXMin = floorDiv(xmin, 16);
        int cXMax = floorDiv(xmax, 16);
        int cZMin = floorDiv(zmin, 16);
        int cZMax = floorDiv(zmax, 16);
        total += (cXMax - cXMin + 1) * (cZMax - cZMin + 1);
    }

    return total;
}

std::vector<ChunkJob> buildChunkJobs(const Config& cfg, const std::vector<Area>& areas, const std::vector<RegionJob>& regions) {
    (void)cfg;
    std::map<std::pair<int, int>, int> bucketIndex;

    for (size_t i = 0; i < regions.size(); ++i) {
        bucketIndex[{regions[i].rx, regions[i].rz}] = static_cast<int>(i);
    }

    std::set<std::pair<int, int>> seen;
    std::vector<ChunkJob> jobs;

    for (size_t areaIndex = 0; areaIndex < areas.size(); ++areaIndex) {
        const Area& a = areas[areaIndex];
        int xmin, xmax, zmin, zmax;
        generationBounds(a, xmin, xmax, zmin, zmax);
        int cXMin = floorDiv(xmin, 16);
        int cXMax = floorDiv(xmax, 16);
        int cZMin = floorDiv(zmin, 16);
        int cZMax = floorDiv(zmax, 16);

        for (int cz = cZMin; cz <= cZMax; ++cz) {
            for (int cx = cXMin; cx <= cXMax; ++cx) {
                if (!seen.insert({cx, cz}).second) {
                    continue;
                }
                int rx = floorDiv(cx, 32);
                int rz = floorDiv(cz, 32);
                auto it = bucketIndex.find({rx, rz});
                if (it == bucketIndex.end()) {
                    continue;
                }
                jobs.push_back({cx, cz, rx, rz, static_cast<int>(areaIndex), it->second});
            }
        }
    }

    return jobs;
}
