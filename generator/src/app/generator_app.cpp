#include "mcwf/app.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "mcwf/area.hpp"
#include "mcwf/blocks.hpp"
#include "mcwf/chunk.hpp"
#include "mcwf/config.hpp"
#include "mcwf/fuhc.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/nbt.hpp"
#include "mcwf/preview.hpp"
#include "mcwf/region.hpp"
#include "mcwf/util.hpp"
#include "mcwf/world_writer.hpp"

namespace fs = std::filesystem;

int parseWorkers(int argc, char** argv, int totalChunks) {
    int requested = 0;
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--workers") {
            try {
                requested = std::stoi(argv[i + 1]);
            } catch (...) {
                requested = 0;
            }
        }
    }

    int hw = static_cast<int>(std::max(1u, std::thread::hardware_concurrency()));
    int workers = requested;
    if (workers <= 0) {
        // The generator is memory/cache heavy and also streams preview and NBT
        // output. Using every logical processor can be slower than a moderate
        // worker count on real Windows boxes and in CPU-quota environments.
        // Manual --workers still overrides this cap for experiments.
        workers = std::min(hw, 8);
        if (totalChunks < 512) {
            workers = std::min(workers, 4);
        }
    }
    return clampInt(workers, 1, 64);
}

fs::path argValue(int argc, char** argv, const std::string& key) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == key) {
            return fs::path(argv[i + 1]);
        }
    }
    return {};
}

