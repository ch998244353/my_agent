from __future__ import annotations

from scripts.verify_current_baseline import _exit_code, _failure_ids


def test_failure_ids_extract_pytest_summary_nodes() -> None:
    output = "\n".join(
        (
            "FAILED tests/test_one.py::test_first - AssertionError",
            "FAILED tests/test_two.py::Case::test_second - RuntimeError",
        )
    )

    assert _failure_ids(output) == {
        "tests/test_one.py::test_first",
        "tests/test_two.py::Case::test_second",
    }


def test_exit_code_allows_only_the_registered_baseline_failures() -> None:
    known = "FAILED tests/test_basic_chat_example.py::BasicChatExampleTestCase::test_example_runs_two_chat_turns_with_one_session - FileNotFoundError"
    unexpected = "FAILED tests/test_new.py::test_regression - AssertionError"

    assert _exit_code(1, known) == 0
    assert _exit_code(1, f"{known}\n{unexpected}") == 1
    assert _exit_code(2, "pytest crashed before summary") == 2
    assert _exit_code(0, "all passed") == 0
