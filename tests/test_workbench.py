from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import fitz

from app import db, exporter, mcp_server, pdf_import
from app.config import BASE_DIR


def run_dir() -> Path:
    path = BASE_DIR / "data" / "test_runs" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_paths(monkeypatch, root: Path) -> None:
    data_dir = root / "data"
    papers_dir = root / "papers"
    exports_dir = root / "exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    papers_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db, "DATABASE_PATH", data_dir / "reading.db")
    monkeypatch.setattr(pdf_import, "PAPERS_DIR", papers_dir)
    monkeypatch.setattr(exporter, "EXPORTS_DIR", exports_dir)
    db.init_db()


def create_pdf(path: Path, text: str | None = None) -> None:
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_remove_page_notes_drops_footnotes_and_reference_snippets() -> None:
    text = (
        "Main body sentence about trehalose.\n"
        "More body text before the page-bottom notes.\n"
        "* To whom correspondence should be addressed.\n"
        "(1) Guo, N.; Puhlev, I. Reference text."
    )

    cleaned = pdf_import.remove_page_notes(text)

    assert "Main body sentence" in cleaned
    assert "correspondence" not in cleaned
    assert "Reference text" not in cleaned


def test_normalize_pdf_line_breaks_joins_soft_breaks_and_hyphenation() -> None:
    text = "Encapsulated Solute Leakage in\nAnhydrobiotic Preservation and glycero-\nphosphocholine vesicles"

    normalized = pdf_import.normalize_pdf_line_breaks(text)

    assert normalized == (
        "Encapsulated Solute Leakage in Anhydrobiotic Preservation "
        "and glycerophosphocholine vesicles"
    )


def test_split_paragraphs_merges_short_blocks() -> None:
    text = (
        "Long enough first paragraph with enough context.\n\n"
        "short\n\n"
        "Second paragraph is also long enough for reading."
    )

    assert pdf_import.split_paragraphs(text) == [
        "Long enough first paragraph with enough context. short",
        "Second paragraph is also long enough for reading.",
    ]


def test_empty_pdf_page_is_marked_for_manual_ocr() -> None:
    pdf_path = run_dir() / "blank.pdf"
    create_pdf(pdf_path)

    paragraphs, empty_pages = pdf_import.extract_pdf_paragraphs(pdf_path)

    assert empty_pages == 1
    assert paragraphs[0]["extraction_status"] == "needs_manual_ocr"
    assert "OCR" in paragraphs[0]["source_text"]


def test_import_pdf_uses_unique_stored_name(monkeypatch) -> None:
    root = run_dir()
    configure_paths(monkeypatch, root)
    source_a = root / "sample.pdf"
    source_b_dir = root / "other"
    source_b_dir.mkdir()
    source_b = source_b_dir / "sample.pdf"
    create_pdf(source_a, "First paper text with enough words for extraction.")
    create_pdf(source_b, "Second paper text with enough words for extraction.")

    first_id = pdf_import.import_pdf(source_a)
    second_id = pdf_import.import_pdf(source_b)

    with db.get_db() as conn:
        rows = conn.execute("SELECT id, stored_path FROM papers ORDER BY id").fetchall()
    assert [row["id"] for row in rows] == [first_id, second_id]
    assert Path(rows[0]["stored_path"]).name == "sample.pdf"
    assert Path(rows[1]["stored_path"]).name == "sample_2.pdf"


def test_export_markdown_only_includes_confirmed_explanations(monkeypatch) -> None:
    configure_paths(monkeypatch, run_dir())
    now = db.now_iso()
    with db.get_db() as conn:
        paper_id = conn.execute(
            """
            INSERT INTO papers (title, original_filename, stored_path, created_at, summary, notes)
            VALUES ('Reading Test', 'paper.pdf', 'papers/paper.pdf', ?, 'Summary', 'Check citation.')
            """,
            (now,),
        ).lastrowid
        paragraph_id = conn.execute(
            """
            INSERT INTO paragraphs (
                paper_id, page_index, paragraph_index, source_text,
                translation_text, translation_status, extraction_status, created_at, updated_at
            )
            VALUES (?, 3, 1, 'Source evidence', 'Confirmed translation', 'confirmed', 'ok', ?, ?)
            """,
            (paper_id, now, now),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO explanations (
                paper_id, paragraph_id, selected_text, explanation_text,
                uncertainty, status, created_at, updated_at
            )
            VALUES (?, ?, 'confirmed term', 'Confirmed explanation', '', 'confirmed', ?, ?)
            """,
            (paper_id, paragraph_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO explanations (
                paper_id, paragraph_id, selected_text, explanation_text,
                uncertainty, status, created_at, updated_at
            )
            VALUES (?, ?, 'draft term', 'Draft explanation', '', 'draft', ?, ?)
            """,
            (paper_id, paragraph_id, now, now),
        )

    output = exporter.export_markdown(paper_id)
    text = output.read_text(encoding="utf-8")

    assert "## 原文与中文翻译" in text
    assert "Summary" in text
    assert "Confirmed explanation" in text
    assert "Draft explanation" not in text


def test_mcp_tools_read_search_confirm_and_export(monkeypatch) -> None:
    configure_paths(monkeypatch, run_dir())
    now = db.now_iso()
    with db.get_db() as conn:
        paper_id = conn.execute(
            """
            INSERT INTO papers (title, original_filename, stored_path, created_at)
            VALUES ('MCP Test', 'paper.pdf', 'papers/paper.pdf', ?)
            """,
            (now,),
        ).lastrowid
        paragraph_id = conn.execute(
            """
            INSERT INTO paragraphs (
                paper_id, page_index, paragraph_index, source_text,
                translation_text, translation_status, extraction_status, created_at, updated_at
            )
            VALUES (?, 1, 1, 'alpha beta source', '中文 alpha', 'confirmed', 'ok', ?, ?)
            """,
            (paper_id, now, now),
        ).lastrowid
        explanation_id = conn.execute(
            """
            INSERT INTO explanations (
                paper_id, paragraph_id, selected_text, explanation_text,
                uncertainty, status, created_at, updated_at
            )
            VALUES (?, ?, 'alpha', 'Draft alpha explanation', '', 'draft', ?, ?)
            """,
            (paper_id, paragraph_id, now, now),
        ).lastrowid

    assert mcp_server.list_papers()[0]["title"] == "MCP Test"
    assert mcp_server.get_paper(paper_id)["paper"]["id"] == paper_id
    assert mcp_server.search_paragraphs("alpha", paper_id=paper_id)[0]["paragraph_index"] == 1
    assert mcp_server.update_paper_notes(paper_id, notes="Use in theory section.")["ok"]
    assert mcp_server.confirm_explanation(explanation_id)["status"] == "confirmed"

    exported = mcp_server.export_writing_materials(paper_id)
    assert Path(exported["path"]).exists()
