import app.db as db
import app.exporter as exporter
from app.config import BASE_DIR
from app.db import get_db, init_db, now_iso
from app.exporter import export_markdown


def test_export_only_includes_confirmed_explanations(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", BASE_DIR / "data" / "test_reading.db")
    monkeypatch.setattr(exporter, "EXPORTS_DIR", BASE_DIR / "exports")
    init_db()
    stamp = now_iso()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO papers (title, original_filename, stored_path, created_at, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Test Export Paper", "test.pdf", "papers/test.pdf", stamp, "A short summary."),
        )
        paper_id = int(cur.lastrowid)
        cur = conn.execute(
            """
            INSERT INTO paragraphs (
                paper_id, page_index, paragraph_index, source_text,
                translation_text, translation_status, extraction_status,
                created_at, updated_at
            )
            VALUES (?, 1, 1, ?, ?, 'confirmed', 'ok', ?, ?)
            """,
            (paper_id, "Original evidence text.", "中文翻译。", stamp, stamp),
        )
        paragraph_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO explanations (
                paper_id, paragraph_id, selected_text, explanation_text,
                uncertainty, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, '', 'confirmed', ?, ?)
            """,
            (paper_id, paragraph_id, "term", "confirmed explanation", stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO explanations (
                paper_id, paragraph_id, selected_text, explanation_text,
                uncertainty, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, '', 'draft', ?, ?)
            """,
            (paper_id, paragraph_id, "draft term", "draft explanation", stamp, stamp),
        )

    path = export_markdown(paper_id)
    content = path.read_text(encoding="utf-8")

    assert "confirmed explanation" in content
    assert "draft explanation" not in content
    assert "PDF第1页" in content
