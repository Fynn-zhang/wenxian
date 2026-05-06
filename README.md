# 本地 SCI 论文精读工作台

一个本机运行的 SCI PDF 精读 Web 应用：导入可复制文本 PDF 后按段落自动翻译，阅读时并排显示原文、中文翻译和人工选择触发的术语/关键内容解释。

## 安装

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 中填入 `OPENAI_API_KEY`。如果不填写，应用仍可导入 PDF、编辑翻译和导出 Markdown，但不能自动翻译或生成解释。

## 运行

```powershell
python -m uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000

## 本地 MCP

项目内置一个轻量级 `paper-workbench` MCP stdio server，不依赖 Notion、Zotero 或云端文献库。它直接读取同一个 SQLite 数据库和 `papers/exports` 目录。

```powershell
python -m app.mcp_server
```

可用工具：

- `list_papers`：列出已导入论文和阅读进度。
- `get_paper`：读取论文、段落、翻译和解释。
- `search_paragraphs`：检索原文和译文。
- `update_paper_notes`：更新论文级摘要和写作备注。
- `confirm_explanation`：人工确认解释。
- `export_writing_materials`：导出 Markdown 写作素材。

## Codex Skills

本工作流配套 3 个本地 Codex skills，默认安装在 `C:\Users\Veuns\.codex\skills`：

- `literature-intake`：导入和检查 PDF。
- `paper-deep-reading`：按段落精读、翻译草稿、术语解释和不确定性标记。
- `writing-material-export`：导出已确认的论文写作素材。

新增或更新 skill 后，重启 Codex 以重新加载 skill 元数据。

## 重要约束

- 第一版只支持可复制文本 PDF；扫描件或无文本页会标记为需手动处理。
- 页码使用 PDF 页序，不冒充期刊印刷页码。
- AI 只基于给定原文生成翻译或解释；无法确定时必须说明不确定。
- 专业术语解释必须由用户选中文本后触发，未确认解释不会进入 Markdown 导出。
- 不批量删除任何文件或目录。
