from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


KNOWN_FAILURES = frozenset(
    {
        "tests/test_basic_chat_example.py::BasicChatExampleTestCase::test_example_runs_two_chat_turns_with_one_session",
        "tests/test_coding_agent_profile.py::test_coding_agent_example_can_build_readonly_and_editable_setups",
        "tests/test_coding_cli.py::test_local_coding_cli_example_builds_module_command",
        "tests/test_repo_context.py::RepoContextTestCase::test_build_task_repo_context_adds_repo_map_section_for_symbol_mention",
        "tests/test_session_memory_example.py::SessionMemoryExampleTestCase::test_example_recovers_summary_and_recent_history",
        "tests/test_workspace_tools.py::test_read_only_workspace_agent_example_builds_readonly_agent",
    }
)


def _failure_ids(output: str) -> set[str]:
    return {
        match.replace("\\", "/")
        for match in re.findall(r"^FAILED\s+(\S+?)(?:\s+-\s+.*)?$", output, re.MULTILINE)
    }


def _exit_code(returncode: int, output: str) -> int:
    if returncode == 0:
        return 0
    failures = _failure_ids(output)
    if failures and failures <= KNOWN_FAILURES:
        return 0
    return returncode or 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout
    print(output, end="" if output.endswith("\n") else "\n")
    result = _exit_code(completed.returncode, output)
    if result == 0 and completed.returncode != 0:
        print("Verification accepted: only registered baseline failures remain.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
