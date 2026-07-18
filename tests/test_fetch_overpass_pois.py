import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_overpass_pois.py"
SPEC = importlib.util.spec_from_file_location("fetch_overpass_pois", SCRIPT)
pois = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pois)


class MallClassificationTests(unittest.TestCase):
    def test_capacity_estimate(self):
        self.assertEqual(pois.estimate_mall_capacity(15_000), 3_750)
        self.assertEqual(pois.estimate_mall_capacity(2_999), 749)
        self.assertIsNone(pois.estimate_mall_capacity(None))
        self.assertIsNone(pois.estimate_mall_capacity(0))

    def test_capacity_boundaries(self):
        cases = [
            (None, "sin_dato"),
            (749, "chico"),
            (750, "mediano"),
            (3_749, "mediano"),
            (3_750, "grande"),
        ]
        for capacity, expected in cases:
            with self.subTest(capacity=capacity):
                self.assertEqual(pois.classify_mall_capacity(capacity), expected)

    def test_store_boundaries(self):
        cases = [
            (None, "sin_dato"),
            (14, "chico"),
            (15, "mediano"),
            (59, "mediano"),
            (60, "grande"),
        ]
        for stores, expected in cases:
            with self.subTest(stores=stores):
                self.assertEqual(pois.classify_mall_stores(stores), expected)

    def test_final_size_uses_strongest_available_signal(self):
        self.assertEqual(pois.classify_mall_size(4_000, 2), "grande")
        self.assertEqual(pois.classify_mall_size(200, 70), "grande")
        self.assertEqual(pois.classify_mall_size(1_000, 2), "mediano")
        self.assertEqual(pois.classify_mall_size(None, None), "sin_dato")

    def test_element_center_supports_all_overpass_shapes(self):
        self.assertEqual(pois.element_center({"type": "node", "lon": -70, "lat": -33}), (-70, -33))
        self.assertEqual(
            pois.element_center({"type": "way", "center": {"lon": -71, "lat": -34}}),
            (-71, -34),
        )
        self.assertIsNone(pois.element_center({"type": "way"}))


if __name__ == "__main__":
    unittest.main()
