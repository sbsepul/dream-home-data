import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_education_sources.py"
SPEC = importlib.util.spec_from_file_location("validate_education_sources", SCRIPT)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(validator)


class EducationSourceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads(
            (ROOT / "sources" / "education.json").read_text(encoding="utf-8")
        )

    def test_catalog_is_valid(self) -> None:
        self.assertEqual(validator.validate_catalog(self.catalog), [])

    def test_catalog_covers_core_families(self) -> None:
        source_ids = {source["id"] for source in self.catalog["sources"]}

        self.assertTrue(
            {
                "mineduc_school_directory",
                "agency_simce",
                "agency_idps",
                "demre_paes_school_reports",
                "sies_institutions",
                "sies_academic_offering",
                "cna_accreditation",
            }
            <= source_ids
        )


if __name__ == "__main__":
    unittest.main()
