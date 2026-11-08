from __future__ import annotations

import pytest


def _load_repo_map_api():
    try:
        from agents.repo_map import (
            RepoMapCandidate,
            RepoMapRenderResult,
            RepoMapSearchHit,
            rank_repo_map_candidates,
            render_repo_map_section,
        )
    except ModuleNotFoundError as exc:
        raise AssertionError(f"repo map API is missing: {exc}") from exc
    return (
        RepoMapCandidate,
        RepoMapRenderResult,
        RepoMapSearchHit,
        rank_repo_map_candidates,
        render_repo_map_section,
    )


def test_repo_map_candidate_and_search_hit_contracts_expose_stable_fields() -> None:
    RepoMapCandidate, _, RepoMapSearchHit, _, _ = _load_repo_map_api()

    candidate = RepoMapCandidate(
        path="pkg/service.py",
        score=100,
        reasons=("symbol definition: Service",),
    )
    search_hit = RepoMapSearchHit(
        path="pkg/service.py",
        line=4,
        query="Service",
        text="class Service:",
    )

    assert (candidate.path, candidate.score, candidate.reasons, candidate.symbols) == (
        "pkg/service.py",
        100,
        ("symbol definition: Service",),
        (),
    )
    assert (search_hit.path, search_hit.line, search_hit.query, search_hit.text) == (
        "pkg/service.py",
        4,
        "Service",
        "class Service:",
    )


def test_rank_repo_map_candidates_puts_mentioned_symbol_definition_first() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.repo_symbols import SymbolTag
    from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory

    _, _, _, rank_repo_map_candidates, _ = _load_repo_map_api()

    tags = (
        SymbolTag(path="pkg/controller.py", name="Controller", kind="class", line=1),
        SymbolTag(path="pkg/service.py", name="Service", kind="class", line=3),
    )
    mentions = (MentionCandidate(text="Service", kind="symbol", confidence=0.7),)
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="pkg/controller.py", kind="file"),
            WorkspaceFileEntry(path="pkg/service.py", kind="file"),
        ),
    )

    candidates = rank_repo_map_candidates(
        tags,
        selected_paths=(),
        mentions=mentions,
        inventory=inventory,
    )

    assert [candidate.path for candidate in candidates] == ["pkg/service.py"]
    assert candidates[0].score == 100
    assert "symbol definition: Service" in candidates[0].reasons
    assert candidates[0].symbols == (tags[1],)


def test_rank_repo_map_candidates_orders_path_evidence_and_dedupes_reasons() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory

    _, _, _, rank_repo_map_candidates, _ = _load_repo_map_api()

    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="pkg/service.py", kind="file"),
            WorkspaceFileEntry(path="pkg/view.py", kind="file"),
            WorkspaceFileEntry(path="pkg/notes.py", kind="file"),
        ),
    )
    mentions = (
        MentionCandidate(
            text="pkg/service.py",
            kind="path",
            confidence=1.0,
            matched_path="pkg/service.py",
        ),
        MentionCandidate(
            text="service.py",
            kind="filename",
            confidence=0.8,
            matched_path="pkg/service.py",
        ),
        MentionCandidate(
            text="service.py",
            kind="filename",
            confidence=0.8,
            matched_path="pkg/service.py",
        ),
        MentionCandidate(text="pkg/view.py", kind="path", confidence=1.0),
        MentionCandidate(text="notes.py", kind="filename", confidence=0.8),
    )

    candidates = rank_repo_map_candidates(
        (),
        selected_paths=(),
        mentions=mentions,
        inventory=inventory,
    )

    assert [(candidate.path, candidate.score) for candidate in candidates] == [
        ("pkg/service.py", 140),
        ("pkg/notes.py", 20),
        ("pkg/view.py", 20),
    ]
    assert candidates[0].reasons == (
        "path mention: pkg/service.py",
        "basename mention: service.py",
    )


