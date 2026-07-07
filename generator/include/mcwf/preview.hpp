#pragma once

#include <cstdint>
#include <filesystem>
#include <mutex>
#include <vector>

#include "mcwf/blocks.hpp"
#include "mcwf/config.hpp"

namespace fs = std::filesystem;

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

void previewColor(const Config& cfg, int x, int z, uint8_t& r, uint8_t& g, uint8_t& b);

PreviewFrame createPreviewFrame(const Config& cfg, const fs::path& outDir);

void clearPreviewFrame(PreviewFrame& frame);

void updatePreviewForChunk(const Config& cfg, PreviewFrame& frame, int cx, int cz);

void updatePreviewFromChunkBlocks(const Config& cfg, const BlockIds& ids, PreviewFrame& frame, int cx, int cz,
                                  const std::vector<uint16_t>& blockIds, const std::vector<uint16_t>& biomeIds);

void flushPreviewFrame(PreviewFrame& frame);

void writePreview(const Config& cfg, const fs::path& outDir);
