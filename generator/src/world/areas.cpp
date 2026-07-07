#include "mcwf/common.hpp"

Area overworldArea(const Config& cfg) {
    int xmin = -(cfg.size / 2);
    int xmax = xmin + cfg.size - 1;
    return {"overworld", xmin, xmax, xmin, xmax};
}

Area netherArea(const Config& cfg) {
    Area ow = overworldArea(cfg);
    int nxmin = ow.xmax + 1 + NETHER_BOX_GAP;
    int nxmax = nxmin + NETHER_BOX_SIZE - 1;
    int nzmin = -(NETHER_BOX_SIZE / 2);
    int nzmax = nzmin + NETHER_BOX_SIZE - 1;
    return {"nether", nxmin, nxmax, nzmin, nzmax};
}

std::vector<Area> generationAreas(const Config& cfg) {
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
    return x1 >= area.xmin && x0 <= area.xmax && z1 >= area.zmin && z0 <= area.zmax;
}

bool insideArea(int x, int z, const Area& area) {
    return x >= area.xmin && x <= area.xmax && z >= area.zmin && z <= area.zmax;
}

bool atAreaWall(int x, int z, const Area& area) {
    return x == area.xmin || x == area.xmax || z == area.zmin || z == area.zmax;
}

std::vector<RegionJob> buildRegionJobs(const Config& cfg) {
    std::set<std::pair<int, int>> seen;
    for (const Area& a : generationAreas(cfg)) {
        int cXMin = floorDiv(a.xmin, 16);
        int cXMax = floorDiv(a.xmax, 16);
        int cZMin = floorDiv(a.zmin, 16);
        int cZMax = floorDiv(a.zmax, 16);
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
    for (const Area& a : generationAreas(cfg)) {
        int cXMin = floorDiv(a.xmin, 16);
        int cXMax = floorDiv(a.xmax, 16);
        int cZMin = floorDiv(a.zmin, 16);
        int cZMax = floorDiv(a.zmax, 16);
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
        int cXMin = floorDiv(a.xmin, 16);
        int cXMax = floorDiv(a.xmax, 16);
        int cZMin = floorDiv(a.zmin, 16);
        int cZMax = floorDiv(a.zmax, 16);
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