def test_rank_repo_map_candidates_excludes_selected_paths_even_when_high_score() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.repo_symbols import SymbolTag
    from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory

    _, _, _, rank_repo_map_candidates, _ = _load_repo_map_api()
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="pkg/service.py", kind="file"),
            WorkspaceFileEntry(path="pkg/controller.py", kind="file"),
        ),
    )

    candidates = rank_repo_map_candidates(
        (
            SymbolTag(path="pkg/service.py", name="Service", kind="class", line=1),
            SymbolTag(path="pkg/controller.py", name="Controller", kind="class", line=1),
        ),
        selected_paths=("pkg/service.py",),
        mentions=(
            MentionCandidate(text="Service", kind="symbol", confidence=0.7),
            MentionCandidate(text="Controller", kind="symbol", confidence=0.7),
        ),
        inventory=inventory,
    )

    assert [candidate.path for candidate in candidates] == ["pkg/controller.py"]


def test_rank_repo_map_candidates_keeps_ambiguous_basename_matches() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory

    _, _, _, rank_repo_map_candidates, _ = _load_repo_map_api()
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="pkg/service.py", kind="file"),
            WorkspaceFileEntry(path="tests/service.py", kind="file"),
        ),
    )

    candidates = rank_repo_map_candidates(
        (),
        selected_paths=(),
        mentions=(MentionCandidate(text="service.py", kind="filename", confidence=0.5),),
        inventory=inventory,
    )

    assert [candidate.path for candidate in candidates] == [
        "pkg/service.py",
        "tests/service.py",
    ]
    assert all("ambiguous basename: service.py" in candidate.reasons for candidate in candidates)


def test_rank_repo_map_candidates_merges_search_and_symbol_evidence() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.repo_symbols import SymbolTag
    from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory

    _, _, RepoMapSearchHit, rank_repo_map_candidates, _ = _load_repo_map_api()
    service_tag = SymbolTag(path="pkg/service.py", name="Service", kind="class", line=3)
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="pkg/service.py", kind="file"),
            WorkspaceFileEntry(path="pkg/view.py", kind="file"),
        ),
    )

    candidates = rank_repo_map_candidates(
        (service_tag,),
        selected_paths=(),
        mentions=(MentionCandidate(text="Service", kind="symbol", confidence=0.7),),
        inventory=inventory,
        search_hits=(
            RepoMapSearchHit(
                path="pkg/service.py",
                line=7,
                query="Service",
                text="return Service()",
            ),
            RepoMapSearchHit(
                path="pkg/view.py",
                line=2,
                query="Service",
                text="from pkg.service import Service",
            ),
        ),
    )

    assert [(candidate.path, candidate.score) for candidate in candidates] == [
        ("pkg/service.py", 140),
        ("pkg/view.py", 40),
    ]
    assert candidates[0].symbols == (service_tag,)
    assert candidates[0].reasons == (
        "symbol definition: Service",
        "search hit: Service",
    )


def test_repo_context_section_metadata_is_immutable_copy() -> None:
    from agents.repo_context import RepoContextSection

    metadata = {"included_paths": ("pkg/service.py",), "truncated": False}
    section = RepoContextSection(
        title="Repo map",
        content="pkg/service.py",
        source="repo_map",
        priority=15,
        metadata=metadata,
    )
    metadata["truncated"] = True

    assert section.metadata["truncated"] is False
    with pytest.raises(TypeError):
        section.metadata["included_paths"] = ()


def test_render_repo_map_section_returns_metadata_backed_result() -> None:
    RepoMapCandidate, RepoMapRenderResult, _, _, render_repo_map_section = _load_repo_map_api()
    candidate = RepoMapCandidate(
        path="pkg/service.py",
        score=100,
        reasons=("symbol definition: Service",),
    )

    result = render_repo_map_section((candidate,), reader=object())

    assert isinstance(result, RepoMapRenderResult)
    assert result.section is not None
    assert result.section.source == "repo_map"
    assert result.included_paths == ("pkg/service.py",)
    assert result.truncated is False
    assert result.section.metadata["included_paths"] == result.included_paths
    assert result.section.metadata["truncated"] == result.truncated


