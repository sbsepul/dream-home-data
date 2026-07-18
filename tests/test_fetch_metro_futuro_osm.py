import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_metro_futuro_osm.py"
SPEC = importlib.util.spec_from_file_location("fetch_metro_futuro_osm", SCRIPT)
metro = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(metro)


class FutureMetroTests(unittest.TestCase):
    def test_project_id(self):
        self.assertEqual(metro.project_id({"name": "Lo Errázuriz"}), "L6")
        self.assertEqual(metro.project_id({"name": "Línea 7 (en construcción)"}), "L7")
        self.assertEqual(metro.project_id({"network": "Línea 8"}), "L8")
        self.assertEqual(metro.project_id({"website": "https://www.metro.cl/nuevos-proyectos/linea-9"}), "L9")
        self.assertIsNone(metro.project_id({"name": "Línea 10"}))

    def test_build_features_groups_segments_and_stations(self):
        elements = [
            {
                "type": "way",
                "id": 1,
                "tags": {"name": "Línea 7 (en construcción)"},
                "geometry": [{"lon": -70.7, "lat": -33.4}, {"lon": -70.6, "lat": -33.4}],
            },
            {
                "type": "node",
                "id": 2,
                "lon": -70.7,
                "lat": -33.4,
                "tags": {"name": "Brasil", "network": "Línea 7", "ref": "1"},
            },
        ]
        stations, lines = metro.build_features(elements)
        self.assertEqual(stations[0]["properties"]["linea"], "L7")
        self.assertEqual(stations[0]["properties"]["orden"], 1)
        self.assertEqual(lines[0]["geometry"]["type"], "MultiLineString")
        self.assertEqual(lines[0]["properties"]["estado"], "en_construccion")


if __name__ == "__main__":
    unittest.main()
