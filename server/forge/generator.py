"""
Python wrapper for the native C++ vanilla world generator.

The web UI and CLI still build the same WorldConfig dataclass as before, but the
heavy chunk/terrain/region work is delegated to generator/bin/mc_world_forge_generator.exe.
The legacy Python implementation remains available as forge.legacy_generator for
Skywars support and as an emergency fallback during development.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image

from .legacy_generator import (  # re-export public config API
    OreCfg,
    WorldConfig,
    _default_ores,
    overworld_bounds,
    nether_bounds,
)
from . import legacy_generator


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _native_executable() -> Path:
    root = _project_root()
    if os.name == "nt":
        candidates = [
            root / "generator" / "bin" / "mc_world_forge_generator.exe",
            root.parent / "generator" / "bin" / "mc_world_forge_generator.exe",
            root / "generator" / "bin" / "mc_world_forge_generator",
            root.parent / "generator" / "bin" / "mc_world_forge_generator",
        ]
    else:
        candidates = [
            root / "generator" / "bin" / "mc_world_forge_generator",
            root.parent / "generator" / "bin" / "mc_world_forge_generator",
            root / "generator" / "bin" / "mc_world_forge_generator.exe",
            root.parent / "generator" / "bin" / "mc_world_forge_generator.exe",
        ]
    for path in candidates:
        if path.exists() and (os.name == "nt" or os.access(path, os.X_OK)):
            return path
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _bool(v: bool) -> str:
    return "1" if bool(v) else "0"


def _write_native_config(cfg: WorldConfig, path: Path) -> None:
    lines = [
        f"name={cfg.name}",
        f"size={int(cfg.size)}",
        f"seed={int(cfg.seed)}",
        f"data_version={int(cfg.data_version)}",
        f"version_name={cfg.version_name}",
        f"floor_y={int(cfg.floor_y)}",
        f"water_level={int(cfg.water_level)}",
        f"base_height={int(cfg.base_height)}",
        f"hill_amp={float(cfg.hill_amp)}",
        f"mountain_amp={float(cfg.mountain_amp)}",
        f"mountain_amount={float(cfg.mountain_amount)}",
        f"extreme_mountain_amount={float(getattr(cfg, 'extreme_mountain_amount', 0.20))}",
        f"snowline_y={int(cfg.snowline_y)}",
        f"surface_lava_pools={_bool(getattr(cfg, 'surface_lava_pools', True))}",
        f"biome_frozen_cut={float(getattr(cfg, 'biome_frozen_cut', 0.20))}",
        f"biome_cold_cut={float(getattr(cfg, 'biome_cold_cut', 0.36))}",
        f"biome_warm_cut={float(getattr(cfg, 'biome_warm_cut', 0.70))}",
        f"biome_desert_hum_cut={float(getattr(cfg, 'biome_desert_hum_cut', 0.42))}",
        f"biome_forest_hum_cut={float(getattr(cfg, 'biome_forest_hum_cut', 0.62))}",
        f"biome_jungle_hum_cut={float(getattr(cfg, 'biome_jungle_hum_cut', 0.82))}",
        f"biome_plains_forest_weight={float(getattr(cfg, 'biome_plains_forest_weight', 50.0))}",
        f"biome_desert_weight={float(getattr(cfg, 'biome_desert_weight', 20.0))}",
        f"biome_snow_weight={float(getattr(cfg, 'biome_snow_weight', 15.0))}",
        f"biome_rare_weight={float(getattr(cfg, 'biome_rare_weight', 15.0))}",
        f"caves={_bool(cfg.caves)}",
        f"cave_intensity={float(cfg.cave_intensity)}",
        f"ravines={_bool(cfg.ravines)}",
        f"fluids={_bool(cfg.fluids)}",
        f"rivers={_bool(cfg.rivers)}",
        f"river_amount={float(cfg.river_amount)}",
        f"lakes={_bool(cfg.lakes)}",
        f"lake_amount={float(cfg.lake_amount)}",
        f"trees={_bool(cfg.trees)}",
        f"tree_density={float(cfg.tree_density)}",
        f"decorations={_bool(cfg.decorations)}",
        f"stone_variants={_bool(cfg.stone_variants)}",
        f"wall={_bool(cfg.wall)}",
        f"wall_min_y={int(cfg.wall_min_y)}",
        f"wall_max_y={int(cfg.wall_max_y)}",
        f"game_type={int(cfg.game_type)}",
        f"allow_commands={_bool(cfg.allow_commands)}",
        f"deep_dark={_bool(cfg.deep_dark)}",
        f"nether={_bool(cfg.nether)}",
        f"structures={_bool(cfg.structures)}",
    ]
    for ore in cfg.ores:
        name = str(ore.name).replace(",", "_").strip() or "stone"
        bias = str(ore.bias).replace(",", "_").strip() or "uniform"
        lines.append(
            "ore=" + ",".join([
                name,
                str(int(ore.y_min)),
                str(int(ore.y_max)),
                str(float(ore.veins_per_chunk)),
                str(int(ore.vein_size)),
                bias,
            ])
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_ppm(path: Path) -> np.ndarray:
    # Pillow reads the native generator's simple P6 preview without adding a new dependency.
    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _sanitize_progress_message(msg: str) -> str:
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg[:180] if msg else "Generating..."


def _run_native(cfg: WorldConfig, out_dir: str,
                progress: Optional[Callable[[float, str], None]],
                workers: Optional[int],
                preview_progress: Optional[Callable[[np.ndarray], None]] = None) -> dict:
    exe = _native_executable()
    if not exe.exists():
        raise FileNotFoundError(
            f"Native generator not found at {exe}. Run build_generator_msvc.bat first."
        )

    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    cfg_path = out_path / "generator_config.ini"
    _write_native_config(cfg, cfg_path)

    cmd = [str(exe), "--config", str(cfg_path), "--out", str(out_path)]
    if workers is not None:
        cmd += ["--workers", str(int(workers))]

    preview_path = out_path / "preview.ppm"

    def publish_preview() -> None:
        if not preview_progress or not preview_path.exists():
            return
        try:
            preview_progress(_read_ppm(preview_path))
        except Exception:
            # Preview is optional UX. Never fail the actual world build because
            # Windows antivirus/filesystem timing made the first read race.
            pass

    if progress:
        progress(0.0, "Starting native generator process...")

    proc = subprocess.Popen(
        cmd,
        cwd=str(_project_root()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    last_line = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        last_line = line
        if line.startswith("PROGRESS "):
            parts = line.split(" ", 2)
            try:
                p = max(0.0, min(1.0, float(parts[1])))
                msg = _sanitize_progress_message(parts[2] if len(parts) > 2 else "Generating...")
                if progress:
                    progress(p, msg)
            except (TypeError, ValueError):
                pass
        elif line == "PREVIEW_READY":
            publish_preview()

    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"Native generator failed with exit code {code}. Last output: {last_line}")

    result_path = out_path / "generator_result.json"
    if not result_path.exists():
        raise RuntimeError("Native generator finished but did not write generator_result.json")

    with result_path.open("r", encoding="utf-8") as fp:
        result = json.load(fp)

    if preview_path.exists():
        result["preview"] = _read_ppm(preview_path)
        publish_preview()
    else:
        result["preview"] = np.zeros((1, 1, 3), dtype=np.uint8)

    spawn_y = int(result.get("spawn", [0, int(cfg.base_height) + 1, 0])[1])
    if progress:
        progress(0.96, "Writing level.dat...")
    with open(out_path / "level.dat", "wb") as fp:
        fp.write(legacy_generator._build_level_dat(cfg, spawn_y))
    with open(out_path / "session.lock", "wb") as fp:
        fp.write(b"\xe2\x98\x83")

    return result


def generate_world(cfg: WorldConfig, out_dir: str,
                   progress: Optional[Callable[[float, str], None]] = None,
                   workers: Optional[int] = None,
                   preview_progress: Optional[Callable[[np.ndarray], None]] = None):
    """Generate a vanilla world through the native C++ executable.

    Set MCWF_FORCE_LEGACY=1 to temporarily use the old Python generator while
    debugging. The normal path is native C++ and returns the same shape of result
    dictionary the web UI already expects.
    """
    t0 = time.time()
    if os.environ.get("MCWF_FORCE_LEGACY") == "1":
        return legacy_generator.generate_world(
            cfg, out_dir, progress=progress, workers=workers,
            preview_progress=preview_progress,
        )

    result = _run_native(cfg, out_dir, progress, workers, preview_progress)
    if preview_progress and "preview" in result:
        preview_progress(result["preview"])
    result.setdefault("seconds", round(time.time() - t0, 1))
    result.setdefault("biome_midpoints", [])
    result.setdefault("structures", [])
    result.setdefault("layer_starts", {
        "deep_dark": cfg.floor_y if cfg.deep_dark else None,
        "nether": 127 if cfg.nether else None,
    })
    result.setdefault("nether_bounds", nether_bounds(cfg) if cfg.nether else None)
    return result