def test_render_repo_map_section_reads_first_symbol_lines() -> None:
    from agents.repo_symbols import SymbolTag

    RepoMapCandidate, _, _, _, render_repo_map_section = _load_repo_map_api()

    class FakeReader:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, int | None, int]] = []

        def read_lines(
            self,
            path: str,
            *,
            start_line: int,
            end_line: int | None = None,
            max_lines: int = 120,
        ) -> dict[str, object]:
            self.calls.append((path, start_line, end_line, max_lines))
            return {
                "path": path,
                "start_line": start_line,
                "end_line": start_line,
                "lines": [{"path": path, "line": start_line, "text": f"def symbol_{start_line}():"}],
                "truncated": False,
            }

    reader = FakeReader()
    candidate = RepoMapCandidate(
        path="pkg/service.py",
        score=100,
        reasons=("symbol definition: Service",),
        symbols=(
            SymbolTag(path="pkg/service.py", name="one", kind="function", line=1),
            SymbolTag(path="pkg/service.py", name="two", kind="function", line=2),
            SymbolTag(path="pkg/service.py", name="three", kind="function", line=3),
            SymbolTag(path="pkg/service.py", name="four", kind="function", line=4),
        ),
    )

    result = render_repo_map_section((candidate,), reader=reader)

    assert result.section is not None
    assert "  L1 def symbol_1():" in result.section.content
    assert "  L3 def symbol_3():" in result.section.content
    assert "symbol_4" not in result.section.content
    assert reader.calls == [
        ("pkg/service.py", 1, 1, 1),
        ("pkg/service.py", 2, 2, 1),
        ("pkg/service.py", 3, 3, 1),
    ]


def test_render_repo_map_section_keeps_other_candidates_when_read_fails() -> None:
    from agents.repo_symbols import SymbolTag

    RepoMapCandidate, _, _, _, render_repo_map_section = _load_repo_map_api()

    class FakeReader:
        def read_lines(
            self,
            path: str,
            *,
            start_line: int,
            end_line: int | None = None,
            max_lines: int = 120,
        ) -> dict[str, object]:
            if path == "pkg/broken.py":
                raise OSError("cannot read")
            return {
                "path": path,
                "start_line": start_line,
                "end_line": start_line,
                "lines": [{"path": path, "line": start_line, "text": "class Service:"}],
                "truncated": False,
            }

    candidates = (
        RepoMapCandidate(
            path="pkg/broken.py",
            score=100,
            reasons=("symbol definition: Broken",),
            symbols=(SymbolTag(path="pkg/broken.py", name="Broken", kind="class", line=1),),
        ),
        RepoMapCandidate(
            path="pkg/service.py",
            score=90,
            reasons=("symbol definition: Service",),
            symbols=(SymbolTag(path="pkg/service.py", name="Service", kind="class", line=2),),
        ),
    )

    result = render_repo_map_section(candidates, reader=FakeReader())

    assert result.section is not None
    assert "pkg/broken.py score=100" in result.section.content
    assert "read failed: cannot read" in result.section.content
    assert "pkg/service.py score=90" in result.section.content
    assert "  L2 class Service:" in result.section.content


def test_render_repo_map_section_returns_empty_when_budget_is_zero() -> None:
    RepoMapCandidate, _, _, _, render_repo_map_section = _load_repo_map_api()

    result = render_repo_map_section(
        (RepoMapCandidate(path="pkg/service.py", score=100, reasons=()),),
        reader=object(),
        max_chars=0,
    )

    assert result.section is None
    assert result.included_paths == ()
    assert result.truncated is False


def test_render_repo_map_section_exact_fit_is_not_truncated() -> None:
    RepoMapCandidate, _, _, _, render_repo_map_section = _load_repo_map_api()
    candidate = RepoMapCandidate(
        path="pkg/service.py",
        score=100,
        reasons=("symbol definition: Service",),
    )
    expected = "pkg/service.py score=100\nreasons: symbol definition: Service"

    first = render_repo_map_section((candidate,), reader=object(), max_chars=len(expected))
    second = render_repo_map_section((candidate,), reader=object(), max_chars=len(expected))

    assert first.section is not None
    assert first.section.content == expected
    assert first.truncated is False
    assert first.section.metadata["truncated"] is False
    assert first.section.content == second.section.content
    assert first.included_paths == second.included_paths


