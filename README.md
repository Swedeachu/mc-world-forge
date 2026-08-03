# MC World Forge

MC World Forge is split into clean layers:

```text
mc-world-forge/
  web/
    templates/                  HTML UI
  server/
    app.py                      Flask server + job API
    cli.py                      Headless CLI entry point
    requirements.txt            Python dependencies
    forge/
      generator.py              Python wrapper around the native generator
      fuhc_generator.py         Native FUHC arena configuration/wrapper
      legacy_generator.py       Python fallback/reference generator
      skywars_generator.py      Skywars generator, still Python-backed
      nbt.py / region.py        Shared Python NBT helpers used by Skywars + level.dat
  generator/
    CMakeLists.txt              Native C++ build project
    include/mcwf/               Native headers
    src/main.cpp                Tiny entry point only
    src/app/                    CLI process orchestration
    src/core/                   Config, utility, and noise code
    src/world/                  Terrain, chunk, NBT, region, preview, and palette code
    scripts/windows/            MSVC build/clean scripts
    bin/                        Native executable output folder
  scripts/windows/              Project setup/start/build convenience scripts
```

## Windows setup

Run this from the project root:

```bat
scripts\windows\setup.bat
```

That will create `.venv`, install the Python dependencies from `server\requirements.txt`, generate the Visual Studio solution under `generator\build\msvc`, and compile the native Release executable to:

```text
generator\bin\mc_world_forge_generator.exe
```

Then start the website:

```bat
scripts\windows\start_web.bat
```

Open:

```text
http://127.0.0.1:5000
```

## Native generator build only

```bat
scripts\windows\build_generator_msvc.bat
```

or directly:

```bat
generator\scripts\windows\build_msvc.bat
```

The build script uses CMake with the Visual Studio 2022 generator. It tries to locate MSVC through `vswhere` when it is not already running inside a Visual Studio Developer Command Prompt.

Generated CMake caches and prebuilt executables are not required in the source archive. Running either setup/build script recreates the current native binary from the included C++ sources.

## Runtime flow

The website/server behavior stays the same from the UI's point of view:

1. Flask receives the generation request.
2. Python builds the mode-specific `WorldConfig`, `FuhcConfig`, or `SkywarsConfig` dataclass.
3. `server/forge/generator.py` writes a small native config file.
4. Python launches `generator/bin/mc_world_forge_generator.exe` or `generator/bin/mc_world_forge_generator`.
5. The C++ executable generates chunks/preview metadata using worker threads and writes either Java Anvil regions or Bedrock 1.18 LevelDB chunk data.
6. Python reads the result JSON/preview, writes the Java `level.dat` only for Java output, zips the output, and serves it back through the same download endpoint.

## Generator status

The vanilla and FUHC world paths are native C++ and multithreaded by chunk. Vanilla includes deterministic terrain height, biomes, rivers/lakes, ores, cave/ravine carving, trees/decorations, walls, a deep-dark underlayer, and the optional Nether box. The Nether path uses the native C++ generator too: it builds a vanilla-height 0..127 enclosed Nether cube with rough bedrock floor/roof layers, lava sea, cave-density terrain, Nether ores, and the 1.16+ biome set (`nether_wastes`, `crimson_forest`, `warped_forest`, `soul_sand_valley`, `basalt_deltas`). The web UI defaults to Java output, with a top-bar toggle for direct Bedrock 1.18 output intended for PocketMine/PMMP-style LevelDB worlds.

The deep-dark ore/palette bug fix from the Python generator is mirrored into C++: ore variants are registered up-front, namespaced ore names are converted to valid `minecraft:deepslate_*_ore` IDs, and the deep-dark layer gets its own ore pass after deep-dark material selection so ore IDs cannot outrun the block palette.

The old Python vanilla generator is still present as `server.forge.legacy_generator` and can be forced for debugging:

```bat
set MCWF_FORCE_LEGACY=1
scripts\windows\start_web.bat
```