int runGenerator(int argc, char** argv) {
    try {
        fs::path cfgPath = argValue(argc, argv, "--config");
        fs::path outDir = argValue(argc, argv, "--out");
        
        if (cfgPath.empty() || outDir.empty()) {
            std::cerr << "usage: mc_world_forge_generator --config <config.ini> --out <world-dir> [--workers N]\n";
            return 2;
        }

        auto start = std::chrono::steady_clock::now();
        Config cfg = readConfig(cfgPath);
        BlockIds ids;
        Blocks blocks = buildBlocks(cfg, ids);
        std::vector<Area> areas = generationAreas(cfg);
        std::vector<RegionJob> regions = buildRegionJobs(cfg);
        std::vector<ChunkJob> chunks = buildChunkJobs(cfg, areas, regions);
        std::vector<std::array<int, 2>> fuhcSpawnYs;
        if (cfg.generator == "fuhc") {
            fuhcSpawnYs.assign(areas.size(), {MIN_Y, MIN_Y});
        }
        int total = static_cast<int>(chunks.size());
        int workers = std::min(parseWorkers(argc, argv, total), std::max(1, total));

        fs::create_directories(outDir);
        fs::path regionDir = outDir / "region";
        if (cfg.outputFormat == "bedrock") {
            fs::remove_all(regionDir);
            fs::remove_all(outDir / "db");
        } else {
            fs::create_directories(regionDir);
            fs::create_directories(outDir / "data");

            for (const auto& e : fs::directory_iterator(regionDir)) {
                if (e.path().extension() == ".mca") {
                    fs::remove(e.path());
                }
            }
        }

        std::vector<std::unique_ptr<SharedWorldBucket>> buckets = createWorldBuckets(cfg, regions);

        std::string generatorLabel = cfg.generator == "fuhc" ? "FUHC" : "vanilla";
        reportProgress(0.01, "Planning native " + generatorLabel + " " + cfg.outputFormat + " chunk jobs");
        PreviewFrame preview = createPreviewFrame(cfg, outDir);
        flushPreviewFrame(preview);
        reportPreviewReady();
        reportProgress(0.025, "Generating " + std::to_string(total) + " chunks with " + std::to_string(workers) + " worker threads");

        std::atomic<int> nextChunk{0};
        std::atomic<int> doneChunks{0};
        std::atomic<int> lastReportedChunks{0};
        std::atomic<int> lastPreviewChunks{0};
        std::atomic<int64_t> lastPreviewMs{0};
        int reportEvery = std::max(8, total / 200);
        int previewEvery = std::max(32, total / 40);
        std::vector<std::thread> pool;

        auto millisecondsNow = []() -> int64_t {
            return std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now().time_since_epoch()).count();
        };

        auto onChunkDone = [&](int cx, int cz, const std::vector<uint16_t>& blockIds, const std::vector<uint16_t>& biomeIds) {
            updatePreviewFromChunkBlocks(cfg, ids, preview, cx, cz, blockIds, biomeIds);
            int dc = doneChunks.fetch_add(1) + 1;

            int lastPreview = lastPreviewChunks.load();
            int64_t nowMs = millisecondsNow();
            int64_t lastMs = lastPreviewMs.load();
            bool previewDue = dc >= total || (dc - lastPreview >= previewEvery && nowMs - lastMs >= 400);

            if (previewDue && lastPreviewChunks.compare_exchange_strong(lastPreview, dc)) {
                lastPreviewMs.store(nowMs);
                flushPreviewFrame(preview);
                reportPreviewReady();
            }

            int last = lastReportedChunks.load();
            bool shouldReport = dc >= total || dc - last >= reportEvery;

            if (!shouldReport) {
                return;
            }

            if (!lastReportedChunks.compare_exchange_strong(last, dc)) {
                return;
            }

            double p = 0.03 + 0.84 * (static_cast<double>(dc) / std::max(1, total));
            reportProgress(p, "Generated " + std::to_string(dc) + "/" + std::to_string(total) + " chunks");
        };

        for (int t = 0; t < workers; ++t) {
            pool.emplace_back([&, t]() {
                (void)t;
                std::vector<uint16_t> blockIds(static_cast<size_t>(WORLD_HEIGHT * 16 * 16));
                std::vector<uint16_t> biomeIds(static_cast<size_t>(16 * 16));

                while (true) {
                    int idx = nextChunk.fetch_add(1);
                    if (idx >= total) {
                        break;
                    }

                    const ChunkJob& job = chunks[static_cast<size_t>(idx)];
                    const Area& area = areas[static_cast<size_t>(job.areaIndex)];
                    if (area.kind == "nether") {
                        generateNetherChunk(cfg, area, blocks, ids, job.cx, job.cz, blockIds, biomeIds);
                    } else if (area.kind == "fuhc") {
                        generateFuhcChunk(cfg, area, blocks, ids, job.cx, job.cz, blockIds, biomeIds,
                                           fuhcSpawnYs[static_cast<size_t>(area.index)]);
                    } else {
                        generateOverworldChunk(cfg, area, blocks, ids, job.cx, job.cz, blockIds, biomeIds);
                    }

                    std::vector<uint8_t> payload;
                    if (cfg.outputFormat != "bedrock") {
                        payload = buildChunkNbt(cfg, blocks, job.cx, job.cz, blockIds, biomeIds);
                    }

                    SharedWorldBucket& bucket = *buckets[static_cast<size_t>(job.bucketIndex)];
                    {
                        std::lock_guard<std::mutex> lock(bucket.mutex);
                        bucket.writer->addChunk(cfg, blocks, job.cx, job.cz, std::move(payload), blockIds, biomeIds);
                    }

                    onChunkDone(job.cx, job.cz, blockIds, biomeIds);
                }
            });
        }

        for (std::thread& th : pool) {
            th.join();
        }

        flushPreviewFrame(preview);
        reportPreviewReady();

        WorldSpawn worldSpawn = cfg.generator == "fuhc"
            ? fuhcWorldSpawn(cfg, areas, fuhcSpawnYs)
            : WorldSpawn{0, spawnY(cfg), 0};

        if (cfg.outputFormat == "bedrock") {
            reportProgress(0.90, "Writing Bedrock 1.18 LevelDB world");
        } else {
            reportProgress(0.90, "Writing " + std::to_string(regions.size()) + " region files");
        }
        writeWorldBuckets(cfg, outDir, regionDir, buckets, worldSpawn);
        reportProgress(0.95, "World files written");

        auto end = std::chrono::steady_clock::now();
        double seconds = std::chrono::duration<double>(end - start).count();
        double roundedSeconds = std::round(seconds * 10.0) / 10.0;
        reportProgress(0.96, "Writing native result metadata");
        if (cfg.generator == "fuhc") {
            writeFuhcPositionsJson(cfg, outDir, areas, fuhcSpawnYs);
            writeFuhcResultJson(cfg, outDir, areas, fuhcSpawnYs, worldSpawn,
                                 doneChunks.load(), static_cast<int>(regions.size()), workers, roundedSeconds);
        } else {
            writeResultJson(cfg, outDir, doneChunks.load(), static_cast<int>(regions.size()), workers, roundedSeconds);
        }
        reportProgress(1.0, "Native generation complete");

        return 0;
    } catch (const std::exception& e) {
        std::cerr << "native generator error: " << e.what() << "\n";
        return 1;
    }
}
