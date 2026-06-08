from __future__ import annotations

from .contracts import ToolArgument, ToolSpec
from .patches import apply_patch as apply_workspace_patch
from .patches import dry_run_patch
from .tools import FunctionTool, ToolApproval
from .workspace import Workspace


def create_apply_patch_tool(
    workspace: Workspace,
    *,
    needs_approval: ToolApproval = True,
) -> FunctionTool:
    def apply_patch_tool(patch: str, dry_run: bool = False) -> dict[str, object]:
        result = dry_run_patch(patch, workspace) if dry_run else apply_workspace_patch(
            patch,
            workspace,
        )
        return result.to_observation()

    return FunctionTool(
        spec=ToolSpec(
            name="apply_patch",
            description=(
                "Apply a structured patch inside the workspace. "
                "Use dry_run=true to validate and preview changes without writing files."
            ),
            arguments=[
                ToolArgument(
                    name="patch",
                    description=(
                        "Patch text using Begin Patch plus Add/Update/Delete File sections."
                    ),
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="dry_run",
                    description="When true, validate only and do not write files.",
                    schema={"type": "boolean"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=apply_patch_tool,
        needs_approval=needs_approval,
    )


__all__ = ["create_apply_patch_tool"]
