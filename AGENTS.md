# AGENTS.md

## Project Intent

This repository is a Python coding-agent project. Changes should preserve the existing architecture, naming style, module boundaries, and data-flow assumptions unless the task explicitly asks for redesign.

The agent should optimize for maintainable, minimal, reviewable changes.

## Core Working Rules

* Prefer minimal, targeted diffs.
* Do not refactor unrelated code.
* Do not rewrite working code just to make it look cleaner.
* Do not introduce new abstractions unless the current task has at least two concrete call sites or implementations that need the abstraction.
* Do not add speculative extension points, registries, factories, managers, adapters, protocols, or service layers unless explicitly required.
* Preserve existing public APIs unless the task explicitly asks to change them.
* Preserve existing file layout unless the task explicitly asks to reorganize files.
* Prefer direct, readable code over overly generic code.

## Anti-Overengineering Rules

Do not add code for hypothetical future requirements.

Avoid these patterns unless the task explicitly requires them:

* generic plugin systems
* premature interface/protocol extraction
* unnecessary inheritance
* unnecessary dependency injection
* configuration systems for values that are not currently configurable
* fallback branches for states that cannot occur
* compatibility layers for versions that are not supported
* broad utility functions used by only one caller
* “just in case” error handling

If a proposed abstraction has only one current use, keep the code concrete.

## Defensive Code Policy

Validate data once at real system boundaries.

Real system boundaries include:

* user input
* CLI arguments
* file system input
* JSON/YAML/TOML loading
* network/API responses
* database records
* subprocess output
* external tool calls
* MCP/plugin/tool arguments

Internal helper functions may assume:

* documented invariants hold
* dataclass constructors already validated their fields
* upstream boundary validation already ran
* typed internal objects have the expected attributes
* impossible states should fail fast rather than be silently repaired

Do not add repeated internal checks such as:

* `if x is None` when `x` is not optional
* `hasattr` checks for known internal types
* `getattr(obj, "field", default)` for required fields
* broad `try/except Exception`
* silent fallback to empty strings, empty lists, empty dicts, or default objects
* swallowing exceptions and continuing with partial state

Use direct attribute access for known internal types.

Prefer:

```python
workspace = setup.workspace
```

over:

```python
workspace = getattr(setup, "workspace", None)
if workspace is None:
    ...
```

unless `setup` is genuinely dynamic or externally supplied.

## Error Handling Policy

Prefer fail-fast behavior for broken internal invariants.

Good error handling:

* checks external input at the boundary
* raises a clear, specific exception
* preserves the original error when useful
* does not hide corrupted state

Bad error handling:

* catches broad exceptions without a concrete recovery path
* logs and continues when the program state is invalid
* returns partial or fake data
* converts programmer errors into silent fallbacks
* makes tests pass by hiding the real failure

## Change Scope Policy

Before editing, identify the smallest set of files needed.

Do not modify:

* unrelated modules
* formatting-only sections
* unrelated imports
* public behavior not mentioned in the task
* tests unrelated to the changed behavior
* documentation unrelated to the changed behavior

If a task reveals a larger design issue, explain it separately instead of fixing it opportunistically.

## Architecture Policy

Classify changed code before editing:

* boundary layer: validate external input strictly
* core logic layer: keep assumptions clear and code direct
* serialization layer: normalize data explicitly and once
* tool integration layer: validate tool arguments and external effects
* test layer: test real behavior and real failure paths

Boundary layer may contain defensive checks.

Core logic layer should not duplicate boundary validation.

## Testing Policy

Run the narrowest relevant tests first.

When changing Python code, prefer:

```bash
python -m pytest <relevant-test-file>
```

If no targeted test exists, run the smallest available test group that covers the change.

Do not add large test scaffolding for simple behavior.

Do not weaken existing tests to make new code pass.

## Review Checklist Before Final Response

Before finishing, verify:

* The diff is minimal.
* No unrelated refactor was introduced.
* No unnecessary defensive code was added.
* No broad `try/except` was added.
* No silent fallback was added.
* No new abstraction was added without current need.
* Boundary validation and internal assumptions are not duplicated.
* The changed behavior is covered by an existing or new targeted test when practical.

## Required Final Response

When reporting changes, include:

* files changed
* behavior changed
* tests run
* any intentional non-change

If defensive code was added, explain:

* which invalid input it protects against
* where that invalid input comes from
* why upstream validation is insufficient
