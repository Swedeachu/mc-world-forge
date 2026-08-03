import unittest
from unittest.mock import patch

import numpy as np

from . import bedrock_writer
from .bedrock_writer import (
    _canonical_block_state,
    _crc32c,
    _heightmap_and_biomes,
    _write_biome_palette,
    build_chunk_entries,
)
from .skywars_generator import (
    AIR,
    BAND_LO,
    BIOMES,
    SURFACE_TREES,
    ChunkStore,
    Island,
    Rng,
    SkyBlocks,
    SkywarsConfig,
    _block_at,
    _ice_spikes,
    _ore_probs,
    _rock,
    _rock_materials,
    _set,
    _surface_ore_quotas,
    _surface_spec,
    _terracotta_mound,
    _tree,
    decorate_island,
    place_island_chests,
    plan_cluster,
    stamp_island,
)


class SkywarsArtDirectionTests(unittest.TestCase):
    def test_every_tree_form_has_one_face_connected_canopy(self):
        directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                      (0, -1, 0), (0, 0, 1), (0, 0, -1))
        forms = sorted({tuple(form) for choices in SURFACE_TREES.values()
                        for form in choices})
        for seed, form in enumerate(forms):
            with self.subTest(form=form):
                blocks = SkyBlocks()
                store = ChunkStore(0)
                for z in range(-10, 11):
                    for x in range(-10, 11):
                        _set(store, blocks, x, 90, z, blocks.grass)
                _tree(store, blocks, 0, 0, 91, form, Rng(8181, seed))
                kind = form[0]
                leaf = blocks.leaf(kind)
                logs = {blocks.log(kind, axis) for axis in ("x", "y", "z")}
                leaves = set()
                roots = set()
                for y in range(91, 112):
                    for z in range(-9, 10):
                        for x in range(-9, 10):
                            bid = _block_at(store, x, y, z)
                            if bid == leaf:
                                leaves.add((x, y, z))
                            elif bid in logs:
                                roots.add((x, y, z))
                self.assertTrue(leaves)
                seen = set(roots)
                stack = list(roots)
                walkable = leaves | roots
                while stack:
                    x, y, z = stack.pop()
                    for dx, dy, dz in directions:
                        other = (x + dx, y + dy, z + dz)
                        if other in walkable and other not in seen:
                            seen.add(other)
                            stack.append(other)
                self.assertTrue(leaves.issubset(seen))

    def test_rejected_high_intensity_blocks_are_not_registered_or_emitted(self):
        blocks = SkyBlocks()
        rejected = {
            "minecraft:yellow_terracotta", "minecraft:yellow_wool",
            "minecraft:yellow_concrete", "minecraft:lime_concrete",
            "minecraft:lime_wool", "minecraft:light_gray_terracotta",
            "minecraft:white_terracotta", "minecraft:orange_concrete",
            "minecraft:light_blue_concrete",
        }
        self.assertTrue(rejected.isdisjoint(name for name, _ in blocks.palette()))

    def test_podzol_led_palettes_never_mix_in_grass_blocks(self):
        blocks = SkyBlocks()
        for _, _, style in BIOMES:
            spec = _surface_spec(blocks, style)
            surface = set(spec["rim_palette"])
            for palette in spec["surface_palettes"]:
                surface.update(palette)
            if blocks.podzol in surface:
                with self.subTest(style=style):
                    self.assertNotIn(blocks.grass, surface)
                    self.assertNotIn(blocks.grass_snow, surface)

    def test_savanna_surface_does_not_mix_in_badlands_colors(self):
        blocks = SkyBlocks()
        spec = _surface_spec(blocks, "savanna")
        surface = set(spec["rim_palette"])
        for palette in spec["surface_palettes"] + spec["sub_palettes"]:
            surface.update(palette)
        badlands_colors = {
            blocks.red_sand, blocks.red_sandstone, blocks.cut_red_sandstone,
            blocks.orange_terracotta, blocks.red_terracotta,
            blocks.red_concrete, blocks.red_wool, blocks.orange_wool,
        }
        self.assertTrue(badlands_colors.isdisjoint(surface))

    def test_snow_surface_uses_blue_wool_but_ice_spikes_keep_real_ice(self):
        blocks = SkyBlocks()
        for style in ("snowy", "snowy_grove"):
            with self.subTest(style=style):
                spec = _surface_spec(blocks, style)
                surface = set(spec["rim_palette"])
                for palette in spec["surface_palettes"]:
                    surface.update(palette)
                self.assertTrue({blocks.light_blue_wool, blocks.blue_wool} & surface)
                self.assertNotIn(blocks.packed_ice, surface)
                self.assertNotIn(blocks.blue_ice, surface)

        store = ChunkStore(0)
        surface_map = {}
        for z in range(-8, 9):
            for x in range(-8, 9):
                _set(store, blocks, x, 90, z, blocks.snow_block)
                surface_map[(x, z)] = 90
        _ice_spikes(store, blocks, 0, 0, surface_map, Rng(5512))
        placed = {
            _block_at(store, x, y, z)
            for y in range(91, 101)
            for z in range(-6, 7)
            for x in range(-6, 7)
        }
        self.assertIn(blocks.packed_ice, placed)
        self.assertIn(blocks.blue_ice, placed)

    def test_brown_region_palettes_balance_regular_and_coarse_dirt(self):
        blocks = SkyBlocks()
        for style in ("meadow", "woodland", "birch_grove", "taiga", "jungle", "savanna"):
            with self.subTest(style=style):
                brown_region = _surface_spec(blocks, style)["surface_palettes"][1]
                regular = brown_region.count(blocks.dirt)
                coarse = brown_region.count(blocks.coarse_dirt)
                self.assertGreaterEqual(regular, 2)
                self.assertGreaterEqual(coarse, 2)
                self.assertLessEqual(abs(regular - coarse), 1)

    def test_bedrock_chest_facing_conversion_covers_all_cardinal_directions(self):
        expected = {"north": 2, "south": 3, "west": 4, "east": 5}
        for facing, direction in expected.items():
            with self.subTest(facing=facing):
                self.assertEqual(
                    ("minecraft:chest", {"facing_direction": direction}),
                    _canonical_block_state(("minecraft:chest", {"facing": facing})),
                )

    def test_terrain_palettes_exclude_calcite_and_mossy_cobblestone(self):
        blocks = SkyBlocks()
        forbidden = {blocks.calcite, blocks.mossy_cobble}
        for _, _, style in BIOMES:
            with self.subTest(style=style):
                spec = _surface_spec(blocks, style)
                floor = set(spec["rim_palette"]) | set(spec["geology"])
                for palette in spec["surface_palettes"] + spec["sub_palettes"]:
                    floor.update(palette)
                self.assertTrue(forbidden.isdisjoint(floor))

    def test_surface_regions_have_weighted_artist_micro_palettes(self):
        blocks = SkyBlocks()
        for _, _, style in BIOMES:
            with self.subTest(style=style):
                spec = _surface_spec(blocks, style)
                for palette in spec["surface_palettes"]:
                    self.assertGreaterEqual(len(set(palette)), 4)
                    self.assertNotEqual(blocks.dirt, palette[0])

    def test_default_ore_budget_is_three_times_the_old_budget(self):
        blocks = SkyBlocks()
        _, probabilities = _ore_probs(blocks, SkywarsConfig())
        self.assertAlmostEqual(0.27, sum(probabilities), places=7)

    def test_leveldb_crc32c_matches_standard_vector_and_incremental_use(self):
        self.assertEqual(0xE3069283, _crc32c(b"123456789"))
        first = _crc32c(b"1234")
        self.assertEqual(0xE3069283, _crc32c(b"56789", first))

    def test_column_biome_palette_is_encoded_once_then_reused_vertically(self):
        world = np.zeros((384, 16, 16), dtype=np.int16)
        biomes = np.zeros((16, 16), dtype=np.int16)
        with patch.object(
            bedrock_writer, "_write_biome_palette", wraps=_write_biome_palette
        ) as encoder:
            payload = _heightmap_and_biomes(
                world, biomes, ["minecraft:the_void"]
            )
        self.assertGreater(len(payload), 512)
        self.assertEqual(1, encoder.call_count)

    def test_bedrock_palette_uses_pocketmine_supported_moss_fallback(self):
        self.assertEqual(
            ("minecraft:grass", {}),
            _canonical_block_state(("minecraft:moss_block", {})),
        )

    def test_bedrock_chunk_has_one_authoritative_version_key(self):
        world = np.zeros((384, 16, 16), dtype=np.int16)
        biomes = np.zeros((16, 16), dtype=np.int16)
        entries = build_chunk_entries(
            0, 0, world, biomes,
            [("minecraft:air", {})],
            ["minecraft:the_void"],
        )
        key_types = [key[8] for key, _ in entries if len(key) == 9]
        self.assertEqual(1, key_types.count(0x2C))
        self.assertNotIn(0x76, key_types)

    def test_biome_art_decks_are_balanced_and_do_not_repeat_at_ring_seams(self):
        cfg = SkywarsConfig(seed=44219)
        islands, _ = plan_cluster(cfg, 24, 0, 0, cfg.seed)
        for kind in ("middle", "spawn"):
            ring = [island.biome_idx for island in islands if island.kind == kind]
            self.assertNotEqual(ring[0], ring[-1])
            self.assertTrue(all(a != b for a, b in zip(ring, ring[1:])))
            if len(ring) >= len(BIOMES):
                self.assertEqual(set(range(len(BIOMES))), set(ring[:len(BIOMES)]))

    def test_every_biome_paints_surface_and_geology_in_broad_variation(self):
        cfg = SkywarsConfig(seed=918273, ores=[], surface_ore=0.0)
        for biome_idx, (_, _, style) in enumerate(BIOMES):
            with self.subTest(style=style, biome_idx=biome_idx):
                blocks = SkyBlocks(cfg.data_version)
                store = ChunkStore(0)
                island = Island(0, 0, 18.0, 90, 15, biome_idx, "middle", 1)
                columns = stamp_island(store, blocks, cfg, island,
                                       Rng(cfg.seed, biome_idx, 5))

                top_ids = {_block_at(store, x, y, z) for x, z, y in columns}
                spec = _surface_spec(blocks, style,
                                     variant=(biome_idx + cfg.seed) % 3)
                expected_top = set(spec["rim_palette"])
                for palette in spec["surface_palettes"]:
                    expected_top.update(palette)
                self.assertGreaterEqual(len(top_ids & expected_top), 5)

                used = set()
                for arr in store.blocks.values():
                    used.update(int(v) for v in np.unique(arr))
                self.assertGreaterEqual(len(used & set(spec["geology"])), 2)

    def test_soil_to_stone_boundary_is_not_a_flat_cutoff(self):
        cfg = SkywarsConfig(seed=113377, ores=[], surface_ore=0.0)
        blocks = SkyBlocks(cfg.data_version)
        store = ChunkStore(0)
        island = Island(0, 0, 24.0, 90, 18, 0, "main", 1)
        columns = stamp_island(store, blocks, cfg, island, Rng(cfg.seed, 8))
        geology = set(_surface_spec(blocks, "meadow")["geology"])
        transition_depths = set()
        for x, z, surface_y in columns:
            for depth in range(1, 9):
                if _block_at(store, x, surface_y - depth, z) in geology:
                    transition_depths.add(depth)
                    break
        self.assertGreaterEqual(len(transition_depths), 3)

    def test_all_rock_shapes_are_grounded_and_never_one_block_wide(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                blocks = SkyBlocks()
                store = ChunkStore(0)
                surface_map = {}
                for z in range(-8, 9):
                    for x in range(-8, 9):
                        ground_y = 90
                        _set(store, blocks, x, ground_y, z, blocks.grass)
                        surface_map[(x, z)] = ground_y
                _rock(store, blocks, 0, 0, "meadow", Rng(seed, 91), surface_map)
                rock_ids = set(_rock_materials(blocks, "meadow"))
                layers = {}
                columns = {}
                for y in range(91, 100):
                    for z in range(-8, 9):
                        for x in range(-8, 9):
                            if _block_at(store, x, y, z) in rock_ids:
                                layers.setdefault(y, set()).add((x, z))
                                columns.setdefault((x, z), []).append(y)
                self.assertTrue(layers)
                for coords in layers.values():
                    xs = [x for x, _ in coords]
                    zs = [z for _, z in coords]
                    self.assertGreaterEqual(max(xs) - min(xs) + 1, 2)
                    self.assertGreaterEqual(max(zs) - min(zs) + 1, 2)
                    self.assertGreaterEqual(len(coords), 4)
                base = layers[min(layers)]
                self.assertGreaterEqual(max(x for x, _ in base) - min(x for x, _ in base) + 1, 3)
                self.assertGreaterEqual(max(z for _, z in base) - min(z for _, z in base) + 1, 3)
                for (x, z), ys in columns.items():
                    self.assertNotEqual(AIR, _block_at(store, x, min(ys) - 1, z))

    def test_terracotta_formation_is_a_low_grounded_mound_not_a_pipe(self):
        blocks = SkyBlocks()
        store = ChunkStore(0)
        surface_map = {}
        for z in range(-8, 9):
            for x in range(-8, 9):
                _set(store, blocks, x, 90, z, blocks.red_sand)
                surface_map[(x, z)] = 90
        _terracotta_mound(store, blocks, 0, 0, surface_map, Rng(7721))
        formation = {blocks.terracotta, blocks.orange_terracotta,
                     blocks.brown_terracotta, blocks.red_terracotta,
                     blocks.brown_concrete}
        occupied = []
        for y in range(91, 98):
            for z in range(-8, 9):
                for x in range(-8, 9):
                    if _block_at(store, x, y, z) in formation:
                        occupied.append((x, y, z))
        self.assertTrue(occupied)
        self.assertLessEqual(max(y for _, y, _ in occupied) - 90, 3)
        self.assertGreaterEqual(max(x for x, _, _ in occupied) - min(x for x, _, _ in occupied), 5)
        self.assertGreaterEqual(max(z for _, _, z in occupied) - min(z for _, _, z in occupied), 5)
        for x, y, z in occupied:
            if y == min(py for px, py, pz in occupied if px == x and pz == z):
                self.assertNotEqual(AIR, _block_at(store, x, y - 1, z))

    def test_chest_placement_uses_multiple_deterministic_facings(self):
        cfg = SkywarsConfig(seed=4488, ores=[], chests_per_middle=24)
        blocks = SkyBlocks()
        store = ChunkStore(0)
        island = Island(0, 0, 24.0, 90, 18, 0, "middle", 1)
        island._biome_pid = 0
        stamp_island(store, blocks, cfg, island, Rng(cfg.seed, 4))
        positions = place_island_chests(store, blocks, cfg, island, Rng(cfg.seed, 77))
        palette = blocks.palette()
        facings = {
            palette[_block_at(store, x, y, z)][1]["facing"]
            for x, y, z in positions
        }
        self.assertGreaterEqual(len(facings), 3)

    def test_final_ore_pass_preserves_the_configured_buried_ratio(self):
        cfg = SkywarsConfig(seed=99017, surface_ore=0.0)
        blocks = SkyBlocks()
        store = ChunkStore(0)
        island = Island(0, 0, 26.0, 90, 20, 0, "main", 1)
        island._biome_pid = 0
        columns = stamp_island(store, blocks, cfg, island, Rng(cfg.seed, 12))
        ore_ids = {blocks.ore(ore.name) for ore in cfg.ores}
        eligible = ores = 0
        for x, z, surface_y in columns:
            for y in range(BAND_LO, surface_y):
                bid = _block_at(store, x, y, z)
                if bid != AIR:
                    eligible += 1
                    ores += bid in ore_ids
        self.assertGreater(eligible, 1000)
        self.assertAlmostEqual(0.27, ores / eligible, delta=0.025)

    def test_surface_ore_coverage_is_thirty_percent_higher(self):
        cfg = SkywarsConfig(seed=337799, ore_density=1.0, surface_ore=1.0)
        blocks = SkyBlocks()
        store = ChunkStore(0)
        island = Island(0, 0, 30.0, 90, 22, 0, "main", 1)
        island._biome_pid = 0
        columns = stamp_island(store, blocks, cfg, island, Rng(cfg.seed, 42))
        ore_ids = {blocks.ore(ore.name) for ore in cfg.ores}
        exposed = sum(
            _block_at(store, x, surface_y, z) in ore_ids
            for x, z, surface_y in columns
        )
        # 0.27 buried budget * 0.26 exposure factor = 7.02% target.
        self.assertAlmostEqual(0.0702, exposed / len(columns), delta=0.015)

    def test_each_spawn_and_middle_island_gets_exact_fair_surface_ore_quotas(self):
        cfg = SkywarsConfig(seed=713399, ore_density=1.0, surface_ore=1.0)
        for kind, radius, minimum in (("spawn", 9.0, 8), ("middle", 21.0, 12)):
            for seed in (41, 97):
                with self.subTest(kind=kind, seed=seed):
                    blocks = SkyBlocks()
                    store = ChunkStore(0)
                    island = Island(0, 0, radius, 90, 16, seed % len(BIOMES), kind, 1)
                    island._biome_pid = 0
                    columns = stamp_island(store, blocks, cfg, island,
                                           Rng(cfg.seed, seed, 5))
                    ore_ids, ore_probs = _ore_probs(blocks, cfg)
                    expected = _surface_ore_quotas(
                        len(columns), kind, ore_probs, cfg
                    )
                    counts = [sum(
                        _block_at(store, x, surface_y, z) == ore_id
                        for x, z, surface_y in columns
                    ) for ore_id in ore_ids]
                    self.assertEqual(expected, counts)
                    self.assertTrue(all(count >= minimum for count in counts))

                    # The stratified pass must cover the island rather than
                    # concentrating the guaranteed quota into one side.
                    occupied_quadrants = {
                        (x >= island.wx, z >= island.wz)
                        for x, z, surface_y in columns
                        if _block_at(store, x, surface_y, z) in set(ore_ids)
                    }
                    self.assertEqual(4, len(occupied_quadrants))

    def test_spawn_headroom_stays_clear_under_heavy_decoration(self):
        cfg = SkywarsConfig(seed=77123, ores=[], tree_density=2.0,
                            rock_density=2.0, foliage_density=2.0)
        for biome_idx in range(len(BIOMES)):
            with self.subTest(biome_idx=biome_idx):
                blocks = SkyBlocks(cfg.data_version)
                store = ChunkStore(0)
                island = Island(0, 0, 26.0, 90, 18, biome_idx, "spawn", 1)
                columns = stamp_island(store, blocks, cfg, island,
                                       Rng(cfg.seed, biome_idx, 5))
                decorate_island(store, blocks, cfg, island, columns,
                                Rng(cfg.seed, biome_idx, 9))

                for x, z, surface_y in columns:
                    if x * x + z * z <= 36:
                        self.assertEqual(AIR, _block_at(store, x, surface_y + 1, z))
                        self.assertEqual(AIR, _block_at(store, x, surface_y + 2, z))


if __name__ == "__main__":
    unittest.main()