def test_render_repo_map_section_truncates_before_over_budget_candidate() -> None:
    RepoMapCandidate, _, _, _, render_repo_map_section = _load_repo_map_api()
    kept = RepoMapCandidate(path="a.py", score=100, reasons=())
    dropped = RepoMapCandidate(path="b.py", score=90, reasons=("x" * 80,))
    budget = len("a.py score=100\n... repo map truncated")

    result = render_repo_map_section((kept, dropped), reader=object(), max_chars=budget)

    assert result.section is not None
    assert len(result.section.content) <= budget
    assert result.section.content == "a.py score=100\n... repo map truncated"
    assert result.included_paths == ("a.py",)
    assert result.truncated is True
    assert result.section.metadata["included_paths"] == ("a.py",)
    assert result.section.metadata["truncated"] is True


def test_rank_to_render_keeps_symbol_candidate_under_budget_pressure() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.repo_symbols import SymbolTag
    from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory

    _, _, RepoMapSearchHit, rank_repo_map_candidates, render_repo_map_section = _load_repo_map_api()

    class FakeReader:
        def read_lines(
            self,
            path: str,
            *,
            start_line: int,
            end_line: int | None = None,
            max_lines: int = 120,
        ) -> dict[str, object]:
            return {
                "path": path,
                "start_line": start_line,
                "end_line": start_line,
                "lines": [{"path": path, "line": start_line, "text": "class Service:"}],
                "truncated": False,
            }

    selected_path = "pkg/selected.py"
    service_tag = SymbolTag(path="pkg/service.py", name="Service", kind="class", line=2)
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path=selected_path, kind="file"),
            WorkspaceFileEntry(path="pkg/service.py", kind="file"),
            WorkspaceFileEntry(path="pkg/helper.py", kind="file"),
            WorkspaceFileEntry(path="tests/helper.py", kind="file"),
        ),
    )

    candidates = rank_repo_map_candidates(
        (
            SymbolTag(path=selected_path, name="Selected", kind="class", line=1),
            service_tag,
        ),
        selected_paths=(selected_path,),
        mentions=(
            MentionCandidate(text="Selected", kind="symbol", confidence=0.7),
            MentionCandidate(text="Service", kind="symbol", confidence=0.7),
            MentionCandidate(text="helper.py", kind="filename", confidence=0.5),
        ),
        inventory=inventory,
        search_hits=(
            RepoMapSearchHit(
                path="pkg/service.py",
                line=8,
                query="Service",
                text="return Service()",
            ),
        ),
    )

    expected_service_block = (
        "pkg/service.py score=140\n"
        "reasons: symbol definition: Service, search hit: Service\n"
        "  L2 class Service:"
    )
    budget = len(f"{expected_service_block}\n... repo map truncated")
    result = render_repo_map_section(
        candidates,
        reader=FakeReader(),
        selected_paths=(selected_path,),
        max_chars=budget,
    )

    candidate_paths = tuple(candidate.path for candidate in candidates)
    assert selected_path not in candidate_paths
    assert {"pkg/helper.py", "tests/helper.py"}.issubset(candidate_paths)
    assert candidates[0].path == "pkg/service.py"
    assert result.section is not None
    assert selected_path not in result.included_paths
    assert result.included_paths == ("pkg/service.py",)
    assert result.section.metadata["included_paths"] == ("pkg/service.py",)
    assert result.truncated is True
    assert result.section.content == f"{expected_service_block}\n... repo map truncated"


def test_render_repo_map_section_keeps_truncation_state_consistent() -> None:
    RepoMapCandidate, _, _, _, render_repo_map_section = _load_repo_map_api()
    kept = RepoMapCandidate(path="a.py", score=100, reasons=())
    dropped = RepoMapCandidate(path="b.py", score=90, reasons=("x" * 80,))
    budget = len("a.py score=100\n... repo map truncated")

    result = render_repo_map_section((kept, dropped), reader=object(), max_chars=budget)

    assert result.section is not None
    assert result.truncated is True
    assert result.section.metadata["truncated"] == result.truncated
    assert result.section.metadata["included_paths"] == result.included_paths
    assert result.section.content.endswith("... repo map truncated")
    assert len(result.section.content) <= budget
