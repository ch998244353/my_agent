from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import ChatMessage, CompactionPolicy, JsonSession  # noqa: E402


def build_demo_messages(path: str | Path) -> list[ChatMessage]:
    session = JsonSession(
        path,
        compaction_policy=CompactionPolicy(
            compact_after_turns=1,
            keep_recent_turns=1,
        ),
    )
    session.clear_session()
    session.add_items([
        ChatMessage(role="user", content="Remember project language: Python."),
        ChatMessage(role="assistant", content="Project language is Python."),
    ])
    session.add_items([
        ChatMessage(role="user", content="Recent task: add tests."),
        ChatMessage(role="assistant", content="Next step is adding tests."),
    ])

    restored = JsonSession(path)
    return restored.get_items()


def main() -> None:
    path = ROOT / ".session_memory_demo.json"
    for message in build_demo_messages(path):
        print(f"{message.role}: {message.content}")


if __name__ == "__main__":
    main()
