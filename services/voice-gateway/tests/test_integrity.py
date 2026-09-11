import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from voice_gateway.integrity import verify_model_lock


class IntegrityTests(unittest.TestCase):
    def test_verify_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "models" / "vad" / "tiny.onnx"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            expected = hashlib.sha256(b"model").hexdigest()
            lock = {
                "assets": {
                    "vad": {
                        "files": {"tiny.onnx": expected}
                    }
                }
            }
            (root / "models" / "models.lock.json").write_text(
                json.dumps(lock), encoding="utf-8"
            )
            results = verify_model_lock(root)
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].valid)


if __name__ == "__main__":
    unittest.main()

