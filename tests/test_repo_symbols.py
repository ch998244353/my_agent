from agents.workspace import Workspace


def test_symbol_contracts_expose_stable_fields() -> None:
    from agents.repo_symbols import SymbolExtractionDiagnostic, SymbolTag

    tag = SymbolTag(path="pkg/service.py", name="Service", kind="class", line=4)
    diagnostic = SymbolExtractionDiagnostic(path="pkg/broken.py", reason="SyntaxError")

    assert (tag.path, tag.name, tag.kind, tag.line, tag.role) == (
        "pkg/service.py",
        "Service",
        "class",
        4,
        "definition",
    )
    assert (diagnostic.path, diagnostic.reason) == ("pkg/broken.py", "SyntaxError")


def test_extract_workspace_symbol_tags_returns_python_definition_tags(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "service.py").write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "class Service:",
                "    def run(self):",
                "        return helper()",
            ]
        ),
        encoding="utf-8",
    )

    tags, diagnostics = extract_workspace_symbol_tags(
        Workspace(tmp_path),
        ["pkg/service.py"],
    )

    assert diagnostics == ()
    assert [(tag.path, tag.name, tag.kind, tag.line, tag.role) for tag in tags] == [
        ("pkg/service.py", "helper", "function", 1, "definition"),
        ("pkg/service.py", "Service", "class", 4, "definition"),
        ("pkg/service.py", "run", "method", 5, "definition"),
    ]


def test_extract_workspace_symbol_tags_reports_decode_error(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "binary.py").write_bytes(b"\xff\xfe\x00")

    tags, diagnostics = extract_workspace_symbol_tags(Workspace(tmp_path), ["binary.py"])

    assert tags == ()
    assert [(item.path, item.reason) for item in diagnostics] == [
        ("binary.py", "File is not UTF-8 text.")
    ]


def test_extract_workspace_symbol_tags_reports_syntax_error(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    tags, diagnostics = extract_workspace_symbol_tags(Workspace(tmp_path), ["broken.py"])

    assert tags == ()
    assert diagnostics[0].path == "broken.py"
    assert diagnostics[0].reason.startswith("SyntaxError:")


def test_extract_workspace_symbol_tags_reports_outside_workspace_path(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    outside_path = tmp_path.parent / "outside.py"
    outside_path.write_text("def outside():\n    pass\n", encoding="utf-8")

    tags, diagnostics = extract_workspace_symbol_tags(
        Workspace(tmp_path),
        [str(outside_path)],
    )

    assert tags == ()
    assert diagnostics[0].path == str(outside_path)
    assert diagnostics[0].reason == "outside workspace root"


def test_extract_workspace_symbol_tags_respects_max_files_order(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "a.py").write_text("def first():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def second():\n    pass\n", encoding="utf-8")

    tags, diagnostics = extract_workspace_symbol_tags(
        Workspace(tmp_path),
        ["a.py", "b.py"],
        max_files=1,
    )

    assert diagnostics == ()
    assert [(tag.path, tag.name) for tag in tags] == [("a.py", "first")]


def test_extract_workspace_symbol_tags_sorts_tags_stably(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "a.py").write_text(
        "class Alpha:\n    def run(self):\n        pass\n\ndef build():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("def beta():\n    pass\n", encoding="utf-8")

    tags, diagnostics = extract_workspace_symbol_tags(
        Workspace(tmp_path),
        ["b.py", "a.py"],
    )

    assert diagnostics == ()
    assert [(tag.path, tag.line, tag.kind, tag.name) for tag in tags] == [
        ("a.py", 1, "class", "Alpha"),
        ("a.py", 2, "method", "run"),
        ("a.py", 5, "function", "build"),
        ("b.py", 1, "function", "beta"),
    ]


def test_extract_workspace_symbol_tags_deduplicates_same_tag(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "service.py").write_text("def build():\n    pass\n", encoding="utf-8")

    tags, diagnostics = extract_workspace_symbol_tags(
        Workspace(tmp_path),
        ["service.py", "service.py"],
    )

    assert diagnostics == ()
    assert [(tag.path, tag.name, tag.kind, tag.line, tag.role) for tag in tags] == [
        ("service.py", "build", "function", 1, "definition")
    ]


def test_extract_workspace_symbol_tags_returns_empty_for_no_paths(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    tags, diagnostics = extract_workspace_symbol_tags(Workspace(tmp_path), [])

    assert tags == ()
    assert diagnostics == ()


def test_extract_workspace_symbol_tags_returns_javascript_definition_tags(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "ui.js").write_text(
        "\n".join(
            [
                "class Widget {",
                "}",
                "",
                "function buildWidget() {",
                "}",
                "",
                "export function renderWidget() {",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    tags, diagnostics = extract_workspace_symbol_tags(Workspace(tmp_path), ["ui.js"])

    assert diagnostics == ()
    assert [(tag.path, tag.name, tag.kind, tag.line, tag.role) for tag in tags] == [
        ("ui.js", "Widget", "class", 1, "definition"),
        ("ui.js", "buildWidget", "function", 4, "definition"),
        ("ui.js", "renderWidget", "function", 7, "definition"),
    ]


def test_extract_workspace_symbol_tags_returns_typescript_definition_tags(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "ui.ts").write_text(
        "\n".join(
            [
                "class Panel {",
                "}",
                "",
                "export function renderPanel() {",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    tags, diagnostics = extract_workspace_symbol_tags(Workspace(tmp_path), ["ui.ts"])

    assert diagnostics == ()
    assert [(tag.path, tag.name, tag.kind, tag.line, tag.role) for tag in tags] == [
        ("ui.ts", "Panel", "class", 1, "definition"),
        ("ui.ts", "renderPanel", "function", 4, "definition"),
    ]


def test_extract_workspace_symbol_tags_reports_unsupported_extension(tmp_path) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "notes.txt").write_text("plain notes\n", encoding="utf-8")

    tags, diagnostics = extract_workspace_symbol_tags(Workspace(tmp_path), ["notes.txt"])

    assert tags == ()
    assert [(item.path, item.reason) for item in diagnostics] == [
        ("notes.txt", "Unsupported file type: .txt")
    ]


def test_extract_workspace_symbol_tags_reports_unsupported_binary_without_decoding(
    tmp_path,
) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "image.png").write_bytes(b"\xff\xfe\x00")

    tags, diagnostics = extract_workspace_symbol_tags(Workspace(tmp_path), ["image.png"])

    assert tags == ()
    assert [(item.path, item.reason) for item in diagnostics] == [
        ("image.png", "Unsupported file type: .png")
    ]


def test_extract_workspace_symbol_tags_keeps_python_tags_with_unsupported_files(
    tmp_path,
) -> None:
    from agents.repo_symbols import extract_workspace_symbol_tags

    (tmp_path / "service.py").write_text("def build():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Notes\n", encoding="utf-8")

    tags, diagnostics = extract_workspace_symbol_tags(
        Workspace(tmp_path),
        ["README.md", "service.py"],
    )

    assert [(tag.path, tag.name, tag.kind, tag.line, tag.role) for tag in tags] == [
        ("service.py", "build", "function", 1, "definition")
    ]
    assert [(item.path, item.reason) for item in diagnostics] == [
        ("README.md", "Unsupported file type: .md")
    ]
