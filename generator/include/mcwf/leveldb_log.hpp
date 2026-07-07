#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

struct LevelDbLogEntry {
    std::string key;
    std::vector<uint8_t> value;
};

void writeLevelDbLogStore(const fs::path& dbDir, std::vector<LevelDbLogEntry>&& entries);
