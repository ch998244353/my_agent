from agents.edit_tools import create_apply_patch_tool
from agents.workspace import Workspace


def test_create_apply_patch_tool_defaults_to_approval(tmp_path) -> None:
    tool = create_apply_patch_tool(Workspace(tmp_path))

    assert tool.name == "apply_patch"
    assert tool.needs_approval is True
    assert [argument.name for argument in tool.spec.arguments] == ["patch", "dry_run"]


def test_apply_patch_tool_can_dry_run_without_writing(tmp_path) -> None:
    tool = create_apply_patch_tool(Workspace(tmp_path))
    patch = """*** Begin Patch
*** Add File: notes.txt
+hello
*** End Patch"""

    result = tool.execute({"patch": patch, "dry_run": True})

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["changed_files"] == ["notes.txt"]
    assert (tmp_path / "notes.txt").exists() is False


def test_apply_patch_tool_applies_patch_when_not_dry_run(tmp_path) -> None:
    tool = create_apply_patch_tool(Workspace(tmp_path))
    patch = """*** Begin Patch
*** Add File: notes.txt
+hello
*** End Patch"""

    result = tool.execute({"patch": patch, "dry_run": False})

    assert result["success"] is True
    assert result["dry_run"] is False
    assert result["changed_files"] == ["notes.txt"]
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello\n"
