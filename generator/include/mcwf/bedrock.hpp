#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"

namespace fs = std::filesystem;

struct BedrockKv {
    std::string key;
    std::vector<uint8_t> value;
};

void appendBedrockChunk(const Config& cfg, const Blocks& blocks, int cx, int cz,
                        const std::vector<uint16_t>& blockIds,
                        const std::vector<uint16_t>& biomeIds,
                        std::vector<BedrockKv>& out);

void writeBedrockWorld(const Config& cfg, const fs::path& outDir, std::vector<BedrockKv>&& entries);
