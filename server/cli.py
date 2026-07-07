"""
Headless world generator (no web UI).

Example:
  python cli.py --name "Valley" --size 1000 --seed 42 --out ./out
  python cli.py --size 512 --mountain-amount 0.8 --no-trees --out ./out
"""
import argparse
import os
import sys

from .forge.generator import WorldConfig, generate_world, _default_ores

VERSIONS = {
    "1.20.1": 3465, "1.20.2": 3578, "1.20.4": 3700, "1.20.6": 3839,
    "1.21.1": 3955, "1.21.4": 4189, "1.21.5": 4325, "1.21.7": 4438,
}


def main():
    ap = argparse.ArgumentParser(description="Generate a finite Minecraft Java world.")
    ap.add_argument("--out", default="./generated_world",
                    help="output folder (the world save)")
    ap.add_argument("--name", default="Forged World")
    ap.add_argument("--size", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--version", default="1.20.1", choices=list(VERSIONS))

    ap.add_argument("--floor-y", type=int, default=-20)
    ap.add_argument("--water-level", type=int, default=118)
    ap.add_argument("--base-height", type=int, default=128)
    ap.add_argument("--mountain-amount", type=float, default=0.30)
    ap.add_argument("--mountain-amp", type=float, default=74)
    ap.add_argument("--hill-amp", type=float, default=11)
    ap.add_argument("--snowline-y", type=int, default=246)

    ap.add_argument("--no-caves", action="store_true")
    ap.add_argument("--cave-intensity", type=float, default=1.0)
    ap.add_argument("--no-ravines", action="store_true")
    ap.add_argument("--no-fluids", action="store_true")
    ap.add_argument("--no-stone-variants", action="store_true")
    ap.add_argument("--no-rivers", action="store_true")
    ap.add_argument("--river-amount", type=float, default=0.4)
    ap.add_argument("--no-lakes", action="store_true")
    ap.add_argument("--lake-amount", type=float, default=0.4)
    ap.add_argument("--no-trees", action="store_true")
    ap.add_argument("--tree-density", type=float, default=1.0)
    ap.add_argument("--no-decorations", action="store_true")

    ap.add_argument("--no-wall", action="store_true")
    ap.add_argument("--wall-min-y", type=int, default=-64)
    ap.add_argument("--wall-max-y", type=int, default=319)
    ap.add_argument("--creative", action="store_true")
    ap.add_argument("--deep-dark", action="store_true",
                    help="fill the space below the overworld floor with deep dark caverns")
    ap.add_argument("--nether", action="store_true",
                    help="generate a separate 1000x1000 Nether bedrock box to the right")
    ap.add_argument("--structures", action="store_true",
                    help="scatter overworld, deep dark, and Nether-box structures")
    ap.add_argument("--workers", type=int, default=None,
                    help="generation worker processes. Default auto-detects CPU threads; 1 forces serial generation.")

    ap.add_argument("--diamond-veins", type=float, default=None,
                    help="override diamond veins per chunk. Set 0 to omit diamonds.")
    args = ap.parse_args()

    # full vanilla-flavoured default ore set (coal/iron/copper/gold/redstone/lapis/diamond)
    ores = _default_ores()
    if args.diamond_veins is not None:
        for o in ores:
            if o.name == "diamond_ore":
                o.veins_per_chunk = args.diamond_veins
    ores = [o for o in ores if o.veins_per_chunk > 0]

    cfg = WorldConfig(
        name=args.name, size=args.size, seed=args.seed,
        data_version=VERSIONS[args.version], version_name=args.version,
        floor_y=args.floor_y, water_level=args.water_level,
        base_height=args.base_height, mountain_amount=args.mountain_amount,
        mountain_amp=args.mountain_amp, hill_amp=args.hill_amp,
        snowline_y=args.snowline_y,
        caves=not args.no_caves, cave_intensity=args.cave_intensity,
        ravines=not args.no_ravines, fluids=not args.no_fluids,
        stone_variants=not args.no_stone_variants,
        rivers=not args.no_rivers, river_amount=args.river_amount,
        lakes=not args.no_lakes, lake_amount=args.lake_amount,
        trees=not args.no_trees, tree_density=args.tree_density,
        decorations=not args.no_decorations,
        wall=not args.no_wall, wall_min_y=args.wall_min_y,
        wall_max_y=args.wall_max_y,
        game_type=1 if args.creative else 0,
        deep_dark=args.deep_dark, nether=args.nether, structures=args.structures,
        ores=ores,
    )

    def prog(p, msg):
        bar = int(p * 30)
        sys.stdout.write("\r[" + "#" * bar + "." * (30 - bar) + f"] {int(p*100):3d}%  {msg}   ")
        sys.stdout.flush()

    os.makedirs(args.out, exist_ok=True)
    res = generate_world(cfg, args.out, prog, workers=args.workers)
    print()
    print(f"World written to: {args.out}")
    print(f"  chunks={res['chunks']} regions={res['regions']} workers={res['workers']} "
          f"bounds={res['bounds']} spawn={res['spawn']} time={res['seconds']}s")
    print("Copy this folder into your Minecraft 'saves' directory to play.")


if __name__ == "__main__":
    main()
