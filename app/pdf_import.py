from __future__ import annotations

import re
import shutil
from pathlib import Path

import fitz

from app.config import PAPERS_DIR
from app.db import get_db, now_iso, safe_filename


MIN_PARAGRAPH_CHARS = 35
OCR_PLACEHOLDER = "[该 PDF 页未检测到可复制正文，需手动处理或 OCR。]"
FOOTNOTE_START_PATTERNS = (
    re.compile(r"^\*\s+To whom correspondence", re.IGNORECASE),
    re.compile(r"^[†‡]\s+"),
)
REFERENCE_START_PATTERN = re.compile(r"^\(\d+\)\s+\S")


def remove_page_notes(text: str) -> str:
    """Drop page-bottom footnotes and reference snippets from PDF text extraction."""
    kept: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            kept.append(raw_line)
            continue
        if any(pattern.match(line) for pattern in FOOTNOTE_START_PATTERNS):
            break
        if REFERENCE_START_PATTERN.match(line):
            break
        kept.append(raw_line)
    return "\n".join(kept).strip()


def split_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
    paragraphs: list[str] = []
    buffer = ""
    for block in blocks:
        compact = re.sub(r"[ \t]+", " ", block)
        if len(compact) < MIN_PARAGRAPH_CHARS and buffer:
            buffer = f"{buffer} {compact}".strip()
            continue
        if buffer:
            paragraphs.append(buffer)
        buffer = compact
    if buffer:
        paragraphs.append(buffer)
    if not paragraphs and normalized.strip():
        paragraphs = [re.sub(r"[ \t]+", " ", normalized.strip())]
    return paragraphs


def extract_pdf_paragraphs(pdf_path: Path) -> tuple[list[dict], int]:
    doc = fitz.open(pdf_path)
    extracted: list[dict] = []
    empty_pages = 0
    paragraph_index = 0
    try:
        for page_number, page in enumerate(doc, start=1):
            text = remove_page_notes(page.get_text("text").strip())
            if not text:
                empty_pages += 1
                paragraph_index += 1
                extracted.append(
                    {
                        "page_index": page_number,
                        "paragraph_index": paragraph_index,
                        "source_text": OCR_PLACEHOLDER,
                        "extraction_status": "needs_manual_ocr",
                    }
                )
                continue
            for paragraph in split_paragraphs(text):
                paragraph_index += 1
                extracted.append(
                    {
                        "page_index": page_number,
                        "paragraph_index": paragraph_index,
                        "source_text": paragraph,
                        "extraction_status": "ok",
                    }
                )
    finally:
        doc.close()
    return extracted, empty_pages


def _insert_paragraphs(paper_id: int, paragraphs: list[dict], timestamp: str) -> None:
    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO paragraphs (
                paper_id, page_index, paragraph_index, source_text,
                extraction_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    paper_id,
                    item["page_index"],
                    item["paragraph_index"],
                    item["source_text"],
                    item["extraction_status"],
                    timestamp,
                    timestamp,
                )
                for item in paragraphs
            ],
        )


def import_pdf(pdf_path: Path, title: str | None = None) -> int:
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported.")
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = safe_filename(pdf_path.name)
    destination = PAPERS_DIR / stored_name
    if destination.exists() and destination.resolve() != pdf_path.resolve():
        stem = destination.stem
        suffix = destination.suffix
        counter = 2
        while destination.exists():
            destination = PAPERS_DIR / f"{stem}_{counter}{suffix}"
            counter += 1
    if destination.resolve() != pdf_path.resolve():
        shutil.copy2(pdf_path, destination)

    paragraphs, empty_pages = extract_pdf_paragraphs(destination)
    status = "needs_manual_ocr" if paragraphs and empty_pages == len(paragraphs) else "imported"
    created_at = now_iso()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO papers (title, original_filename, stored_path, created_at, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title or pdf_path.stem, pdf_path.name, str(destination), created_at, status),
        )
        paper_id = int(cur.lastrowid)
    _insert_paragraphs(paper_id, paragraphs, created_at)
    return paper_id


def refresh_paper_paragraphs(paper_id: int) -> int:
    """Re-extract stored PDF text for a paper and replace app-generated paragraphs."""
    with get_db() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise ValueError("Paper not found.")
        stored_path = Path(paper["stored_path"])
        confirmed = conn.execute(
            """
            SELECT COUNT(*) FROM paragraphs
            WHERE paper_id = ?
              AND (translation_status = 'confirmed' OR translation_text != '')
            """,
            (paper_id,),
        ).fetchone()[0]
        explanations = conn.execute(
            "SELECT COUNT(*) FROM explanations WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()[0]
        if confirmed or explanations:
            raise ValueError("Paper has reviewed translations or explanations; refresh manually.")

    paragraphs, empty_pages = extract_pdf_paragraphs(stored_path)
    status = "needs_manual_ocr" if paragraphs and empty_pages == len(paragraphs) else "imported"
    timestamp = now_iso()
    with get_db() as conn:
        conn.execute("DELETE FROM paragraphs WHERE paper_id = ?", (paper_id,))
        conn.execute("UPDATE papers SET status = ? WHERE id = ?", (status, paper_id))
    _insert_paragraphs(paper_id, paragraphs, timestamp)
    return len(paragraphs)
