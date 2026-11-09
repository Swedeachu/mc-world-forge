"""
Headless FUHC arena batch generation.

Takes the same fields the web UI's FUHC panel posts, as a JSON file, and writes
the world plus fuhc_arenas.json / generator_result.json into --out. No Flask
server is started; the request is parsed by the same builder the UI uses.

Example:
  python -m server.fuhc_cli --spec arenas.json --out .\out\Arenas

Spec keys of note (everything else defaults like the UI):
  name, seed, output_format ("java" | "bedrock"), version
  fuhc_batches: [{"size": 50, "count": 40}, ...]
  fuhc_base_y, fuhc_depth, fuhc_bedrock_wall_height, fuhc_glass_wall_height
  fuhc_layout: "rows" with fuhc_chunk_gap, fuhc_row_columns, fuhc_origin_x/z
  fuhc_wall_style: "pattern" with fuhc_wall_pattern: ["light_blue_stained_glass", ...]
  fuhc_roof_block: "glass" | "barrier"
  biome_*_weight, trees, tree_density, decorations, stone_variants, ores
"""
import argparse
import json
import sys

from .app import _build_fuhc_config
from .forge.fuhc_generator import generate_fuhc_world


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a FUHC arena batch without the web UI.")
    parser.add_argument("--spec", required=True, help="JSON file with FUHC form fields")
    parser.add_argument("--out", required=True, help="output world folder")
    parser.add_argument("--workers", type=int, default=None, help="native worker threads (default: auto)")
    args = parser.parse_args()

    with open(args.spec, encoding="utf-8") as handle:
        form = json.load(handle)

    cfg, workers = _build_fuhc_config(form)
    if args.workers is not None:
        workers = args.workers

    def progress(fraction: float, message: str) -> None:
        print(f"[{fraction * 100:5.1f}%] {message}", file=sys.stderr, flush=True)

    result = generate_fuhc_world(cfg, args.out, progress, workers or None)
    print(json.dumps({key: value for key, value in result.items() if key != "preview"}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
