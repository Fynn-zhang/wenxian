from __future__ import annotations

import re
from pathlib import Path

from app.config import EXPORTS_DIR
from app.db import get_db, row_to_dict


def _md_escape(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _slug(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", text).strip("_")
    return value[:80] or "paper"


def export_markdown(paper_id: int) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise ValueError("Paper not found.")
        paragraphs = conn.execute(
            "SELECT * FROM paragraphs WHERE paper_id = ? ORDER BY paragraph_index",
            (paper_id,),
        ).fetchall()
        explanations = conn.execute(
            """
            SELECT e.*, p.paragraph_index, p.page_index
            FROM explanations e
            LEFT JOIN paragraphs p ON p.id = e.paragraph_id
            WHERE e.paper_id = ? AND e.status = 'confirmed'
            ORDER BY COALESCE(p.paragraph_index, 999999), e.id
            """,
            (paper_id,),
        ).fetchall()

    paper_dict = row_to_dict(paper)
    confirmed_translations = [
        row_to_dict(row)
        for row in paragraphs
        if row["translation_status"] == "confirmed" and row["translation_text"].strip()
    ]
    lines = [
        f"# {paper_dict['title']}",
        "",
        "## 元数据",
        "",
        f"- 原始文件：{paper_dict['original_filename']}",
        f"- PDF 路径：{paper_dict['stored_path']}",
        f"- 导入时间：{paper_dict['created_at']}",
        f"- 阅读状态：{paper_dict['status']}",
        "",
        "## 论文级摘要",
        "",
        _md_escape(paper_dict["summary"]) or "（待补充）",
        "",
        "## 写作备注与待核查点",
        "",
        _md_escape(paper_dict["notes"]) or "（待补充）",
        "",
        "## 可引用原文证据",
        "",
    ]
    if confirmed_translations:
        for item in confirmed_translations:
            lines.extend(
                [
                    f"### 段落 {item['paragraph_index']}（PDF第{item['page_index']}页）",
                    "",
                    _md_escape(item["source_text"]),
                    "",
                    f"中文理解：{_md_escape(item['translation_text'])}",
                    "",
                ]
            )
    else:
        lines.extend(["（暂无已确认翻译段落）", ""])

    lines.extend(["## 确认的术语与关键内容解释", ""])
    if explanations:
        for row in explanations:
            item = row_to_dict(row)
            locator = f"PDF第{item['page_index']}页" if item["page_index"] else "未绑定段落"
            lines.extend(
                [
                    f"### {item['selected_text']}",
                    "",
                    f"- 位置：{locator}",
                    f"- 解释：{_md_escape(item['explanation_text'])}",
                ]
            )
            if item["uncertainty"]:
                lines.append(f"- 不确定性：{_md_escape(item['uncertainty'])}")
            lines.append("")
    else:
        lines.extend(["（暂无已确认解释）", ""])

    lines.extend(["## 原文与中文翻译", ""])
    for row in paragraphs:
        item = row_to_dict(row)
        lines.extend(
            [
                f"### 段落 {item['paragraph_index']}（PDF第{item['page_index']}页）",
                "",
                "**原文**",
                "",
                _md_escape(item["source_text"]),
                "",
                "**中文翻译**",
                "",
                _md_escape(item["translation_text"]) or "（待翻译）",
                "",
            ]
        )

    output = EXPORTS_DIR / f"{paper_id}_{_slug(paper_dict['title'])}.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
