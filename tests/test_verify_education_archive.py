import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_education_archive.py"
SPEC = importlib.util.spec_from_file_location("verify_education_archive", SCRIPT)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verifier)


class EducationArchiveVerificationTests(unittest.TestCase):
    def test_valid_release_and_checksum_failure(self):
        with TemporaryDirectory() as directory:
            release_dir = Path(directory) / "source_id" / "2025"
            release_dir.mkdir(parents=True)
            payload = b"source data"
            source_path = release_dir / "source.zip"
            source_path.write_bytes(payload)
            metadata = {
                "source_id": "source_id",
                "release_id": "2025",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            (release_dir / "metadata.json").write_text(json.dumps(metadata))

            errors, releases, total_bytes = verifier.verify_archive(Path(directory))
            self.assertEqual(errors, [])
            self.assertEqual((releases, total_bytes), (1, len(payload)))

            source_path.write_bytes(b"changed")
            errors, _, _ = verifier.verify_archive(Path(directory))
            self.assertTrue(any("SHA-256" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
