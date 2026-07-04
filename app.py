"""
MC World Forge -- web UI.

Run:  python app.py   then open http://127.0.0.1:5000
"""
import io
import os
import re
import base64
import shutil
import tempfile
import threading
import traceback
import zipfile
import uuid

from flask import Flask, request, jsonify, send_file, render_template
from PIL import Image

from generator import WorldConfig, OreCfg, generate_world

app = Flask(__name__)

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


def _build_config(form):
    def num(key, default, cast=float):
        try:
            return cast(form.get(key, default))
        except (TypeError, ValueError):
            return default

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
        seed=int(num("seed", 0, int)),
        data_version=data_version,
        version_name=version,
        floor_y=int(num("floor_y", -20, int)),
        water_level=int(num("water_level", 62, int)),
        base_height=int(num("base_height", 71, int)),
        hill_amp=num("hill_amp", 12.0),
        mountain_amp=num("mountain_amp", 70.0),
        mountain_amount=num("mountain_amount", 0.5),
        snowline_y=int(num("snowline_y", 95, int)),
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
        ores=ores,
    )
    return cfg


def _run_job(job_id, cfg):
    job = JOBS[job_id]
    out_dir = os.path.join(WORKROOT, job_id, _safe_name(cfg.name))
    os.makedirs(out_dir, exist_ok=True)

    def progress(p, msg):
        job["progress"] = round(p * 100, 1)
        job["message"] = msg

    try:
        res = generate_world(cfg, out_dir, progress)
        job["progress"] = 99.0
        job["message"] = "Zipping world..."
        zip_path = os.path.join(WORKROOT, job_id, f"{_safe_name(cfg.name)}.zip")
        base = os.path.join(WORKROOT, job_id)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(out_dir):
                for fn in files:
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, base)
                    zf.write(full, arc)
        job["zip_path"] = zip_path
        job["preview"] = _preview_png_b64(res["preview"])
        job["stats"] = {k: v for k, v in res.items() if k != "preview"}
        job["progress"] = 100.0
        job["message"] = "Done"
        job["done"] = True
    except Exception as e:
        job["error"] = f"{e}"
        job["traceback"] = traceback.format_exc()
        job["done"] = True


@app.route("/")
def index():
    return render_template("index.html", versions=list(VERSIONS.keys()))


@app.route("/generate", methods=["POST"])
def generate():
    form = request.get_json(force=True)
    cfg = _build_config(form)
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"progress": 0.0, "message": "Queued", "done": False,
                        "error": None}
    t = threading.Thread(target=_run_job, args=(job_id, cfg), daemon=True)
    t.start()
    est = cfg.size * cfg.size
    return jsonify({"job_id": job_id, "estimate_blocks": est})


@app.route("/progress/<job_id>")
def progress(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    out = {k: job.get(k) for k in ("progress", "message", "done", "error",
                                   "stats", "preview", "traceback")}
    return jsonify(out)


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("zip_path"):
        return "Not ready", 404
    return send_file(job["zip_path"], as_attachment=True,
                     download_name=os.path.basename(job["zip_path"]))


if __name__ == "__main__":
    print("MC World Forge -> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
