"""
MC World Forge -- web UI.

Run:  python app.py   then open http://127.0.0.1:5000
"""
import io
import os
import re
import json
import base64
import shutil
import tempfile
import threading
import time
import traceback
import zipfile
import uuid

from flask import Flask, request, jsonify, send_file, render_template
from PIL import Image

from .forge.generator import WorldConfig, OreCfg, generate_world
from .forge.skywars_generator import (
    SkywarsConfig, SkyOre, BatchEntry, generate_skywars_world,
    _default_ores as _sky_default_ores,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT_DIR, "web", "templates")
app = Flask(__name__, template_folder=TEMPLATE_DIR)

VERSIONS = {
    "1.20.1": 3465, "1.20.2": 3578, "1.20.4": 3700, "1.20.6": 3839,
    "1.21.1": 3955, "1.21.4": 4189, "1.21.5": 4325, "1.21.7": 4438,
}

JOBS = {}
JOBS_LOCK = threading.Lock()
WORKROOT = os.path.join(tempfile.gettempdir(), "mc_world_forge")
os.makedirs(WORKROOT, exist_ok=True)


def _safe_name(name):
    name = re.sub(r"[^A-Za-z0-9 _\-]", "", name).strip() or "Forged World"
    return name[:48]


def _preview_png_b64(arr, max_side=768):
    img = Image.fromarray(arr, "RGB")
    w, h = img.size
    scale = max(1, max(w, h) // max_side)
    if scale > 1:
        img = img.resize((max(1, w // scale), max(1, h // scale)), Image.NEAREST)
    else:
        # upscale small previews so they are visible
        up = max(1, max_side // max(w, h))
        if up > 1:
            img = img.resize((w * up, h * up), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _set_job_preview(job, arr):
    job["preview"] = _preview_png_b64(arr)
    job["preview_rev"] = int(job.get("preview_rev", 0)) + 1
    job["last_update"] = time.time()


def _build_config(form):
    def num(key, default, cast=float):
        try:
            return cast(form.get(key, default))
        except (TypeError, ValueError):
            return default

    def seed_num():
        try:
            seed = int(form.get("seed", 0))
        except (TypeError, ValueError):
            seed = 0
        return max(-(2 ** 31), min(2 ** 31 - 1, seed))

    version = form.get("version", "1.20.1")
    data_version = VERSIONS.get(version, 3465)

    ores = []
    for o in form.get("ores", []):
        try:
            ores.append(OreCfg(
                name=str(o.get("name", "diamond_ore")).strip() or "diamond_ore",
                y_min=int(o.get("y_min", -19)),
                y_max=int(o.get("y_max", 5)),
                veins_per_chunk=float(o.get("veins_per_chunk", 8)),
                vein_size=int(o.get("vein_size", 5)),
                bias=str(o.get("bias", "uniform")).strip() or "uniform",
            ))
        except (TypeError, ValueError):
            continue
    if not ores:
        ores = WorldConfig().ores

    cfg = WorldConfig(
        name=_safe_name(form.get("name", "Forged World")),
        size=max(16, min(6000, int(num("size", 1000, int)))),
        seed=seed_num(),
        data_version=data_version,
        version_name=version,
        floor_y=int(num("floor_y", -20, int)),
        water_level=int(num("water_level", 118, int)),
        base_height=int(num("base_height", 128, int)),
        hill_amp=num("hill_amp", 8.0),
        mountain_amp=num("mountain_amp", 74.0),
        mountain_amount=num("mountain_amount", 0.30),
        snowline_y=int(num("snowline_y", 246, int)),
        biome_plains_forest_weight=max(0.0, min(100.0, num("biome_plains_forest_weight", 50.0))),
        biome_desert_weight=max(0.0, min(100.0, num("biome_desert_weight", 20.0))),
        biome_snow_weight=max(0.0, min(100.0, num("biome_snow_weight", 15.0))),
        biome_rare_weight=max(0.0, min(100.0, num("biome_rare_weight", 15.0))),
        caves=bool(form.get("caves", True)),
        cave_intensity=num("cave_intensity", 1.0),
        ravines=bool(form.get("ravines", True)),
        fluids=bool(form.get("fluids", True)),
        rivers=bool(form.get("rivers", True)),
        river_amount=num("river_amount", 0.4),
        lakes=bool(form.get("lakes", True)),
        lake_amount=num("lake_amount", 0.4),
        trees=bool(form.get("trees", True)),
        tree_density=num("tree_density", 1.0),
        decorations=bool(form.get("decorations", True)),
        stone_variants=bool(form.get("stone_variants", True)),
        wall=bool(form.get("wall", True)),
        wall_min_y=int(num("wall_min_y", -64, int)),
        wall_max_y=int(num("wall_max_y", 319, int)),
        game_type=int(num("game_type", 0, int)),
        allow_commands=bool(form.get("allow_commands", True)),
        deep_dark=bool(form.get("deep_dark", False)),
        nether=bool(form.get("nether", False)),
        structures=bool(form.get("structures", False)),
        ores=ores,
    )
    workers = int(num("workers", 0, int))
    workers = max(0, min(64, workers))
    return cfg, workers


def _build_skywars_config(form):
    def num(key, default, cast=float):
        try:
            return cast(form.get(key, default))
        except (TypeError, ValueError):
            return default

    def seed_num():
        try:
            seed = int(form.get("seed", 0))
        except (TypeError, ValueError):
            seed = 0
        return max(-(2 ** 31), min(2 ** 31 - 1, seed))

    version = form.get("version", "1.20.1")
    data_version = VERSIONS.get(version, 3465)

    batches = []
    for b in form.get("batches", []):
        try:
            spawn = max(2, min(128, int(b.get("spawn_islands", 12))))
            count = max(0, min(200, int(b.get("count", 1))))
            if count > 0:
                batches.append(BatchEntry(spawn_islands=spawn, count=count))
        except (TypeError, ValueError):
            continue
    if not batches:
        batches = [BatchEntry(12, 1)]

    ores = []
    for o in form.get("ores", []):
        try:
            name = str(o.get("name", "")).strip()
            ratio = float(o.get("ratio", 0))
            if name and ratio > 0:
                ores.append(SkyOre(name=name, ratio=ratio))
        except (TypeError, ValueError):
            continue
    if not ores:
        ores = _sky_default_ores()

    cfg = SkywarsConfig(
        name=_safe_name(form.get("name", "Skywars")),
        seed=seed_num(),
        data_version=data_version,
        version_name=version,
        game_type=int(num("game_type", 0, int)),
        allow_commands=bool(form.get("allow_commands", True)),
        batches=batches,
        main_size=max(8, min(200, int(num("main_size", 52, int)))),
        middle_size=max(6, min(140, int(num("middle_size", 21, int)))),
        spawn_size=max(5, min(120, int(num("spawn_size", 26, int)))),
        island_gap=max(4, min(80, int(num("island_gap", 12, int)))),
        middle_ratio=max(0.2, min(1.0, num("middle_ratio", 1.0))),
        base_y=int(num("base_y", 90, int)),
        height_jitter=max(0, min(40, int(num("height_jitter", 0, int)))),
        surface_relief=max(0.0, min(8.0, num("surface_relief", 1.0))),
        cluster_spacing=max(200, min(6000, int(num("cluster_spacing", 240, int)))),
        varied_biomes=bool(form.get("varied_biomes", True)),
        tree_density=max(0.0, num("tree_density", 1.0)),
        rock_density=max(0.0, num("rock_density", 1.0)),
        foliage_density=max(0.0, num("foliage_density", 1.0)),
        ore_density=max(0.0, num("ore_density", 1.0)),
        surface_ore=max(0.0, num("surface_ore", 1.0)),
        ores=ores,
        chests_per_spawn=max(0, min(120, int(num("chests_per_spawn", 5, int)))),
        chests_per_middle=max(0, min(120, int(num("chests_per_middle", 5, int)))),
        ender_chests_main=max(0, min(200, int(num("ender_chests_main", 10, int)))),
    )
    workers = int(num("workers", 0, int))
    workers = max(0, min(64, workers))
    return cfg, workers


def _zip_job(job_base, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(job_base):
            for fn in files:
                full = os.path.join(root, fn)
                if os.path.abspath(full) == os.path.abspath(zip_path):
                    continue
                arc = os.path.relpath(full, job_base)
                zf.write(full, arc)


def _run_vanilla_job(job, job_id, cfg, workers, progress, preview_progress):
    job_base = os.path.join(WORKROOT, job_id)
    out_dir = os.path.join(job_base, _safe_name(cfg.name))
    os.makedirs(out_dir, exist_ok=True)
    res = generate_world(cfg, out_dir, progress,
                         workers=workers or None,
                         preview_progress=preview_progress)

    job["progress"] = 98.0
    job["message"] = "Writing map JSON..."
    xmin, xmax = res.get("bounds", (0, 0))
    nether_bounds = res.get("nether_bounds")
    layer_starts = res.get("layer_starts", {}) or {}
    structures = [{"type": s["type"], "x": s["x"], "y": s["y"], "z": s["z"]}
                  for s in res.get("structures", [])]
    map_json = {
        "world": _safe_name(cfg.name),
        "seed": cfg.seed,
        "bounds": {"x_min": xmin, "x_max": xmax, "z_min": xmin, "z_max": xmax},
        "spawn": list(res.get("spawn", (0, 64, 0))),
        "layers": {
            "deep_dark": {"enabled": bool(cfg.deep_dark),
                          "start_y": layer_starts.get("deep_dark")},
            "nether": {"enabled": bool(cfg.nether),
                       "bounds": ({"x_min": nether_bounds[0], "x_max": nether_bounds[1],
                                   "z_min": nether_bounds[2], "z_max": nether_bounds[3]}
                                  if nether_bounds else None),
                       "floor_y": -64, "ceiling_y": 127},
        },
        "biomes": res.get("biome_midpoints", []),
        "structures": structures,
    }
    with open(os.path.join(job_base, "world_map.json"), "w", encoding="utf-8") as fp:
        json.dump(map_json, fp, indent=2)

    job["progress"] = 99.0
    job["message"] = "Zipping world..."
    zip_path = os.path.join(WORKROOT, job_id + ".zip")
    _zip_job(job_base, zip_path)
    job["zip_path"] = zip_path
    job["download_name"] = _safe_name(cfg.name) + ".zip"
    _set_job_preview(job, res["preview"])
    job["stats"] = {k: v for k, v in res.items() if k != "preview"}


def _run_skywars_job(job, job_id, cfg, workers, progress, preview_progress):
    job_base = os.path.join(WORKROOT, job_id)
    out_dir = os.path.join(job_base, _safe_name(cfg.name))
    os.makedirs(out_dir, exist_ok=True)
    res = generate_skywars_world(cfg, out_dir, progress,
                                 workers=workers or None,
                                 preview_progress=preview_progress)
    job["progress"] = 98.0
    job["message"] = "Writing positions JSON..."
    positions = res.pop("positions", [])
    pos_dir = os.path.join(job_base, "positions")
    os.makedirs(pos_dir, exist_ok=True)
    for p in positions:
        fn = re.sub(r"[^A-Za-z0-9_\-]", "", p.get("label", "cluster")) + ".json"
        with open(os.path.join(pos_dir, fn), "w", encoding="utf-8") as fp:
            json.dump(p, fp, indent=2)
    with open(os.path.join(pos_dir, "positions.json"), "w", encoding="utf-8") as fp:
        json.dump({"world": _safe_name(cfg.name),
                   "cluster_count": len(positions),
                   "clusters": positions}, fp, indent=2)

    job["progress"] = 99.0
    job["message"] = "Zipping world..."
    zip_path = os.path.join(WORKROOT, job_id + ".zip")
    _zip_job(job_base, zip_path)
    job["zip_path"] = zip_path
    job["download_name"] = _safe_name(cfg.name) + ".zip"
    _set_job_preview(job, res["preview"])
    job["stats"] = {k: v for k, v in res.items() if k != "preview"}


def _run_job(job_id, generator, cfg, workers):
    job = JOBS[job_id]
    last_preview = [0.0]

    def progress(p, msg):
        job["progress"] = round(p * 100, 1)
        job["message"] = msg
        job["last_update"] = time.time()

    def preview_progress(arr):
        now = time.time()
        if now - last_preview[0] < 0.50 and not job.get("done"):
            return
        _set_job_preview(job, arr)
        last_preview[0] = now

    try:
        if generator == "skywars":
            _run_skywars_job(job, job_id, cfg, workers, progress, preview_progress)
        else:
            _run_vanilla_job(job, job_id, cfg, workers, progress, preview_progress)
        job["progress"] = 100.0
        job["message"] = "Done"
        job["done"] = True
    except Exception as e:
        job["error"] = f"{e}"
        job["traceback"] = traceback.format_exc()
        job["done"] = True


@app.route("/")
def index():
    return render_template("index.html", versions=list(VERSIONS.keys()),
                           cpu_count=os.cpu_count() or 1)


@app.route("/generate", methods=["POST"])
def generate():
    form = request.get_json(force=True)
    generator = str(form.get("generator", "vanilla")).strip().lower()
    if generator == "skywars":
        cfg, workers = _build_skywars_config(form)
    else:
        generator = "vanilla"
        cfg, workers = _build_config(form)
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        now = time.time()
        JOBS[job_id] = {"progress": 0.0, "message": "Queued", "done": False,
                        "error": None, "generator": generator,
                        "started_at": now, "last_update": now, "preview_rev": 0}
    t = threading.Thread(target=_run_job, args=(job_id, generator, cfg, workers),
                         daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "generator": generator})


@app.route("/progress/<job_id>")
def progress(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    out = {k: job.get(k) for k in ("progress", "message", "done", "error",
                                   "stats", "traceback")}
    preview_rev = int(job.get("preview_rev", 0))
    client_preview_rev = request.args.get("preview_rev", default=-1, type=int)
    out["preview_rev"] = preview_rev
    if client_preview_rev != preview_rev:
        out["preview"] = job.get("preview")
    else:
        out["preview"] = None
    now = time.time()
    out["elapsed"] = round(now - float(job.get("started_at", now)), 1)
    out["idle"] = round(now - float(job.get("last_update", now)), 1)
    response = jsonify(out)
    response.headers["Cache-Control"] = "no-store, no-cache, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("zip_path"):
        return "Not ready", 404
    return send_file(job["zip_path"], as_attachment=True,
                     download_name=job.get("download_name") or os.path.basename(job["zip_path"]))


if __name__ == "__main__":
    print("MC World Forge -> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
