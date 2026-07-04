# World Forge — finite Minecraft Java worlds

Generates a **finite, bedrock-walled Minecraft Java Edition world** with simple
"1.12-style" rolling terrain, caves, ore veins, snowy mountains, and the common
land biomes — centered on `(0, 0)` and sealed inside a bedrock box.

It writes real region files (`.mca`) directly, so there's nothing to install
inside Minecraft — you just drop the folder into your `saves/`.

![what it makes](preview_example.png)

---

## Quick start (web UI)

```bash
# macOS / Linux
./run.sh
# Windows
run.bat
```

Then open **http://127.0.0.1:5000**, adjust settings, click **Forge world**, and
download the `.zip` when the map appears.

To install the world: unzip it into your Minecraft saves folder, then pick it
from the singleplayer world list.

| OS      | saves folder |
|---------|--------------|
| Windows | `%APPDATA%\.minecraft\saves` |
| macOS   | `~/Library/Application Support/minecraft/saves` |
| Linux   | `~/.minecraft/saves` |

## Quick start (command line)

```bash
pip install -r requirements.txt
python cli.py --name "Valley" --size 1000 --seed 42 --out ./Valley
# then copy ./Valley into your saves folder
python cli.py --help      # all options
```

---

## What you can control

- **Boundaries** — square world, any size (default **1000×1000**), always
  centered on `0,0` and growing outward evenly.
- **Vertical shape** — bedrock **floor Y** (default **−20**, void below it),
  water level, and base land height.
- **Bedrock border wall** — encases the whole border. By default it runs from the
  world bottom (**−64**) to the world top (**319**) with **no ceiling**. Both ends
  are configurable.
- **Ore veins** — a table of ores, each with its own Y range, veins-per-chunk
  rate, and vein size. Default: **very common diamond** right around **Y 5 and
  below**, plus coal and iron.
- **Terrain & mountains** — hill height, mountain amount/height, and the snow line
  (snowy caps get a top snow layer).
- **Biomes** — plains, forest, taiga, jungle, snowy plains, snowy taiga, snowy
  mountain slopes, and river. **No oceans**; rivers and small lakes only.
- **Caves**, **rivers**, **lakes**, **trees** — toggle and tune each.
- **Target version** — 1.20.1 → 1.21.7 (worlds also open in newer versions).

Every setting is reproducible: the same **seed + settings** always rebuilds the
exact same world.

---

## How it works (and why it's built this way)

Minecraft's own world generation (via world presets or data packs) is
*infinite* by design — there's no built-in way to make a finite world ringed in
real bedrock, and a `/fill` bedrock box would be millions of commands. So World
Forge generates the terrain itself and writes it straight into the Anvil region
format:

- `noise.py` — seeded, vectorized value noise (no external noise lib).
- `generator.py` — terrain height, biome/climate assignment, cave carving, ore
  veins, trees, snow, the bedrock floor, and the border wall.
- `region.py` — assembles 1.18+ chunk NBT (palette-based `block_states` /
  `biomes`, negative-Y sections) and packs region files.
- `nbt.py` — a small NBT binary writer.
- `app.py` / `templates/index.html` — the web UI.
- `cli.py` — headless generation.
- `verify.py` — self-check that re-parses the output (`python verify.py <world>`).

The world's stored generator is set to **flat void**, so anything beyond the
bedrock walls is empty space rather than stray vanilla terrain. You're sealed
inside the box regardless.

Terrain uses the plain `stone` / `diamond_ore` palette everywhere (even below
`y=0`) for that older, simpler look, instead of deepslate.

---

## Notes & limitations

- A **1000×1000** world takes roughly **1–2 minutes** to build and produces about
  **15–20 MB** of region files.
- Lighting and heightmaps are recomputed by Minecraft on first load, so the very
  first time you open the world near the edges it may relight briefly.
- Trees are clipped at chunk borders in rare cases (cosmetic).
- The Nether and End are left to vanilla defaults (only the Overworld is forged).
- Requires Python 3.9+.
