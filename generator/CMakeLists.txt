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
    src/world/chunk.cpp
    src/world/nbt.cpp
    src/world/palette.cpp
    src/world/preview.cpp
    src/world/region.cpp
    src/world/terrain.cpp
)

target_include_directories(mc_world_forge_generator PRIVATE include)

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
