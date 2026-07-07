#pragma once

#include <vector>

#include "mcwf/config.hpp"

struct Area {
    std::string kind;
    int xmin = 0;
    int xmax = 0;
    int zmin = 0;
    int zmax = 0;
};

struct RegionJob {
    int rx = 0;
    int rz = 0;
};

struct ChunkJob {
    int cx = 0;
    int cz = 0;
    int rx = 0;
    int rz = 0;
    int areaIndex = 0;
    int bucketIndex = 0;
};

Area overworldArea(const Config& cfg);

Area netherArea(const Config& cfg);

std::vector<Area> generationAreas(const Config& cfg);

bool chunkIntersectsArea(int cx, int cz, const Area& area);

bool insideArea(int x, int z, const Area& area);

bool atAreaWall(int x, int z, const Area& area);

std::vector<RegionJob> buildRegionJobs(const Config& cfg);

int totalChunks(const Config& cfg);

std::vector<ChunkJob> buildChunkJobs(const Config& cfg, const std::vector<Area>& areas, const std::vector<RegionJob>& regions);