Skywars generation is intentionally left on the Python path because it is a separate specialized generator with island/chest/positions JSON logic.

Skywars islands keep their established PvP silhouettes and ring spacing, but their art pass is island-local rather than a copy of vanilla biome topsoil. Each biome style coordinates broad low-frequency surface regions with weighted close-colour micro-palettes of natural blocks, concrete, wool, and terracotta. Podzol-led palettes are kept separate from grass-led palettes, brown regions balance regular and coarse dirt, snowy ground uses blue wool while its spike landmarks retain real ice, and the intentionally muted material allow-list excludes high-intensity color combinations that read as artificial. Eroded rims, variable three-to-seven-block soil caps, stone fingers, and restrained geological pockets carry the same material story through the underside. Repeated biomes rotate accents while keeping a dominant paint, so they remain readable without block-by-block confetti. The final vectorized buried-ore pass runs after terrain painting so the full resource budget survives. Exposed ores use deterministic per-island quotas with ratio-aware extras and stratified placement: every spawn island receives at least eight of each configured ore type and every middle island at least twelve, independent of chunk seams or random luck.

Natural landmarks use compact procedural silhouette families: rounded, forked, layered, arched, wind-bent, umbrella, fir, palm, flat-canopy, and jungle trees; volumetric grounded boulders, split outcrops, and clustered crags; plus fallen logs, shrubs, cactus monuments, ice-spike clusters, and low weathered terracotta mounds. A canopy connectivity pass bridges one-block leaf gaps and removes any foliage still disconnected from a log. Their materials follow the island style. Placement uses the original terrain height map, requires perfectly level rock and landmark footprints, spaces structures apart, protects randomly oriented chests, and preserves a two-block-high clear combat disc around island centres and player spawns.

When Bedrock is selected, Skywars is written directly as a PocketMine-compatible LevelDB world (storage version 10, network version 503 / Bedrock 1.18.30), including chunk palettes, 3D biomes, and chest block entities. The exporter targets the block-state support of the bundled PocketMine server rather than merely renaming a Java Anvil world.

## FUHC arena batches

The FUHC mode creates batches of compact vanilla PvP arenas through the same native worker pool, chunk writer, preview path, and Java/Bedrock exporters as the vanilla generator. Each batch row defines an arena footprint and count, so one run can mix sizes such as `50 x 50`, `64 x 64`, and `100 x 100`. The UI seed is the root seed: FUHC hashes it with each arena ordinal to produce a separate deterministic terrain seed for every arena, preventing terrain, tree, decoration, and ore noise from continuing across the grid as one large world.

FUHC biome weights are resolved once for the entire generated arena set instead of being treated as loose climate biases inside every small box. The native planner converts the four UI weights into exact whole-arena quotas, guarantees every enabled group appears when the arena count is large enough, and uses a seeded smooth weighted schedule to spread those assignments across the grid rather than clumping them. Green-biome assignments cycle through plains, forest, birch forest, taiga, and savanna; rare assignments cycle through jungle, swamp, and badlands. Desert and snow assignments remain visually distinct single-biome arenas.

A `50 x 50` arena remains one coherent biome. Arenas larger than 50 blocks switch to an exact-quota climate blend. The generator first makes one broad, curved cold continent and one broad, curved warm continent. It then divides the cold side into snow and temperate terrain with a perpendicular low-frequency climate lobe, and divides the warm side into desert and rare terrain with a separate lobe. This keeps temperature-compatible biomes beside each other without forcing the middle groups into thin transition stripes or cutting the arena into a Voronoi quilt.

The plains/forest/taiga family and the rare family are divided again with independent broad selectors rather than strict temperature thresholds. Plains remains the largest temperate variant, forest and taiga receive substantial regions, birch and savanna remain secondary regions, and jungle, swamp, and badlands split the rare quota evenly. The four UI group weights still produce exact whole-column quotas, groups with zero weight remain excluded, and the root seed plus per-arena derived seed reproduce both the proportions and natural boundaries.

