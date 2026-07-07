#include "mcwf/app.hpp"

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
#include "mcwf/config.hpp"
#include "mcwf/constants.hpp"
#include "mcwf/preview.hpp"
#include "mcwf/region.hpp"
#include "mcwf/util.hpp"

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
        int total = static_cast<int>(chunks.size());
        int workers = std::min(parseWorkers(argc, argv, total), std::max(1, total));

        fs::create_directories(outDir);
        fs::path regionDir = outDir / "region";
        fs::create_directories(regionDir);
        fs::create_directories(outDir / "data");

        for (const auto& e : fs::directory_iterator(regionDir)) {
            if (e.path().extension() == ".mca") {
                fs::remove(e.path());
            }
        }

        std::vector<std::unique_ptr<RegionBucket>> buckets;
        buckets.reserve(regions.size());

        for (const RegionJob& r : regions) {
            buckets.push_back(std::make_unique<RegionBucket>(r.rx, r.rz));
        }

        reportProgress(0.01, "Planning native chunk jobs");
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
                    std::vector<uint8_t> payload = generateChunkPayload(
                        cfg, blocks, ids, area, job.cx, job.cz, blockIds, biomeIds);

                    RegionBucket& bucket = *buckets[static_cast<size_t>(job.bucketIndex)];
                    {
                        std::lock_guard<std::mutex> lock(bucket.mutex);
                        bucket.writer.addChunk(job.cx, job.cz, std::move(payload));
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

        reportProgress(0.90, "Writing " + std::to_string(regions.size()) + " region files");
        std::atomic<int> nextRegion{0};
        std::atomic<int> writtenRegions{0};
        std::vector<std::thread> writers;

        int writerCount = std::min(workers, std::max(1, static_cast<int>(buckets.size())));
        for (int t = 0; t < writerCount; ++t) {
            writers.emplace_back([&]() {
                while (true) {
                    int idx = nextRegion.fetch_add(1);
                    if (idx >= static_cast<int>(buckets.size())) {
                        break;
                    }
                    buckets[static_cast<size_t>(idx)]->writer.write(regionDir);
                    int wr = writtenRegions.fetch_add(1) + 1;
                    reportProgress(0.90 + 0.05 * (static_cast<double>(wr) / std::max(1, static_cast<int>(buckets.size()))),
                                   "Wrote " + std::to_string(wr) + "/" + std::to_string(buckets.size()) + " region files");
                }
            });
        }

        for (std::thread& th : writers) {
            th.join();
        }

        auto end = std::chrono::steady_clock::now();
        double seconds = std::chrono::duration<double>(end - start).count();
        reportProgress(0.95, "Writing native result metadata");
        writeResultJson(cfg, outDir, doneChunks.load(), static_cast<int>(regions.size()), workers, std::round(seconds * 10.0) / 10.0);
        reportProgress(1.0, "Native generation complete");

        return 0;
    } catch (const std::exception& e) {
        std::cerr << "native generator error: " << e.what() << "\n";
        return 1;
    }
}
