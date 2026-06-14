from __future__ import annotations

import sys
from collections.abc import Sequence


def build_example_command(
    *,
    workspace: str = ".",
    task: str = "inspect the workspace and summarize the project",
    profile: str = "read-only",
    model: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "agents.coding_cli",
        "--workspace",
        workspace,
        "--task",
        task,
        "--profile",
        profile,
    ]
    if model is not None:
        command.extend(["--model", model])
    return command


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print("This example does not run arbitrary arguments.")
        return 1
    print(" ".join(build_example_command()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
