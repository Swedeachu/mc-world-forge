#include "mcwf/blocks.hpp"

#include <string>

#include "mcwf/util.hpp"

int Blocks::add(const std::string& name) {
    std::string raw = trim(name);
    std::string base = raw;
    std::string props;
    size_t pipe = raw.find('|');
    if (pipe != std::string::npos) {
        base = raw.substr(0, pipe);
        props = raw.substr(pipe);
    }
    std::string n = mcName(base) + props;
    auto it = map.find(n);
    if (it != map.end()) {
        return it->second;
    }
    int id = static_cast<int>(names.size());
    names.push_back(n);
    map[n] = id;

    return id;
}

int Blocks::id(const std::string& name) const {
    auto it = map.find(mcName(name));
    if (it == map.end()) {
        auto stone = map.find("minecraft:stone");

        return stone == map.end() ? 0 : stone->second;
    }

    return it->second;
}

std::string deepDarkOreName(const std::string& name) {
    std::string normalized = mcName(name);
    std::string prefix;
    std::string shortName = normalized;
    size_t colon = normalized.find(':');
    if (colon != std::string::npos) {
        prefix = normalized.substr(0, colon + 1);
        shortName = normalized.substr(colon + 1);
    }
    if (shortName.rfind("deepslate_", 0) == 0 || shortName.size() < 4 || shortName.substr(shortName.size() - 4) != "_ore") {
        return normalized;
    }

    return prefix + "deepslate_" + shortName;
}

Blocks buildBlocks(const Config& cfg, BlockIds& ids) {
    Blocks b;
    ids.air = b.add("minecraft:air");
    ids.bedrock = b.add("minecraft:bedrock");
    ids.glass = b.add("minecraft:glass");
    ids.barrier = b.add("minecraft:barrier");
    ids.stone = b.add("minecraft:stone");
    ids.dirt = b.add("minecraft:dirt");
    ids.coarseDirt = b.add("minecraft:coarse_dirt");
    ids.grass = b.add("minecraft:grass_block");
    ids.grassSnow = b.add("minecraft:grass_block");
    ids.sand = b.add("minecraft:sand");
    ids.sandstone = b.add("minecraft:sandstone");
    ids.redSand = b.add("minecraft:red_sand");
    ids.redSandstone = b.add("minecraft:red_sandstone");
    ids.terracotta = b.add("minecraft:terracotta");
    ids.orangeTerracotta = b.add("minecraft:orange_terracotta");
    ids.yellowTerracotta = b.add("minecraft:yellow_terracotta");
    ids.brownTerracotta = b.add("minecraft:brown_terracotta");
    ids.redTerracotta = b.add("minecraft:red_terracotta");
    ids.whiteTerracotta = b.add("minecraft:white_terracotta");
    ids.gravel = b.add("minecraft:gravel");
    ids.clay = b.add("minecraft:clay");
    ids.water = b.add("minecraft:water");
    ids.lava = b.add("minecraft:lava");
    ids.snowLayer = b.add("minecraft:snow");
    ids.snowBlock = b.add("minecraft:snow_block");
    ids.podzol = b.add("minecraft:podzol");
    b.add("minecraft:andesite");
    b.add("minecraft:diorite");
    b.add("minecraft:granite");

    ids.deepslate = b.add("minecraft:deepslate");
    ids.tuff = b.add("minecraft:tuff");
    ids.cobbledDeepslate = b.add("minecraft:cobbled_deepslate");
    ids.sculk = b.add("minecraft:sculk");

    ids.magma = b.add("minecraft:magma_block");
    ids.netherrack = b.add("minecraft:netherrack");
    ids.soulSand = b.add("minecraft:soul_sand");
    ids.soulSoil = b.add("minecraft:soul_soil");
    ids.basalt = b.add("minecraft:basalt");
    ids.blackstone = b.add("minecraft:blackstone");
    ids.netherGoldOre = b.add("minecraft:nether_gold_ore");
    ids.netherQuartzOre = b.add("minecraft:nether_quartz_ore");
    ids.ancientDebris = b.add("minecraft:ancient_debris");
    ids.crimsonNylium = b.add("minecraft:crimson_nylium");
    ids.warpedNylium = b.add("minecraft:warped_nylium");
    ids.crimsonStem = b.add("minecraft:crimson_stem");
    ids.warpedStem = b.add("minecraft:warped_stem");
    ids.netherWartBlock = b.add("minecraft:nether_wart_block");
    ids.warpedWartBlock = b.add("minecraft:warped_wart_block");
    ids.shroomlight = b.add("minecraft:shroomlight");
    ids.glowstone = b.add("minecraft:glowstone");
    ids.boneBlock = b.add("minecraft:bone_block");

    ids.oakLog = b.add("minecraft:oak_log");
    ids.oakLeaves = b.add("minecraft:oak_leaves");
    ids.birchLog = b.add("minecraft:birch_log");
    ids.birchLeaves = b.add("minecraft:birch_leaves");
    ids.spruceLog = b.add("minecraft:spruce_log");
    ids.spruceLeaves = b.add("minecraft:spruce_leaves");
    ids.jungleLog = b.add("minecraft:jungle_log");
    ids.jungleLeaves = b.add("minecraft:jungle_leaves");
    ids.darkOakLog = b.add("minecraft:dark_oak_log");
    ids.darkOakLeaves = b.add("minecraft:dark_oak_leaves");

    ids.shortGrass = b.add(cfg.dataVersion >= 3698 ? "minecraft:short_grass" : "minecraft:grass");
    ids.fern = b.add("minecraft:fern");
    ids.tallGrassLo = b.add("minecraft:tall_grass|half=lower");
    ids.tallGrassHi = b.add("minecraft:tall_grass|half=upper");
    ids.vineNorth = b.add("minecraft:vine|north=true,east=false,south=false,west=false,up=false");
    ids.vineEast = b.add("minecraft:vine|north=false,east=true,south=false,west=false,up=false");
    ids.vineSouth = b.add("minecraft:vine|north=false,east=false,south=true,west=false,up=false");
    ids.vineWest = b.add("minecraft:vine|north=false,east=false,south=false,west=true,up=false");
    ids.mud = b.add("minecraft:mud");
    ids.lilyPad = b.add("minecraft:lily_pad");
    ids.blueOrchid = b.add("minecraft:blue_orchid");
    ids.brownMushroom = b.add("minecraft:brown_mushroom");
    ids.redMushroom = b.add("minecraft:red_mushroom");
    ids.deadBush = b.add("minecraft:dead_bush");
    ids.cactus = b.add("minecraft:cactus");
    ids.sugarCane = b.add("minecraft:sugar_cane");
    const char* flowers[] = {"dandelion", "poppy", "cornflower", "azure_bluet", "oxeye_daisy"};

    for (size_t i = 0; i < ids.flowers.size(); ++i) {
        ids.flowers[i] = b.add(std::string("minecraft:") + flowers[i]);
    }

    for (const OreCfg& ore : cfg.ores) {
        b.add(ore.name);
        b.add(deepDarkOreName(ore.name));
    }

    return b;
}