Defaults:

- `50 x 50` outer footprint per arena.
- Five-block terrain foundation centered around surface Y `80`.
- Seeded one-block terrain waves and terraces instead of a repeated center bump or perfectly flat floor.
- Each arena independently selects rolling terraces, long ridges, crossed swells, tilted terraces, or meandering ridges from its derived arena seed.
- The default `50 x 50` layout uses low-frequency, domain-warped shapes so elevation boundaries remain broad and PvP-friendly rather than forming narrow staircases.
- FUHC disables river, lake, and surface-lava shaping entirely, so no dry water/lava ditches are carved into arena floors.
- Configurable biome weights, trees, decorations, stone variants, and ores.
- Five visible bedrock wall blocks beginning at the configured surface Y.
- Ten glass wall blocks above the bedrock.
- One glass roof block above the glass wall run.
- One hundred twenty blocks of empty edge-to-edge spacing between arena footprints.
- A complete two-block-thick `minecraft:barrier` shell outside the visible bedrock/glass enclosure, including the underside and roof, so the arena cannot be escaped by clipping through any face.
- Two spawn points inset evenly from opposite sides of every arena.
- Void world metadata outside the explicitly generated arena chunks.
- FUHC tree density is one-third of the selected UI density; tree roots stay clear of walls and roof limits.
- Surface decoration uses short grass only; two-block-tall grass is disabled.

The shell dimensions, roof toggle, foundation depth, base Y, surface variation, gap, spawn inset, batches, terrain details, and ore table are all configurable in the FUHC UI panel. FUHC surface variation is deliberately limited to one-block rises: `0` keeps arenas completely flat, while positive values control how much of each arena participates in broad seeded waves and terraces. Terrain never drops below the configured base Y, and each arena's independent derived seed controls its orientation, wavelength, domain warp, profile, and phase. Boundary chunks are explicitly cleared outside each exact arena rectangle except for the two required invisible barrier layers, and the live preview treats barrier blocks as invisible background, so the visible and exported footprint is exactly the requested box size rather than the surrounding chunk bounds.

Every generated FUHC world contains:

```text
fuhc_arenas.json
```

Its top-level keys are `fuhc1`, `fuhc2`, and so on. Each arena contains `spawnPoint1` and `spawnPoint2` objects with `x`, `y`, and `z`. The X/Z positions are symmetric about the arena midpoint. Each Y is measured from the actual generated floor column, and only the two player-body blocks in that exact one-block column are cleared. Trees, surface decoration, and snow layers are prevented at the spawn column, while the glass roof and barrier shell are never modified.

`generator_result.json` also records the primary `arena_biomes` list, final whole-arena `biome_distribution` quota counts, per-arena biome mode, the cold-to-hot biome order, and exact interior-column counts for every large arena. This makes it clear which arenas remain single-biome and verifies that large-arena blends preserve the requested proportions.

## CLI

```bat
.venv\Scripts\activate.bat
python -m server.cli --name "Valley" --size 1000 --seed 42 --out .\out\Valley
```

The CLI invokes the native generator for normal vanilla worlds.

## Wilderness metadata JSON

Every native vanilla world now writes an additional teleport metadata file beside `level.dat` named after the world, for example:

```text
Forged World.json
```

This file is intended for server commands such as `/wilderness`. It contains:

- `world_midpoints.overworld` and `world_midpoints.nether` center positions.
- `biome_sections`, a flat list of detected contiguous biome sections.
- `biomes`, the same section data grouped by keys like `overworld:plains`, `overworld:desert`, `overworld:forest`, and `overworld:snowy_hills`.
- `surface_y` plus a recommended teleport `y` value above ground.
- section bounds, approximate radius, and approximate area so a server can filter out tiny sections before applying a random X/Z offset.

For final safety, server-side teleport code should still re-check the highest safe block after applying its random X/Z offset.
