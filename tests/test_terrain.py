"""Procedural terrain: determinism, range, and the opening-position search."""

import unittest

from flight_sim.terrain import Terrain


class TestTerrain(unittest.TestCase):
    def setUp(self):
        self.world = Terrain(seed=20260905)

    def test_is_deterministic(self):
        other = Terrain(seed=20260905)
        for x, y in [(0, 0), (13.5, -42.25), (999.9, 1234.5), (-500, 700)]:
            self.assertEqual(self.world.elevation(x, y), other.elevation(x, y))

    def test_different_seeds_give_different_worlds(self):
        other = Terrain(seed=7)
        differences = sum(
            1
            for i in range(50)
            if abs(self.world.elevation(i * 3.1, i * 1.7)
                   - other.elevation(i * 3.1, i * 1.7)) > 1.0
        )
        self.assertGreater(differences, 40)

    def test_elevation_stays_within_designed_range(self):
        lowest, highest = 1e9, -1e9
        for i in range(4000):
            x = (i % 97) * 4.3 - 200
            y = (i // 97) * 3.7 - 150
            elevation = self.world.elevation(x, y)
            lowest = min(lowest, elevation)
            highest = max(highest, elevation)
        self.assertGreaterEqual(lowest, 100.0)
        self.assertLess(highest, 10000.0)
        # The world must actually contain mountains, not just gentle hills.
        self.assertGreater(highest, 6000.0)

    def test_elevation_is_continuous(self):
        """No cliffs between adjacent samples -- the interpolation must be smooth."""
        previous = self.world.elevation(10.0, 10.0)
        for step in range(1, 60):
            current = self.world.elevation(10.0 + step * 0.05, 10.0)
            self.assertLess(abs(current - previous), 400.0)
            previous = current

    def test_height_above_ground(self):
        elevation = self.world.elevation(5.0, 5.0)
        self.assertAlmostEqual(
            self.world.height_above_ground(5.0, 5.0, elevation + 1000.0), 1000.0
        )
        self.assertLess(self.world.height_above_ground(5.0, 5.0, elevation - 10.0), 0)

    def test_ahead_projects_along_the_compass(self):
        # 090 is due east: +x. 000 is due north: +y.
        x, y = self.world.ahead(0.0, 0.0, 90.0, 10.0)
        self.assertAlmostEqual(x, 10.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)
        x, y = self.world.ahead(0.0, 0.0, 0.0, 10.0)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 10.0, places=6)

    def test_highest_ahead_finds_the_worst_terrain_in_range(self):
        distance, elevation = self.world.highest_ahead(0.0, 0.0, 90.0, max_nm=12.0)
        self.assertTrue(0.0 < distance <= 12.0)
        samples = [
            self.world.elevation(*self.world.ahead(0.0, 0.0, 90.0, d * 0.5))
            for d in range(1, 25)
        ]
        self.assertAlmostEqual(elevation, max(samples), places=6)

    def test_scan_ahead_returns_one_sample_per_distance(self):
        samples = self.world.scan_ahead(0.0, 0.0, 45.0, distances=(1, 3, 5, 10))
        self.assertEqual([d for d, _ in samples], [1, 3, 5, 10])

    def test_find_start_gives_clearance_and_a_clear_corridor(self):
        for seed in (1, 42, 20260905, 777):
            world = Terrain(seed=seed)
            x, y = world.find_start(5000.0, 90.0)
            self.assertLess(
                world.elevation(x, y), 5000.0 - 2800.0,
                "seed {} started too high".format(seed),
            )
            _distance, highest = world.highest_ahead(x, y, 90.0, max_nm=18.0)
            self.assertLess(
                highest, 5000.0 - 900.0,
                "seed {} started facing a wall".format(seed),
            )

    def test_find_start_is_deterministic(self):
        self.assertEqual(
            Terrain(seed=42).find_start(5000.0, 90.0),
            Terrain(seed=42).find_start(5000.0, 90.0),
        )

    def test_feature_names_are_stable_and_local(self):
        """The same ridge keeps its name; a distant one gets another."""
        name = self.world.feature_name(20.0, 20.0)
        self.assertEqual(name, self.world.feature_name(20.5, 20.5))
        self.assertTrue(name)
        far_names = {
            self.world.feature_name(x * 40.0, 0.0) for x in range(1, 12)
        }
        self.assertGreater(len(far_names), 1)


if __name__ == "__main__":
    unittest.main()
