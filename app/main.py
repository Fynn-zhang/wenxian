from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.ai import AIUnavailable, explain_selection, translate_paragraph
from app.config import BASE_DIR, PAPERS_DIR, ensure_directories
from app.db import get_db, init_db, now_iso, row_to_dict
from app.exporter import export_markdown
from app.pdf_import import import_pdf


app = FastAPI(title="SCI Reading Workbench")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


class TranslationUpdate(BaseModel):
    translation_text: str
    translation_status: str = "confirmed"


class PaperUpdate(BaseModel):
    title: str | None = None
    summary: str | None = None
    notes: str | None = None


class ExplainRequest(BaseModel):
    paragraph_id: int
    selected_text: str


class ExplanationUpdate(BaseModel):
    explanation_text: str
    uncertainty: str = ""
    status: str = "confirmed"


@app.on_event("startup")
def on_startup() -> None:
    ensure_directories()
    init_db()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (BASE_DIR / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/papers")
def list_papers() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   COUNT(par.id) AS paragraph_count,
                   SUM(CASE WHEN par.translation_status = 'translated' THEN 1 ELSE 0 END) AS translated_count
            FROM papers p
            LEFT JOIN paragraphs par ON par.paper_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.post("/api/papers/import")
async def upload_paper(file: UploadFile = File(...), title: str = Form("")) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    target = PAPERS_DIR / file.filename
    counter = 2
    while target.exists():
        target = PAPERS_DIR / f"{target.stem}_{counter}{target.suffix}"
        counter += 1
    content = await file.read()
    target.write_bytes(content)
    paper_id = import_pdf(target, title.strip() or None)
    return {"paper_id": paper_id}


@app.post("/api/papers/import-existing")
def import_existing(path: str, title: str | None = None) -> dict:
    pdf_path = Path(path)
    if not pdf_path.is_absolute():
        pdf_path = BASE_DIR / pdf_path
    try:
        paper_id = import_pdf(pdf_path, title)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"paper_id": paper_id}


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: int) -> dict:
    with get_db() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found.")
        paragraphs = conn.execute(
            "SELECT * FROM paragraphs WHERE paper_id = ? ORDER BY paragraph_index",
            (paper_id,),
        ).fetchall()
        explanations = conn.execute(
            "SELECT * FROM explanations WHERE paper_id = ? ORDER BY id DESC",
            (paper_id,),
        ).fetchall()
    return {
        "paper": row_to_dict(paper),
        "paragraphs": [row_to_dict(row) for row in paragraphs],
        "explanations": [row_to_dict(row) for row in explanations],
    }


@app.patch("/api/papers/{paper_id}")
def update_paper(paper_id: int, payload: PaperUpdate) -> dict:
    updates = []
    values = []
    for field in ("title", "summary", "notes"):
        value = getattr(payload, field)
        if value is not None:
            updates.append(f"{field} = ?")
            values.append(value)
    if not updates:
        return {"ok": True}
    values.append(paper_id)
    with get_db() as conn:
        conn.execute(f"UPDATE papers SET {', '.join(updates)} WHERE id = ?", values)
    return {"ok": True}


@app.post("/api/papers/{paper_id}/translate")
def translate_next_batch(paper_id: int, limit: int = 5) -> dict:
    limit = max(1, min(limit, 20))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM paragraphs
            WHERE paper_id = ?
              AND extraction_status = 'ok'
              AND translation_status IN ('pending', 'failed')
            ORDER BY paragraph_index
            LIMIT ?
            """,
            (paper_id, limit),
        ).fetchall()
    translated = 0
    failed = 0
    errors: list[str] = []
    for row in rows:
        item = row_to_dict(row)
        try:
            result = translate_paragraph(item["source_text"])
            status = "translated"
            text = result.get("translation", "")
        except AIUnavailable as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            failed += 1
            errors.append(f"段落 {item['paragraph_index']}: {exc}")
            with get_db() as conn:
                conn.execute(
                    """
                    UPDATE paragraphs
                    SET translation_status = 'failed', updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), item["id"]),
                )
            continue
        with get_db() as conn:
            conn.execute(
                """
                UPDATE paragraphs
                SET translation_text = ?, translation_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (text, status, now_iso(), item["id"]),
            )
        translated += 1
    return {"translated": translated, "failed": failed, "errors": errors}


@app.patch("/api/paragraphs/{paragraph_id}")
def update_paragraph(paragraph_id: int, payload: TranslationUpdate) -> dict:
    if payload.translation_status not in {"pending", "translated", "confirmed", "failed"}:
        raise HTTPException(status_code=400, detail="Invalid translation status.")
    with get_db() as conn:
        conn.execute(
            """
            UPDATE paragraphs
            SET translation_text = ?, translation_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.translation_text, payload.translation_status, now_iso(), paragraph_id),
        )
    return {"ok": True}


@app.post("/api/explanations/draft")
def draft_explanation(payload: ExplainRequest) -> dict:
    selected = payload.selected_text.strip()
    if not selected:
        raise HTTPException(status_code=400, detail="Please select text to explain.")
    with get_db() as conn:
        paragraph = conn.execute(
            "SELECT * FROM paragraphs WHERE id = ?", (payload.paragraph_id,)
        ).fetchone()
        if not paragraph:
            raise HTTPException(status_code=404, detail="Paragraph not found.")
    try:
        result = explain_selection(paragraph["source_text"], selected)
    except AIUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    created_at = now_iso()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO explanations (
                paper_id, paragraph_id, selected_text, explanation_text,
                uncertainty, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                paragraph["paper_id"],
                payload.paragraph_id,
                selected,
                result.get("explanation", ""),
                result.get("uncertainty", ""),
                created_at,
                created_at,
            ),
        )
        explanation_id = int(cur.lastrowid)
    return {"id": explanation_id, **result}


@app.patch("/api/explanations/{explanation_id}")
def update_explanation(explanation_id: int, payload: ExplanationUpdate) -> dict:
    if payload.status not in {"draft", "confirmed", "discarded"}:
        raise HTTPException(status_code=400, detail="Invalid explanation status.")
    with get_db() as conn:
        conn.execute(
            """
            UPDATE explanations
            SET explanation_text = ?, uncertainty = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.explanation_text,
                payload.uncertainty,
                payload.status,
                now_iso(),
                explanation_id,
            ),
        )
    return {"ok": True}


@app.post("/api/papers/{paper_id}/export")
def export_paper(paper_id: int) -> dict:
    try:
        path = export_markdown(paper_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"path": str(path)}


@app.get("/api/papers/{paper_id}/export-file")
def download_export(paper_id: int) -> FileResponse:
    path = export_markdown(paper_id)
    return FileResponse(path, filename=path.name, media_type="text/markdown")
