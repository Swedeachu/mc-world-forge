#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <mutex>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace fs = std::filesystem;

constexpr int MIN_Y = -64;
constexpr int WORLD_HEIGHT = 384;
constexpr int MIN_SECTION = MIN_Y / 16;
constexpr int N_SECTIONS = WORLD_HEIGHT / 16;
constexpr int NETHER_BOX_SIZE = 1000;
constexpr int NETHER_BOX_GAP = 500;
constexpr int NETHER_BOX_CEILING_Y = 127;
constexpr int SECTOR_BYTES = 4096;

struct OreCfg {
    std::string name = "diamond_ore";
    int yMin = -19;
    int yMax = 5;
    double veinsPerChunk = 8.0;
    int veinSize = 5;
    std::string bias = "uniform";
};

struct Config {
    std::string name = "Forged World";
    int size = 1000;
    int seed = 0;
    int dataVersion = 3465;
    std::string versionName = "1.20.1";
    int floorY = -20;
    int waterLevel = 118;
    int baseHeight = 128;
    double hillAmp = 8.0;
    double mountainAmp = 74.0;
    double mountainAmount = 0.30;
    double extremeMountainAmount = 0.20;
    int snowlineY = 246;
    double biomeFrozenCut = 0.20;
    double biomeColdCut = 0.36;
    double biomeWarmCut = 0.70;
    double biomeDesertHumCut = 0.42;
    double biomeForestHumCut = 0.62;
    double biomeJungleHumCut = 0.82;
    double biomePlainsForestWeight = 50.0;
    double biomeDesertWeight = 20.0;
    double biomeSnowWeight = 15.0;
    double biomeRareWeight = 15.0;
    bool surfaceLavaPools = true;
    bool caves = true;
    double caveIntensity = 1.0;
    bool ravines = true;
    bool fluids = true;
    bool rivers = true;
    double riverAmount = 0.4;
    bool lakes = true;
    double lakeAmount = 0.4;
    bool trees = true;
    double treeDensity = 1.0;
    bool decorations = true;
    bool stoneVariants = true;
    bool wall = true;
    int wallMinY = -64;
    int wallMaxY = 319;
    int gameType = 0;
    bool allowCommands = true;
    bool deepDark = false;
    bool nether = false;
    bool structures = false;
    std::vector<OreCfg> ores;
};

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

struct Blocks {
    std::vector<std::string> names;
    std::unordered_map<std::string, int> map;

    int add(const std::string& name);
    int id(const std::string& name) const;
};

struct BlockIds {
    int air = 0;
    int bedrock = 0;
    int stone = 0;
    int deepslate = 0;
    int tuff = 0;
    int cobbledDeepslate = 0;
    int dirt = 0;
    int coarseDirt = 0;
    int grass = 0;
    int grassSnow = 0;
    int sand = 0;
    int sandstone = 0;
    int redSand = 0;
    int redSandstone = 0;
    int terracotta = 0;
    int orangeTerracotta = 0;
    int yellowTerracotta = 0;
    int brownTerracotta = 0;
    int redTerracotta = 0;
    int whiteTerracotta = 0;
    int gravel = 0;
    int clay = 0;
    int water = 0;
    int lava = 0;
    int magma = 0;
    int sculk = 0;
    int snowLayer = 0;
    int snowBlock = 0;
    int podzol = 0;
    int oakLog = 0;
    int oakLeaves = 0;
    int birchLog = 0;
    int birchLeaves = 0;
    int spruceLog = 0;
    int spruceLeaves = 0;
    int jungleLog = 0;
    int jungleLeaves = 0;
    int darkOakLog = 0;
    int darkOakLeaves = 0;
    int cactus = 0;
    int sugarCane = 0;
    int deadBush = 0;
    int shortGrass = 0;
    int fern = 0;
    int tallGrassLo = 0;
    int tallGrassHi = 0;
    int vineNorth = 0;
    int vineSouth = 0;
    int vineEast = 0;
    int vineWest = 0;
    int mud = 0;
    int lilyPad = 0;
    int blueOrchid = 0;
    int brownMushroom = 0;
    int redMushroom = 0;
    std::array<int, 5> flowers{};
};

