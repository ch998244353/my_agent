from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
EXAMPLE_PATH = PROJECT_ROOT / "examples" / "basic_chat.py"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def load_example_module():
    spec = importlib.util.spec_from_file_location("basic_chat", EXAMPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BasicChatExampleTestCase(unittest.TestCase):
    def test_example_runs_two_chat_turns_with_one_session(self) -> None:
        module = load_example_module()

        answers, replay = module.run_demo()

        self.assertEqual(answers[0], "Hi Ada. I will remember that you are learning agents.")
        self.assertEqual(answers[1], "Yes, Ada. You said you are learning agents.")
        self.assertEqual(
            [message.role for message in replay],
            ["user", "assistant", "user", "assistant"],
        )
        self.assertEqual(replay[-1].content, answers[-1])


if __name__ == "__main__":
    unittest.main()
