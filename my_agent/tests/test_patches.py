import pytest

from agents.patches import (
    PatchChange,
    PatchError,
    PatchOperation,
    PatchResult,
    apply_patch,
    dry_run_patch,
    parse_patch,
    validate_patch_paths,
)
from agents.workspace import Workspace


def test_patch_result_tracks_changed_files() -> None:
    result = PatchResult(
        dry_run=True,
        changes=(
            PatchChange(action="add", path="notes.txt"),
            PatchChange(action="delete", path="old.txt"),
        ),
    )

    assert result.success is True
    assert result.changed_files == ("notes.txt", "old.txt")


def test_patch_result_renders_model_observation() -> None:
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

    assert result.success is False
    assert result.to_observation() == {
        "success": False,
        "dry_run": False,
        "changed_files": [],
        "changes": [],
        "errors": [
            {
                "reason": "invalid_path",
                "message": "Path is outside the workspace.",
                "path": "../secret.txt",
            }
        ],
    }


def test_parse_patch_supports_add_update_delete_operations() -> None:
    patch = """*** Begin Patch
*** Add File: notes.txt
+hello
+world
*** Update File: app.py
@@
-old
+new
*** Delete File: old.txt
*** End Patch"""

    operations = parse_patch(patch)

    assert [(item.action, item.path, item.content) for item in operations] == [
        ("add", "notes.txt", "hello\nworld\n"),
        ("update", "app.py", "@@\n-old\n+new\n"),
        ("delete", "old.txt", None),
    ]


def test_parse_patch_requires_patch_envelope() -> None:
    with pytest.raises(ValueError, match="start with"):
        parse_patch("*** Add File: notes.txt\n+hello")


def test_validate_patch_paths_uses_workspace_allowed_paths(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    workspace = Workspace(tmp_path, allowed_paths=("src",))

    result = validate_patch_paths(
        (
            PatchOperation("add", "src/new.py", "hello\n"),
            PatchOperation("delete", "../outside.py"),
        ),
        workspace,
    )

    assert result.success is False
    assert result.changes == (PatchChange("add", "src/new.py"),)
    assert result.errors[0].reason == "invalid_path"
    assert result.errors[0].path == "../outside.py"


def test_validate_patch_paths_rejects_ignored_paths(tmp_path) -> None:
    workspace = Workspace(tmp_path)

    result = validate_patch_paths(
        (PatchOperation("update", ".git/config", "@@\n-old\n+new\n"),),
        workspace,
    )

    assert result.success is False
    assert result.errors[0].reason == "invalid_path"
    assert result.errors[0].path == ".git/config"


def test_dry_run_patch_returns_expected_changes_without_writing(tmp_path) -> None:
    existing_file = tmp_path / "app.py"
    existing_file.write_text("old\n", encoding="utf-8")
    workspace = Workspace(tmp_path)
    patch = """*** Begin Patch
*** Add File: notes.txt
+hello
*** Update File: app.py
@@
-old
+new
*** Delete File: stale.txt
*** End Patch"""

    result = dry_run_patch(patch, workspace)

    assert result.success is True
    assert result.dry_run is True
    assert result.changes == (
        PatchChange("add", "notes.txt"),
        PatchChange("update", "app.py"),
        PatchChange("delete", "stale.txt"),
    )
    assert (tmp_path / "notes.txt").exists() is False
    assert existing_file.read_text(encoding="utf-8") == "old\n"


def test_dry_run_patch_returns_parse_errors_as_result(tmp_path) -> None:
    result = dry_run_patch("*** Add File: notes.txt\n+hello", Workspace(tmp_path))

    assert result.success is False
    assert result.dry_run is True
    assert result.errors[0].reason == "invalid_patch"
    assert "Use the patch envelope" in result.errors[0].message


def test_apply_patch_writes_updates_and_deletes_files(tmp_path) -> None:
    app_file = tmp_path / "app.py"
    app_file.write_text("before\nold\nmore\n", encoding="utf-8")
    stale_file = tmp_path / "stale.txt"
    stale_file.write_text("remove me\n", encoding="utf-8")
    workspace = Workspace(tmp_path)
    patch = """*** Begin Patch
*** Add File: notes.txt
+hello
*** Update File: app.py
@@
 before
-old
+new
 more
*** Delete File: stale.txt
*** End Patch"""

    result = apply_patch(patch, workspace)

    assert result.success is True
    assert result.dry_run is False
    assert result.changes == (
        PatchChange("add", "notes.txt"),
        PatchChange("update", "app.py"),
        PatchChange("delete", "stale.txt"),
    )
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert app_file.read_text(encoding="utf-8") == "before\nnew\nmore\n"
    assert stale_file.exists() is False


def test_apply_patch_reports_update_no_match(tmp_path) -> None:
    app_file = tmp_path / "app.py"
    app_file.write_text("current\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: app.py
@@
-missing
+new
*** End Patch"""

    result = apply_patch(patch, Workspace(tmp_path))

    assert result.success is False
    assert result.errors[0].reason == "update_no_match"
    assert result.errors[0].path == "app.py"
    assert "missing" in result.errors[0].message
