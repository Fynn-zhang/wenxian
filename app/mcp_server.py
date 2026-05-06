from __future__ import annotations

import json
import sys
from typing import Any, Callable

from app.db import get_db, now_iso, row_to_dict
from app.exporter import export_markdown


JsonDict = dict[str, Any]


def list_papers() -> list[JsonDict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   COUNT(DISTINCT par.id) AS paragraph_count,
                   COUNT(DISTINCT CASE WHEN par.translation_status = 'confirmed' THEN par.id END)
                       AS confirmed_translation_count,
                   COUNT(DISTINCT CASE WHEN e.status = 'confirmed' THEN e.id END)
                       AS confirmed_explanation_count
            FROM papers p
            LEFT JOIN paragraphs par ON par.paper_id = p.id
            LEFT JOIN explanations e ON e.paper_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_paper(paper_id: int) -> JsonDict:
    with get_db() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise ValueError("Paper not found.")
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


def search_paragraphs(query: str, paper_id: int | None = None, limit: int = 20) -> list[JsonDict]:
    term = query.strip()
    if not term:
        raise ValueError("query is required.")
    limit = max(1, min(limit, 100))
    pattern = f"%{term}%"
    values: list[Any] = [pattern, pattern]
    paper_filter = ""
    if paper_id is not None:
        paper_filter = "AND par.paper_id = ?"
        values.append(paper_id)
    values.append(limit)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT par.*, p.title AS paper_title
            FROM paragraphs par
            JOIN papers p ON p.id = par.paper_id
            WHERE (par.source_text LIKE ? OR par.translation_text LIKE ?)
              {paper_filter}
            ORDER BY p.created_at DESC, par.paragraph_index
            LIMIT ?
            """,
            values,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def update_paper_notes(paper_id: int, summary: str | None = None, notes: str | None = None) -> JsonDict:
    updates: list[str] = []
    values: list[Any] = []
    if summary is not None:
        updates.append("summary = ?")
        values.append(summary)
    if notes is not None:
        updates.append("notes = ?")
        values.append(notes)
    if not updates:
        return {"ok": True, "updated": []}
    values.append(paper_id)
    with get_db() as conn:
        cur = conn.execute(f"UPDATE papers SET {', '.join(updates)} WHERE id = ?", values)
        if cur.rowcount == 0:
            raise ValueError("Paper not found.")
    return {"ok": True, "updated": [item.split(" ")[0] for item in updates]}


def confirm_explanation(
    explanation_id: int,
    explanation_text: str | None = None,
    uncertainty: str | None = None,
) -> JsonDict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM explanations WHERE id = ?", (explanation_id,)).fetchone()
        if not row:
            raise ValueError("Explanation not found.")
        conn.execute(
            """
            UPDATE explanations
            SET explanation_text = ?,
                uncertainty = ?,
                status = 'confirmed',
                updated_at = ?
            WHERE id = ?
            """,
            (
                explanation_text if explanation_text is not None else row["explanation_text"],
                uncertainty if uncertainty is not None else row["uncertainty"],
                now_iso(),
                explanation_id,
            ),
        )
    return {"ok": True, "explanation_id": explanation_id, "status": "confirmed"}


def export_writing_materials(paper_id: int) -> JsonDict:
    path = export_markdown(paper_id)
    return {"path": str(path)}


TOOL_SCHEMAS: dict[str, JsonDict] = {
    "list_papers": {
        "description": "List imported papers and local reading progress.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_paper": {
        "description": "Read paper metadata, paragraphs, translations, and explanations.",
        "inputSchema": {
            "type": "object",
            "properties": {"paper_id": {"type": "integer"}},
            "required": ["paper_id"],
            "additionalProperties": False,
        },
    },
    "search_paragraphs": {
        "description": "Search source text and confirmed or draft translations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "paper_id": {"type": "integer"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "update_paper_notes": {
        "description": "Update paper-level summary and writing notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "integer"},
                "summary": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["paper_id"],
            "additionalProperties": False,
        },
    },
    "confirm_explanation": {
        "description": "Mark an explanation as confirmed after human review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "explanation_id": {"type": "integer"},
                "explanation_text": {"type": "string"},
                "uncertainty": {"type": "string"},
            },
            "required": ["explanation_id"],
            "additionalProperties": False,
        },
    },
    "export_writing_materials": {
        "description": "Export confirmed reading material to Markdown.",
        "inputSchema": {
            "type": "object",
            "properties": {"paper_id": {"type": "integer"}},
            "required": ["paper_id"],
            "additionalProperties": False,
        },
    },
}

TOOLS: dict[str, Callable[..., Any]] = {
    "list_papers": list_papers,
    "get_paper": get_paper,
    "search_paragraphs": search_paragraphs,
    "update_paper_notes": update_paper_notes,
    "confirm_explanation": confirm_explanation,
    "export_writing_materials": export_writing_materials,
}


def _tool_result(value: Any) -> JsonDict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, indent=2),
            }
        ]
    }


def handle_request(request: JsonDict) -> JsonDict | None:
    method = request.get("method")
    request_id = request.get("id")
    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "paper-workbench", "version": "0.1.0"},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": name, **schema}
                    for name, schema in TOOL_SCHEMAS.items()
                ]
            }
        elif method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in TOOLS:
                raise ValueError(f"Unknown tool: {name}")
            result = _tool_result(TOOLS[name](**arguments))
        else:
            raise ValueError(f"Unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_request(json.loads(line))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
