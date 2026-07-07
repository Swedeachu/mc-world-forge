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

## Runtime flow

The website/server behavior stays the same from the UI's point of view:

1. Flask receives the generation request.
2. Python builds the same `WorldConfig` dataclass as before.
3. `server/forge/generator.py` writes a small native config file.
4. Python launches `generator/bin/mc_world_forge_generator.exe` or `generator/bin/mc_world_forge_generator`.
5. The C++ executable generates chunks/regions/preview metadata using worker threads.
6. Python reads the result JSON/preview, writes `level.dat`, zips the output, and serves it back through the same download endpoint.

## Generator status

The vanilla world path is native C++ and multithreaded by region. It includes deterministic terrain height, biomes, rivers/lakes, ores, cave/ravine carving, trees/decorations, walls, a deep-dark underlayer, and the optional nether box.

The old Python vanilla generator is still present as `server.forge.legacy_generator` and can be forced for debugging:

```bat
set MCWF_FORCE_LEGACY=1
scripts\windows\start_web.bat
```

Skywars generation is intentionally left on the Python path because it is optimal enough in Python and is its own separate specialized generator with island/chest/positions JSON logic.

## CLI

```bat
.venv\Scripts\activate.bat
python -m server.cli --name "Valley" --size 1000 --seed 42 --out .\out\Valley
```

The CLI invokes the native generator for normal vanilla worlds.
