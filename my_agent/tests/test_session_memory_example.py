from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
EXAMPLE_PATH = PROJECT_ROOT / "examples" / "session_memory_compaction.py"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def load_example_module():
    spec = importlib.util.spec_from_file_location(
        "session_memory_compaction",
        EXAMPLE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SessionMemoryExampleTestCase(unittest.TestCase):
    def test_example_recovers_summary_and_recent_history(self) -> None:
        module = load_example_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"

            messages = module.build_demo_messages(path)
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(messages[0].role, "system")
        self.assertIn("Conversation summary:", messages[0].content)
        self.assertIn("Remember project language", messages[0].content)
        self.assertEqual([message.content for message in messages[1:]], [
            "Recent task: add tests.",
            "Next step is adding tests.",
        ])
        self.assertIsNotNone(raw["session"]["summary"])
        self.assertEqual(len(raw["session"]["turns"]), 1)


if __name__ == "__main__":
    unittest.main()
