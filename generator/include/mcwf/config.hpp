#pragma once

#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

struct OreCfg {
    std::string name = "diamond_ore";
    int yMin = -19;
    int yMax = 5;
    double veinsPerChunk = 8.0;
    int veinSize = 5;
    std::string bias = "uniform";
};

struct FuhcBatchCfg {
    int size = 50;
    int count = 1;
    int glassWallHeight = 10;
};

struct WorldSpawn {
    int x = 0;
    int y = 64;
    int z = 0;
};

struct Config {
    std::string generator = "vanilla";
    std::string name = "Forged World";
    int size = 1000;
    int seed = 0;
    int dataVersion = 3465;
    std::string versionName = "1.20.1";
    std::string outputFormat = "java";
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
    int biomeOverride = -1;
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

    int fuhcBaseY = 80;
    int fuhcDepth = 5;
    double fuhcSurfaceRelief = 2.0;
    int fuhcBedrockWallHeight = 5;
    int fuhcGlassWallHeight = 10;
    bool fuhcGlassRoof = true;
    int fuhcArenaGap = 120;
    int fuhcSpawnInset = 8;
    bool fuhcVariedBiomes = true;
    std::vector<FuhcBatchCfg> fuhcBatches;

    // "square" packs every arena into one centred grid on a shared pitch.
    // "rows" gives each batch its own band of rows, chunk aligned, so every
    // arena footprint (barrier shell included) starts on a chunk edge and is
    // separated from its neighbours by fuhcChunkGap empty chunks.
    std::string fuhcLayout = "square";
    int fuhcChunkGap = 8;
    int fuhcRowColumns = 0;
    int fuhcOriginX = 0;
    int fuhcOriginZ = 0;

    // "classic" is bedrock below a glass band. "pattern" builds the whole wall,
    // floor to top, from fuhcWallPattern repeated along the diagonal.
    std::string fuhcWallStyle = "classic";
    std::vector<std::string> fuhcWallPattern;
    std::string fuhcRoofBlock = "glass";
};

Config readConfig(const fs::path& path);
