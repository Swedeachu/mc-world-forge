#pragma once

#include <filesystem>

#include "mcwf/config.hpp"

namespace fs = std::filesystem;

fs::path writeWildernessJson(const Config& cfg, const fs::path& outDir);
