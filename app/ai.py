from __future__ import annotations

import json

from openai import OpenAI

from app.config import OPENAI_API_KEY, OPENAI_MODEL
from app.pdf_import import OCR_PLACEHOLDER


class AIUnavailable(RuntimeError):
    pass


TRANSLATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "translation": {"type": "string"},
        "uncertainty": {"type": "string"},
    },
    "required": ["translation", "uncertainty"],
}

EXPLANATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "explanation": {"type": "string"},
        "uncertainty": {"type": "string"},
    },
    "required": ["explanation", "uncertainty"],
}


def _client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise AIUnavailable("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _json_response(schema: dict, prompt: str) -> dict:
    response = _client().responses.create(
        model=OPENAI_MODEL,
        instructions=(
            "你是 SCI 论文精读助手。只能依据用户提供的原文回答；"
            "不得编造文献、页码、数据、实验结果、作者观点或外部事实。"
            "如果原文不足以判断，必须在 uncertainty 中说明。"
        ),
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "sci_reading_response",
                "strict": True,
                "schema": schema,
            }
        },
    )
    return json.loads(response.output_text)


def translate_paragraph(source_text: str) -> dict:
    if source_text.startswith(OCR_PLACEHOLDER[:12]):
        return {"translation": "", "uncertainty": "该段无可复制正文，未翻译。"}
    return _json_response(
        TRANSLATION_SCHEMA,
        (
            "请将以下 SCI 论文正文段落翻译成准确、自然的中文。"
            "保留必要英文术语；不要添加原文没有的信息。\n\n"
            f"原文：\n{source_text}"
        ),
    )


def explain_selection(source_text: str, selected_text: str) -> dict:
    return _json_response(
        EXPLANATION_SCHEMA,
        (
            "请解释用户从 SCI 论文正文段落中选中的术语、句子或方法。"
            "解释要面向中文读者，说明它在该段上下文中的含义。"
            "不要扩展到原文没有支持的结论。\n\n"
            f"完整段落：\n{source_text}\n\n"
            f"用户选中内容：\n{selected_text}"
        ),
    )