extern const std::vector<std::string> BIOMES;

enum BiomeId {
    BIOME_PLAINS = 0,
    BIOME_FOREST = 1,
    BIOME_BIRCH = 2,
    BIOME_DESERT = 3,
    BIOME_SAVANNA = 4,
    BIOME_JUNGLE = 5,
    BIOME_SNOW = 6,
    BIOME_TAIGA = 7,
    BIOME_BADLANDS = 8,
    BIOME_RIVER = 9,
    BIOME_SWAMP = 10,
    BIOME_HILLS = 11,
    BIOME_DEEP_DARK = 12,
    BIOME_NETHER = 13,
    BIOME_CRIMSON = 14,
    BIOME_WARPED = 15,
    BIOME_SOUL = 16,
    BIOME_BASALT = 17,
};

struct TerrainSample {
    int height = 64;
    int biome = BIOME_PLAINS;
    bool river = false;
    bool lake = false;
    bool lava = false;
    bool beach = false;
    int waterSurface = 0;
    int lavaSurface = 0;
    bool badlands = false;
    bool desert = false;
    bool snowy = false;
    double mountain = 0.0;
    double badlandsStrength = 0.0;
    int topId = 0;
    int subId = 0;
};

struct PreviewFrame {
    int xmin = 0;
    int xmax = 0;
    int zmin = 0;
    int zmax = 0;
    int scale = 1;
    int width = 1;
    int height = 1;
    fs::path path;
    std::vector<uint8_t> image;
    std::mutex mutex;

    PreviewFrame() = default;
    PreviewFrame(const PreviewFrame&) = delete;
    PreviewFrame& operator=(const PreviewFrame&) = delete;
    PreviewFrame(PreviewFrame&& other) noexcept
        : xmin(other.xmin), xmax(other.xmax), zmin(other.zmin), zmax(other.zmax),
          scale(other.scale), width(other.width), height(other.height),
          path(std::move(other.path)), image(std::move(other.image)) {}
    PreviewFrame& operator=(PreviewFrame&& other) noexcept {
        if (this != &other) {
            xmin = other.xmin;
            xmax = other.xmax;
            zmin = other.zmin;
            zmax = other.zmax;
            scale = other.scale;
            width = other.width;
            height = other.height;
            path = std::move(other.path);
            image = std::move(other.image);
        }
        return *this;
    }
};

struct RegionWriter {
    int rx = 0;
    int rz = 0;
    std::map<int, std::vector<uint8_t>> chunks;

    RegionWriter(int regionX, int regionZ);
    void addChunk(int cx, int cz, std::vector<uint8_t>&& nbtPayload);
    void write(const fs::path& dir) const;
};

struct ChunkJob {
    int cx = 0;
    int cz = 0;
    int rx = 0;
    int rz = 0;
    int areaIndex = 0;
    int bucketIndex = 0;
};

struct RegionBucket {
    RegionWriter writer;
    std::mutex mutex;

    RegionBucket(int regionX, int regionZ) : writer(regionX, regionZ) {}
    RegionBucket(const RegionBucket&) = delete;
    RegionBucket& operator=(const RegionBucket&) = delete;
};

std::string trim(const std::string& s);
bool parseBool(const std::string& v);
std::vector<std::string> split(const std::string& s, char delim);
int floorDiv(int a, int b);
int clampInt(int v, int lo, int hi);
double clampDouble(double v, double lo, double hi);
std::string mcName(const std::string& raw);

