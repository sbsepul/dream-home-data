import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive_education_source.py"
SPEC = importlib.util.spec_from_file_location("archive_education_source", SCRIPT)
archiver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = archiver
SPEC.loader.exec_module(archiver)


class FakeHeaders:
    def get_content_type(self):
        return "application/vnd.rar"

    def get(self, name):
        return "Wed, 12 Nov 2025 15:00:00 GMT" if name == "Last-Modified" else None


class FakeResponse:
    def __init__(self, payload: bytes, url: str):
        self.payload = payload
        self.url = url
        self.offset = 0
        self.headers = FakeHeaders()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if size == -1:
            self.offset = len(self.payload)
            return self.payload
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def geturl(self):
        return self.url


class EducationArchiverTests(unittest.TestCase):
    def test_candidates_are_deduplicated_and_newest_first(self):
        html = """
        <a href="/files/Directorio-2024.rar">2024</a>
        <a href="/files/Directorio-2025.rar">2025</a>
        <a href="/files/Directorio-2025.rar">duplicado</a>
        <a href="/files/readme.pdf">ignorar</a>
        """
        candidates = archiver.release_candidates(html, "https://example.test/dataset/")
        self.assertEqual([candidate.release_id for candidate in candidates], ["2025", "2024"])
        self.assertEqual(candidates[0].extension, ".rar")

    def test_link_pattern_selects_one_file_per_year(self):
        html = """
        <a href="/u/PAES-2026-Inscritos-Puntajes.rar">puntajes</a>
        <a href="/u/PAES-2026-Postulantes.rar">postulantes</a>
        <a href="/u/PAES_2025_Inscritos_Puntajes.rar">puntajes</a>
        """
        candidates = archiver.release_candidates(html, "https://example.test/", r"Inscritos[-_]Puntajes")
        self.assertEqual(
            [candidate.url for candidate in candidates],
            ["https://example.test/u/PAES-2026-Inscritos-Puntajes.rar", "https://example.test/u/PAES_2025_Inscritos_Puntajes.rar"],
        )

    def test_agency_rest_listing_resolves_yearly_bundles(self):
        listing = {
            "success": True,
            "data": [
                {"nombre": "Simce 2025 - Todos los grados", "uuid": "a;1.0"},
                {"nombre": "Simce 2do medio 2025.rar", "uuid": "b;1.0"},
                {"nombre": "Simce 2024.rar", "uuid": "c;1.0"},
                {"nombre": "IDPS 2025.rar", "uuid": "d;1.0"},
            ],
        }
        candidates = archiver.agency_candidates(
            listing, "https://agency.test/rest/", r"^Simce (?P<year>\d{4})(\.rar| - Todos los grados.*)?$"
        )
        self.assertEqual([candidate.release_id for candidate in candidates], ["2025", "2024"])
        self.assertEqual(candidates[0].url, "https://agency.test/rest/archivo/obtener?uuid=a;1.0")
        self.assertEqual(candidates[0].extension, ".rar")

    def test_metadata_records_catalog_license(self):
        source = {"id": "s", "landing_url": "https://example.test/", "license": "CC BY", "terms_url": "https://example.test/terms"}
        candidate = archiver.ReleaseCandidate("https://example.test/f-2025.zip", "2025", ".zip")
        with TemporaryDirectory() as directory:
            with patch.object(archiver, "urlopen", return_value=FakeResponse(b"x", candidate.url)):
                release_dir, _ = archiver.archive_release(source, candidate, Path(directory))
            metadata = json.loads((release_dir / "metadata.json").read_text())
        self.assertEqual((metadata["license"], metadata["terms_url"]), ("CC BY", "https://example.test/terms"))

    def test_archive_is_idempotent_and_records_provenance(self):
        source = {
            "id": "mineduc_school_directory",
            "landing_url": "https://example.test/dataset/",
        }
        candidate = archiver.ReleaseCandidate(
            "https://example.test/Directorio-2025.rar", "2025", ".rar"
        )
        payload = b"release-content"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                archiver, "urlopen", side_effect=lambda *_args, **_kwargs: FakeResponse(payload, candidate.url)
            ):
                release_dir, created = archiver.archive_release(source, candidate, root)
                same_dir, created_again = archiver.archive_release(source, candidate, root)

            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(same_dir, release_dir)
            self.assertEqual((release_dir / "source.rar").read_bytes(), payload)
            metadata = json.loads((release_dir / "metadata.json").read_text())
            self.assertEqual(metadata["release_id"], "2025")
            self.assertEqual(metadata["upstream_release_id"], "2025")
            self.assertEqual(metadata["bytes"], len(payload))
            self.assertEqual(len(metadata["sha256"]), 64)

    def test_silent_correction_creates_revision(self):
        source = {"id": "source", "landing_url": "https://example.test/"}
        candidate = archiver.ReleaseCandidate("https://example.test/file-2025.zip", "2025", ".zip")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(archiver, "urlopen", return_value=FakeResponse(b"first", candidate.url)):
                first_dir, _ = archiver.archive_release(source, candidate, root)
            with patch.object(archiver, "urlopen", return_value=FakeResponse(b"corrected", candidate.url)):
                revision_dir, created = archiver.archive_release(source, candidate, root)

            self.assertTrue(created)
            self.assertEqual(first_dir.name, "2025")
            self.assertRegex(revision_dir.name, r"^2025-revision-[0-9a-f]{12}$")
            self.assertTrue((revision_dir / "source.zip").is_file())


if __name__ == "__main__":
    unittest.main()
