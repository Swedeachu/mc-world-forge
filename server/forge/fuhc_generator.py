"""Native C++ FUHC batch-arena generator."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

from .generator import OreCfg, WorldConfig, _run_native


@dataclass
class FuhcBatchEntry:
    size: int = 50
    count: int = 1
    glass_wall_height: int = 10


@dataclass
class FuhcConfig(WorldConfig):
    generator: str = "fuhc"
    name: str = "FUHC"
    size: int = 50
    floor_y: int = 76
    water_level: int = 75
    base_height: int = 80
    hill_amp: float = 2.0
    mountain_amp: float = 0.0
    mountain_amount: float = 0.0
    extreme_mountain_amount: float = 0.0
    snowline_y: int = 128
    caves: bool = False
    ravines: bool = False
    fluids: bool = False
    rivers: bool = False
    lakes: bool = False
    surface_lava_pools: bool = False
    tree_density: float = 0.45
    wall: bool = False
    deep_dark: bool = False
    nether: bool = False
    structures: bool = False

    batches: List[FuhcBatchEntry] = field(default_factory=lambda: [FuhcBatchEntry(50, 1, 10)])
    fuhc_base_y: int = 80
    fuhc_depth: int = 5
    fuhc_surface_relief: float = 2.0
    fuhc_bedrock_wall_height: int = 5
    fuhc_glass_wall_height: int = 10
    fuhc_glass_roof: bool = True
    fuhc_arena_gap: int = 120
    fuhc_spawn_inset: int = 8
    fuhc_varied_biomes: bool = True


def generate_fuhc_world(cfg: FuhcConfig, out_dir: str,
                         progress: Optional[Callable[[float, str], None]] = None,
                         workers: Optional[int] = None,
                         preview_progress: Optional[Callable[[np.ndarray], None]] = None):
    """Generate boxed FUHC arenas through the shared native C++ executable."""
    t0 = time.time()
    result = _run_native(cfg, out_dir, progress, workers, preview_progress)
    if preview_progress and "preview" in result:
        preview_progress(result["preview"])
    result.setdefault("seconds", round(time.time() - t0, 1))
    return result
