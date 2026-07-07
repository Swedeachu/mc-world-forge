cmake_minimum_required(VERSION 3.20)
project(mc_world_forge_generator LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(mc_world_forge_generator
    src/main.cpp
    src/app/generator_app.cpp
    src/core/config.cpp
    src/core/noise.cpp
    src/core/util.cpp
    src/world/areas.cpp
    src/world/nbt.cpp
    src/world/palette.cpp
    src/world/preview.cpp
    src/world/region.cpp
    # Terrain sampling (public entry points) + concern-split internals.
    src/world/terrain_sample.cpp
    src/world/internal/terrain_grid.cpp
    src/world/internal/terrain_height.cpp
    src/world/internal/terrain_climate.cpp
    src/world/internal/terrain_biome.cpp
    src/world/internal/terrain_water.cpp
    # Chunk assembly (public entry points) + per-pass sources.
    src/world/chunk_common.cpp
    src/world/chunk_ores.cpp
    src/world/chunk_fluids.cpp
    src/world/chunk_caves.cpp
    src/world/chunk_ravines.cpp
    src/world/chunk_decorate.cpp
    src/world/chunk_generate.cpp
)

# "include" holds the public mcwf/*.hpp API; "src/world" lets translation units
# reach the private "internal/*.hpp" module headers.
target_include_directories(mc_world_forge_generator PRIVATE include src/world)

if(MSVC)
    target_compile_options(mc_world_forge_generator PRIVATE /O2 /MP /EHsc /permissive- /W4)
else()
    target_compile_options(mc_world_forge_generator PRIVATE -O3 -Wall -Wextra)
endif()

set_target_properties(mc_world_forge_generator PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "${CMAKE_CURRENT_SOURCE_DIR}/bin"
)
