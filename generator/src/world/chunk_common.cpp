#include "internal/chunk_common.hpp"

#include <cstdlib>
#include <string>

#include "mcwf/chunk.hpp"
#include "mcwf/constants.hpp"

int blockIndex(int yIndex, int z, int x) {
    return (yIndex * 16 + z) * 16 + x;
}

int yIndex(int y) {
    return y - MIN_Y;
}

namespace mcwf::chunk {

void setBlock(std::vector<uint16_t>& a, int lx, int y, int lz, int id) {
    if (lx < 0 || lx >= 16 || lz < 0 || lz >= 16 || y < MIN_Y || y >= MIN_Y + WORLD_HEIGHT) {
        return;
    }
    a[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))] = static_cast<uint16_t>(id);
}

int getBlock(const std::vector<uint16_t>& a, int lx, int y, int lz) {
    if (lx < 0 || lx >= 16 || lz < 0 || lz >= 16 || y < MIN_Y || y >= MIN_Y + WORLD_HEIGHT) {
        return 0;
    }

    return a[static_cast<size_t>(blockIndex(yIndex(y), lz, lx))];
}

bool isPlantLike(int id, const BlockIds& ids) {
    return id == ids.air || id == ids.shortGrass || id == ids.fern ||
           id == ids.tallGrassLo || id == ids.tallGrassHi || id == ids.deadBush ||
           id == ids.vineNorth || id == ids.vineSouth || id == ids.vineEast || id == ids.vineWest ||
           id == ids.lilyPad || id == ids.blueOrchid || id == ids.brownMushroom || id == ids.redMushroom;
}

void placeVineRun(std::vector<uint16_t>& a, int lx, int y, int lz, int vineId, int maxLen, const BlockIds& ids) {
    int len = 0;

    for (int yy = y; yy >= MIN_Y + 1 && len < maxLen; --yy) {
        int cur = getBlock(a, lx, yy, lz);
        if (!isPlantLike(cur, ids)) {
            break;
        }
        setBlock(a, lx, yy, lz, vineId);
        ++len;
    }
}

namespace {

bool caveReplaceable(int id, const BlockIds& ids, const Blocks& blocks) {
    if (id == ids.air || id == ids.bedrock || id == ids.water || id == ids.lava ||
        id == ids.shortGrass || id == ids.fern || id == ids.tallGrassLo || id == ids.tallGrassHi ||
        id == ids.deadBush || id == ids.cactus || id == ids.sugarCane) {
        return false;
    }
    if (id >= 0 && id < static_cast<int>(blocks.names.size())) {
        const std::string& n = blocks.names[static_cast<size_t>(id)];
        if (n.find("log") != std::string::npos || n.find("leaves") != std::string::npos || n.find("snow") != std::string::npos) {
            return false;
        }
    }

    return true;
}

}  // namespace

void carveBlockAir(std::vector<uint16_t>& a, int lx, int y, int lz, const BlockIds& ids, const Blocks& blocks) {
    if (lx < 0 || lx >= 16 || lz < 0 || lz >= 16 || y < MIN_Y || y >= MIN_Y + WORLD_HEIGHT) {
        return;
    }
    int cur = getBlock(a, lx, y, lz);
    if (caveReplaceable(cur, ids, blocks)) {
        setBlock(a, lx, y, lz, ids.air);
    }
}

bool nearSurfaceLava(const std::array<std::array<TerrainSample, 16>, 16>& samples, int lx, int lz, int radius) {
    for (int dz = -radius; dz <= radius; ++dz) {
        for (int dx = -radius; dx <= radius; ++dx) {
            int nx = lx + dx;
            int nz = lz + dz;
            if (nx < 0 || nx >= 16 || nz < 0 || nz >= 16) {
                continue;
            }
            if (std::abs(dx) + std::abs(dz) > radius + 1) {
                continue;
            }
            if (samples[static_cast<size_t>(nz)][static_cast<size_t>(nx)].lava) {
                return true;
            }
        }
    }

    return false;
}

}  // namespace mcwf::chunk
