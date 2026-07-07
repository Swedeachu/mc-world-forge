#include "mcwf/common.hpp"

Config readConfig(const fs::path& path) {
    Config cfg;
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("could not open config: " + path.string());
    }

    std::string line;
    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') {
            continue;
        }
        size_t eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        std::string key = trim(line.substr(0, eq));
        std::string value = trim(line.substr(eq + 1));

        try {
            if (key == "ore") {
                std::vector<std::string> p = split(value, ',');
                if (p.size() >= 5) {
                    OreCfg ore;
                    ore.name = p[0];
                    ore.yMin = std::stoi(p[1]);
                    ore.yMax = std::stoi(p[2]);
                    ore.veinsPerChunk = std::stod(p[3]);
                    ore.veinSize = std::stoi(p[4]);
                    if (p.size() >= 6) {
                        ore.bias = p[5];
                    }
                    if (ore.veinsPerChunk > 0.0) {
                        cfg.ores.push_back(ore);
                    }
                }
            } else if (key == "name") cfg.name = value;
            else if (key == "size") cfg.size = std::stoi(value);
            else if (key == "seed") cfg.seed = std::stoi(value);
            else if (key == "data_version") cfg.dataVersion = std::stoi(value);
            else if (key == "version_name") cfg.versionName = value;
            else if (key == "floor_y") cfg.floorY = std::stoi(value);
            else if (key == "water_level") cfg.waterLevel = std::stoi(value);
            else if (key == "base_height") cfg.baseHeight = std::stoi(value);
            else if (key == "hill_amp") cfg.hillAmp = std::stod(value);
            else if (key == "mountain_amp") cfg.mountainAmp = std::stod(value);
            else if (key == "mountain_amount") cfg.mountainAmount = std::stod(value);
            else if (key == "extreme_mountain_amount") cfg.extremeMountainAmount = std::stod(value);
            else if (key == "snowline_y") cfg.snowlineY = std::stoi(value);
            else if (key == "surface_lava_pools") cfg.surfaceLavaPools = parseBool(value);
            else if (key == "biome_frozen_cut") cfg.biomeFrozenCut = std::stod(value);
            else if (key == "biome_cold_cut") cfg.biomeColdCut = std::stod(value);
            else if (key == "biome_warm_cut") cfg.biomeWarmCut = std::stod(value);
            else if (key == "biome_desert_hum_cut") cfg.biomeDesertHumCut = std::stod(value);
            else if (key == "biome_forest_hum_cut") cfg.biomeForestHumCut = std::stod(value);
            else if (key == "biome_jungle_hum_cut") cfg.biomeJungleHumCut = std::stod(value);
            else if (key == "biome_plains_forest_weight") cfg.biomePlainsForestWeight = std::stod(value);
            else if (key == "biome_desert_weight") cfg.biomeDesertWeight = std::stod(value);
            else if (key == "biome_snow_weight") cfg.biomeSnowWeight = std::stod(value);
            else if (key == "biome_rare_weight") cfg.biomeRareWeight = std::stod(value);
            else if (key == "caves") cfg.caves = parseBool(value);
            else if (key == "cave_intensity") cfg.caveIntensity = std::stod(value);
            else if (key == "ravines") cfg.ravines = parseBool(value);
            else if (key == "fluids") cfg.fluids = parseBool(value);
            else if (key == "rivers") cfg.rivers = parseBool(value);
            else if (key == "river_amount") cfg.riverAmount = std::stod(value);
            else if (key == "lakes") cfg.lakes = parseBool(value);
            else if (key == "lake_amount") cfg.lakeAmount = std::stod(value);
            else if (key == "trees") cfg.trees = parseBool(value);
            else if (key == "tree_density") cfg.treeDensity = std::stod(value);
            else if (key == "decorations") cfg.decorations = parseBool(value);
            else if (key == "stone_variants") cfg.stoneVariants = parseBool(value);
            else if (key == "wall") cfg.wall = parseBool(value);
            else if (key == "wall_min_y") cfg.wallMinY = std::stoi(value);
            else if (key == "wall_max_y") cfg.wallMaxY = std::stoi(value);
            else if (key == "game_type") cfg.gameType = std::stoi(value);
            else if (key == "allow_commands") cfg.allowCommands = parseBool(value);
            else if (key == "deep_dark") cfg.deepDark = parseBool(value);
            else if (key == "nether") cfg.nether = parseBool(value);
            else if (key == "structures") cfg.structures = parseBool(value);
        } catch (...) {
            // Keep the same forgiving behavior as the Python form parser.
        }
    }

    if (cfg.ores.empty()) {
        cfg.ores = {
            {"coal_ore", -64, 319, 15.0, 12, "high"},
            {"iron_ore", -64, 319, 13.0, 9, "mid"},
            {"copper_ore", -16, 96, 8.0, 10, "mid"},
            {"gold_ore", -64, 64, 7.0, 8, "low"},
            {"redstone_ore", -64, 16, 11.0, 8, "low"},
            {"lapis_ore", -64, 16, 5.5, 7, "low"},
            {"diamond_ore", -64, 16, 10.0, 6, "low"},
            {"emerald_ore", -64, 48, 4.0, 5, "high"},
        };
    }

    cfg.biomePlainsForestWeight = clampDouble(cfg.biomePlainsForestWeight, 0.0, 100.0);
    cfg.biomeDesertWeight = clampDouble(cfg.biomeDesertWeight, 0.0, 100.0);
    cfg.biomeSnowWeight = clampDouble(cfg.biomeSnowWeight, 0.0, 100.0);
    cfg.biomeRareWeight = clampDouble(cfg.biomeRareWeight, 0.0, 100.0);
    if (cfg.biomePlainsForestWeight + cfg.biomeDesertWeight + cfg.biomeSnowWeight + cfg.biomeRareWeight <= 0.001) {
        cfg.biomePlainsForestWeight = 50.0;
        cfg.biomeDesertWeight = 20.0;
        cfg.biomeSnowWeight = 15.0;
        cfg.biomeRareWeight = 15.0;
    }

    cfg.size = clampInt(cfg.size, 16, 6000);
    cfg.floorY = clampInt(cfg.floorY, MIN_Y + 1, 256);
    cfg.wallMinY = clampInt(cfg.wallMinY, MIN_Y, 319);
    cfg.wallMaxY = clampInt(cfg.wallMaxY, MIN_Y, 319);
    if (cfg.wallMinY > cfg.wallMaxY) {
        std::swap(cfg.wallMinY, cfg.wallMaxY);
    }
    return cfg;
}
