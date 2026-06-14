from __future__ import annotations

from .coding_policies import PatchApprovalPolicy
from .contracts import ToolArgument, ToolSpec
from .patches import apply_patch as apply_workspace_patch
from .patches import dry_run_patch
from .tool_observations import patch_result_observation
from .tools import FunctionTool, ToolApproval
from .workspace import Workspace


def create_apply_patch_tool(
    workspace: Workspace,
    *,
    needs_approval: ToolApproval = True,
    patch_policy: PatchApprovalPolicy | None = None,
) -> FunctionTool:
    def apply_patch_tool(patch: str, dry_run: bool = False) -> dict[str, object]:
        result = dry_run_patch(patch, workspace) if dry_run else apply_workspace_patch(
            patch,
            workspace,
        )
        observation = patch_result_observation("apply_patch", result)
        payload = result.to_observation()
        payload.update(
            {
                "status": observation.status,
                "summary": observation.summary,
                "change_count": len(result.changes),
                "error_count": len(result.errors),
                "observation": observation.to_text(),
            }
        )
        return payload

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
        needs_approval=(
            patch_policy.needs_approval
            if patch_policy is not None and needs_approval is True
            else needs_approval
        ),
    )


__all__ = ["create_apply_patch_tool"]
