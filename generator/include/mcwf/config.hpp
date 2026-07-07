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

Config readConfig(const fs::path& path);
