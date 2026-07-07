#include "mcwf/config.hpp"

#include <fstream>
#include <functional>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "mcwf/constants.hpp"
#include "mcwf/util.hpp"

namespace {

// A setter binds one config key to the field it writes and the parse it applies.
// Building the dispatch table from member pointers keeps adding a new config
// option a one-line change instead of another rung on an if/else ladder.
using Setter = std::function<void(Config&, const std::string&)>;

Setter strField(std::string Config::* member) {
    return [member](Config& cfg, const std::string& v) { cfg.*member = v; };
}

Setter intField(int Config::* member) {
    return [member](Config& cfg, const std::string& v) { cfg.*member = std::stoi(v); };
}

Setter dblField(double Config::* member) {
    return [member](Config& cfg, const std::string& v) { cfg.*member = std::stod(v); };
}

Setter boolField(bool Config::* member) {
    return [member](Config& cfg, const std::string& v) { cfg.*member = parseBool(v); };
}

const std::unordered_map<std::string, Setter>& configSetters() {
    static const std::unordered_map<std::string, Setter> setters = {
        {"name", strField(&Config::name)},
        {"size", intField(&Config::size)},
        {"seed", intField(&Config::seed)},
        {"data_version", intField(&Config::dataVersion)},
        {"version_name", strField(&Config::versionName)},
        {"output_format", strField(&Config::outputFormat)},
        {"floor_y", intField(&Config::floorY)},
        {"water_level", intField(&Config::waterLevel)},
        {"base_height", intField(&Config::baseHeight)},
        {"hill_amp", dblField(&Config::hillAmp)},
        {"mountain_amp", dblField(&Config::mountainAmp)},
        {"mountain_amount", dblField(&Config::mountainAmount)},
        {"extreme_mountain_amount", dblField(&Config::extremeMountainAmount)},
        {"snowline_y", intField(&Config::snowlineY)},
        {"surface_lava_pools", boolField(&Config::surfaceLavaPools)},
        {"biome_frozen_cut", dblField(&Config::biomeFrozenCut)},
        {"biome_cold_cut", dblField(&Config::biomeColdCut)},
        {"biome_warm_cut", dblField(&Config::biomeWarmCut)},
        {"biome_desert_hum_cut", dblField(&Config::biomeDesertHumCut)},
        {"biome_forest_hum_cut", dblField(&Config::biomeForestHumCut)},
        {"biome_jungle_hum_cut", dblField(&Config::biomeJungleHumCut)},
        {"biome_plains_forest_weight", dblField(&Config::biomePlainsForestWeight)},
        {"biome_desert_weight", dblField(&Config::biomeDesertWeight)},
        {"biome_snow_weight", dblField(&Config::biomeSnowWeight)},
        {"biome_rare_weight", dblField(&Config::biomeRareWeight)},
        {"caves", boolField(&Config::caves)},
        {"cave_intensity", dblField(&Config::caveIntensity)},
        {"ravines", boolField(&Config::ravines)},
        {"fluids", boolField(&Config::fluids)},
        {"rivers", boolField(&Config::rivers)},
        {"river_amount", dblField(&Config::riverAmount)},
        {"lakes", boolField(&Config::lakes)},
        {"lake_amount", dblField(&Config::lakeAmount)},
        {"trees", boolField(&Config::trees)},
        {"tree_density", dblField(&Config::treeDensity)},
        {"decorations", boolField(&Config::decorations)},
        {"stone_variants", boolField(&Config::stoneVariants)},
        {"wall", boolField(&Config::wall)},
        {"wall_min_y", intField(&Config::wallMinY)},
        {"wall_max_y", intField(&Config::wallMaxY)},
        {"game_type", intField(&Config::gameType)},
        {"allow_commands", boolField(&Config::allowCommands)},
        {"deep_dark", boolField(&Config::deepDark)},
        {"nether", boolField(&Config::nether)},
        {"structures", boolField(&Config::structures)},
    };

    return setters;
}

void applyOre(Config& cfg, const std::string& value) {
    std::vector<std::string> p = split(value, ',');
    if (p.size() < 5) {
        return;
    }

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

}  // namespace

Config readConfig(const fs::path& path) {
    Config cfg;
    std::ifstream in(path);
    if (!in) {
        throw std::runtime_error("could not open config: " + path.string());
    }

    const std::unordered_map<std::string, Setter>& setters = configSetters();

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
                applyOre(cfg, value);
                continue;
            }

            auto it = setters.find(key);
            if (it != setters.end()) {
                it->second(cfg, value);
            }
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

    cfg.outputFormat = trim(cfg.outputFormat);
    if (cfg.outputFormat != "bedrock") {
        cfg.outputFormat = "java";
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
