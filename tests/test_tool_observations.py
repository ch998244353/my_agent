from __future__ import annotations

import unittest

from agents.environment import CommandResult
from agents.patches import PatchChange, PatchError, PatchResult
from agents import tool_observations
from agents.tool_observations import ToolObservation, command_result_observation


class ToolObservationTestCase(unittest.TestCase):
    def test_to_dict_returns_json_safe_observation_fields(self) -> None:
        observation = ToolObservation(
            tool_name="run_shell_command",
            status="ok",
            summary="Command completed.",
            details={"returncode": 0, "cwd": "."},
            output="done",
        )

        self.assertEqual(
            observation.to_dict(),
            {
                "tool_name": "run_shell_command",
                "status": "ok",
                "summary": "Command completed.",
                "details": {"returncode": 0, "cwd": "."},
                "output": "done",
                "truncated": False,
            },
        )

    def test_to_text_renders_stable_headings_and_details(self) -> None:
        observation = ToolObservation(
            tool_name="apply_patch",
            status="error",
            summary="Patch failed.",
            details={"dry_run": False, "error_count": 1},
            output="missing file",
        )

        self.assertEqual(
            observation.to_text(),
            "\n".join(
                [
                    "Tool observation",
                    "tool: apply_patch",
                    "status: error",
                    "summary: Patch failed.",
                    "details:",
                    "  dry_run: false",
                    "  error_count: 1",
                    "output:",
                    "missing file",
                ]
            ),
        )

    def test_to_text_clips_output_without_mutating_observation(self) -> None:
        observation = ToolObservation(
            tool_name="run_test_command",
            status="ok",
            summary="Tests passed.",
            output="abcdef",
        )

        rendered = observation.to_text(max_chars=3)

        self.assertIn("abc", rendered)
        self.assertIn("[tool output truncated:", rendered)
        self.assertFalse(observation.truncated)

    def test_command_result_observation_maps_success_to_structured_fields(self) -> None:
        result = CommandResult(
            command="python -m pytest",
            cwd="C:/work/project",
            returncode=0,
            stdout="3 passed",
        )

        observation = command_result_observation("run_test_command", result)

        self.assertEqual(observation.status, "ok")
        self.assertEqual(observation.summary, "Command completed with exit code 0.")
        self.assertEqual(
            observation.details,
            {
                "command": "python -m pytest",
                "cwd": "C:/work/project",
                "returncode": 0,
                "timed_out": False,
            },
        )
        self.assertEqual(observation.output, "stdout:\n3 passed")

    def test_command_result_observation_maps_failure_and_combines_streams(self) -> None:
        result = CommandResult(
            command="python -m pytest",
            cwd=".",
            returncode=1,
            stdout="1 failed",
            stderr="traceback",
        )

        observation = command_result_observation("run_test_command", result)

        self.assertEqual(observation.status, "error")
        self.assertEqual(observation.summary, "Command failed with exit code 1.")
        self.assertEqual(observation.output, "stdout:\n1 failed\nstderr:\ntraceback")

    def test_command_result_observation_marks_timeout_and_truncation(self) -> None:
        result = CommandResult(
            command="python slow.py",
            cwd=".",
            returncode=None,
            stdout="abcdef",
            timed_out=True,
        )

        observation = command_result_observation(
            "run_shell_command",
            result,
            max_chars=3,
        )

        self.assertEqual(observation.status, "error")
        self.assertEqual(observation.summary, "Command timed out.")
        self.assertTrue(observation.truncated)
        self.assertIn("[tool output truncated:", observation.output)

    def test_patch_result_observation_maps_success_to_structured_fields(self) -> None:
        result = PatchResult(
            dry_run=True,
            changes=(
                PatchChange(action="add", path="notes.txt"),
                PatchChange(action="update", path="app.py"),
            ),
        )

        observation = tool_observations.patch_result_observation("apply_patch", result)

        self.assertEqual(observation.status, "ok")
        self.assertEqual(observation.summary, "Patch dry run completed with 2 change(s).")
        self.assertEqual(
            observation.details,
            {
                "dry_run": True,
                "changed_files": ["notes.txt", "app.py"],
                "change_count": 2,
                "error_count": 0,
            },
        )
        self.assertEqual(observation.output, "changes:\nadd notes.txt\nupdate app.py")

    def test_patch_result_observation_maps_errors_to_structured_fields(self) -> None:
        result = PatchResult(
            dry_run=False,
            errors=(
                PatchError(
                    reason="invalid_path",
                    message="Path is outside the workspace.",
                    path="../secret.txt",
                ),
            ),
        )

        observation = tool_observations.patch_result_observation("apply_patch", result)

        self.assertEqual(observation.status, "error")
        self.assertEqual(observation.summary, "Patch failed with 1 error(s).")
        self.assertEqual(
            observation.details,
            {
                "dry_run": False,
                "changed_files": [],
                "change_count": 0,
                "error_count": 1,
            },
        )
        self.assertEqual(
            observation.output,
            "errors:\ninvalid_path: Path is outside the workspace. (../secret.txt)",
        )


if __name__ == "__main__":
    unittest.main()