Config readConfig(const fs::path& path);
Area overworldArea(const Config& cfg);
Area netherArea(const Config& cfg);
std::vector<Area> generationAreas(const Config& cfg);
bool chunkIntersectsArea(int cx, int cz, const Area& area);
bool insideArea(int x, int z, const Area& area);
bool atAreaWall(int x, int z, const Area& area);
std::vector<RegionJob> buildRegionJobs(const Config& cfg);
int totalChunks(const Config& cfg);
std::vector<ChunkJob> buildChunkJobs(const Config& cfg, const std::vector<Area>& areas, const std::vector<RegionJob>& regions);

uint32_t mix32(uint32_t n);
uint32_t hashInts(int64_t x, int64_t y, int64_t z, int seed, uint32_t salt);
double hash01(int64_t x, int64_t y, int64_t z, int seed, uint32_t salt = 0);
double smooth(double t);
double lerp(double a, double b, double t);
double value2d(double x, double z, int seed, uint32_t salt = 0);
double value3d(double x, double y, double z, int seed, uint32_t salt = 0);
double fbm2d(double x, double z, int seed, int octaves, double freq, double gain = 0.5);
double fbm3d(double x, double y, double z, int seed, int octaves, double freq, double gain = 0.5);
double rand01(int x, int y, int z, const Config& cfg, uint32_t salt);
double smoothstep01(double t);

std::string deepDarkOreName(const std::string& name);
Blocks buildBlocks(const Config& cfg, BlockIds& ids);
TerrainSample sampleTerrain(const Config& cfg, int x, int z);
void sampleTerrainChunk(const Config& cfg, const Blocks& blocks, const BlockIds& ids, int cx, int cz,
                        std::array<std::array<TerrainSample, 16>, 16>& out);
bool caveCarves(const Config& cfg, int x, int y, int z, int surfaceY, int floorY);
int netherBiome(const Config& cfg, int x, int z);
int blockIndex(int yIndex, int z, int x);
int yIndex(int y);
void generateOverworldChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                            int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes);
void generateNetherChunk(const Config& cfg, const Area& area, const Blocks& blocks, const BlockIds& ids,
                         int cx, int cz, std::vector<uint16_t>& outBlocks, std::vector<uint16_t>& outBiomes);

void putU8(std::vector<uint8_t>& out, uint8_t v);
void putI32(std::vector<uint8_t>& out, int32_t v);
std::vector<uint64_t> packIndices(const std::vector<uint32_t>& indices, int bits);
std::vector<uint8_t> buildChunkNbt(const Config& cfg, const Blocks& blocks, int cx, int cz,
                                   const std::vector<uint16_t>& blockIds,
                                   const std::vector<uint16_t>& biomeIds);
std::vector<uint8_t> generateChunkPayload(const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                                          const Area& area, int cx, int cz,
                                          std::vector<uint16_t>& blockIds, std::vector<uint16_t>& biomeIds);

bool regionChunkInAnyArea(int cx, int cz, const std::vector<Area>& areas, const Area** outArea);
int generateRegion(const Config& cfg, const Blocks& blocks, const BlockIds& ids,
                   const std::vector<Area>& areas, const RegionJob& job, const fs::path& regionDir,
                   const std::function<void(int, int)>& onChunkDone = {});
void reportProgress(double p, const std::string& msg);
void reportPreviewReady();
int spawnY(const Config& cfg);
void writeResultJson(const Config& cfg, const fs::path& outDir, int chunks, int regions, int workers, double seconds);
void previewColor(const Config& cfg, int x, int z, uint8_t& r, uint8_t& g, uint8_t& b);
PreviewFrame createPreviewFrame(const Config& cfg, const fs::path& outDir);
void clearPreviewFrame(PreviewFrame& frame);
void updatePreviewForChunk(const Config& cfg, PreviewFrame& frame, int cx, int cz);
void updatePreviewFromChunkBlocks(const Config& cfg, const BlockIds& ids, PreviewFrame& frame, int cx, int cz,
                                  const std::vector<uint16_t>& blockIds, const std::vector<uint16_t>& biomeIds);
void flushPreviewFrame(PreviewFrame& frame);
void writePreview(const Config& cfg, const fs::path& outDir);
int runGenerator(int argc, char** argv);
