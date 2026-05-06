let currentPaperId = null;
let currentPaper = null;
let currentDraftExplanationId = null;
let selectedParagraphId = null;

const statusLine = document.querySelector("#statusLine");
const paperList = document.querySelector("#paperList");
const paperTitleView = document.querySelector("#paperTitleView");
const paperMeta = document.querySelector("#paperMeta");
const readingRows = document.querySelector("#readingRows");
const summaryInput = document.querySelector("#summaryInput");
const notesInput = document.querySelector("#notesInput");
const explainBtn = document.querySelector("#explainBtn");
const translateBtn = document.querySelector("#translateBtn");
const exportBtn = document.querySelector("#exportBtn");
const saveSummaryBtn = document.querySelector("#saveSummaryBtn");
const explanationEditor = document.querySelector("#explanationEditor");
const selectedTextInput = document.querySelector("#selectedTextInput");
const explanationText = document.querySelector("#explanationText");
const uncertaintyText = document.querySelector("#uncertaintyText");

function setStatus(message) {
  statusLine.textContent = message;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // Keep the status text.
    }
    throw new Error(detail);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function statusLabel(status) {
  const labels = {
    pending: "待翻译",
    translated: "待确认",
    confirmed: "已确认",
    failed: "失败",
  };
  return labels[status] || status;
}

function paragraphById(paragraphId) {
  return currentPaper?.paragraphs.find((paragraph) => paragraph.id === paragraphId);
}

async function loadPapers() {
  const papers = await api("/api/papers");
  paperList.innerHTML = papers.length
    ? papers
        .map(
          (paper) => `
            <button class="paper-item ${paper.id === currentPaperId ? "active" : ""}" data-paper-id="${paper.id}">
              <strong>${escapeHtml(paper.title)}</strong>
              <span>${paper.paragraph_count || 0} 段 · ${paper.translated_count || 0} 已翻译</span>
            </button>
          `,
        )
        .join("")
    : '<p class="empty">还没有导入论文。</p>';
}

async function loadPaper(paperId) {
  currentPaperId = paperId;
  currentPaper = await api(`/api/papers/${paperId}`);
  renderPaper();
  await loadPapers();
}

function renderPaper() {
  const { paper, paragraphs, explanations } = currentPaper;
  const explanationsByParagraph = explanations.reduce((groups, explanation) => {
    const key = explanation.paragraph_id || "unbound";
    if (!groups[key]) {
      groups[key] = [];
    }
    groups[key].push(explanation);
    return groups;
  }, {});
  paperTitleView.textContent = paper.title;
  paperMeta.textContent = `${paper.original_filename} · ${paragraphs.length} 段 · ${paper.status}`;
  summaryInput.value = paper.summary || "";
  notesInput.value = paper.notes || "";
  translateBtn.disabled = false;
  exportBtn.disabled = false;
  saveSummaryBtn.disabled = false;

  readingRows.innerHTML = paragraphs
    .map((p) => {
      const rowExplanations = explanationsByParagraph[p.id] || [];
      const explanationHtml = rowExplanations.length
        ? rowExplanations
            .map(
              (e) => `
                <article class="explanation-item">
                  <div class="locator">${escapeHtml(e.status)} · ${escapeHtml(e.selected_text)}</div>
                  <div class="explanation-text">${escapeHtml(e.explanation_text)}</div>
                  ${
                    e.uncertainty
                      ? `<p class="tag">不确定性：${escapeHtml(e.uncertainty)}</p>`
                      : ""
                  }
                </article>
              `,
            )
            .join("")
        : '<p class="empty">选中本段术语或句子后生成解释。</p>';
      return `
        <article class="reading-row">
          <section class="reading-cell source-cell" data-paragraph-id="${p.id}">
            <div class="locator">段落 ${p.paragraph_index} · PDF第${p.page_index}页</div>
            <div class="source-text">${escapeHtml(p.source_text)}</div>
          </section>
          <section class="reading-cell translation-cell" data-paragraph-id="${p.id}">
            <div class="locator">
              段落 ${p.paragraph_index} · <span class="status ${p.translation_status}">${statusLabel(p.translation_status)}</span>
            </div>
            <div class="translation-box">
              <textarea data-translation-id="${p.id}">${escapeHtml(p.translation_text || "")}</textarea>
              <div class="translation-actions">
                <button class="save-translation" data-paragraph-id="${p.id}">确认/保存翻译</button>
                <button class="retranslate-translation secondary" data-paragraph-id="${p.id}" type="button">重新翻译本段</button>
                <button class="restore-translation secondary" data-paragraph-id="${p.id}" type="button">恢复已保存翻译</button>
              </div>
            </div>
          </section>
          <section class="reading-cell explanation-cell">
            <div class="locator">段落 ${p.paragraph_index} · 解释</div>
            ${explanationHtml}
          </section>
        </article>
      `;
    })
    .join("");
}

function getSelectionContext() {
  const selection = window.getSelection();
  const text = selection ? selection.toString().trim() : "";
  if (!text) {
    return null;
  }
  let node = selection.anchorNode;
  while (node && node.nodeType !== Node.ELEMENT_NODE) {
    node = node.parentElement;
  }
  const paragraph = node ? node.closest("[data-paragraph-id]") : null;
  if (!paragraph) {
    return null;
  }
  return {
    text,
    paragraphId: Number(paragraph.dataset.paragraphId),
  };
}

document.addEventListener("selectionchange", () => {
  const context = getSelectionContext();
  explainBtn.disabled = !context || !currentPaperId;
  if (context) {
    selectedParagraphId = context.paragraphId;
    selectedTextInput.value = context.text;
  }
});

paperList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-paper-id]");
  if (!button) return;
  await loadPaper(Number(button.dataset.paperId));
});

document.querySelector("#refreshBtn").addEventListener("click", loadPapers);

document.querySelector("#uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  setStatus("正在导入 PDF...");
  try {
    const result = await api("/api/papers/import", { method: "POST", body: data });
    form.reset();
    await loadPaper(result.paper_id);
    setStatus("导入完成。");
  } catch (error) {
    setStatus(`导入失败：${error.message}`);
  }
});

document.querySelector("#importExistingBtn").addEventListener("click", async () => {
  const path = document.querySelector("#existingPath").value.trim();
  if (!path) return;
  setStatus("正在导入现有 PDF...");
  try {
    const query = new URLSearchParams({ path });
    const result = await api(`/api/papers/import-existing?${query}`, { method: "POST" });
    await loadPaper(result.paper_id);
    setStatus("导入完成。");
  } catch (error) {
    setStatus(`导入失败：${error.message}`);
  }
});

translateBtn.addEventListener("click", async () => {
  if (!currentPaperId) return;
  translateBtn.disabled = true;
  setStatus("正在翻译下一批段落...");
  try {
    const result = await api(`/api/papers/${currentPaperId}/translate?limit=5`, { method: "POST" });
    await loadPaper(currentPaperId);
    setStatus(`翻译完成：${result.translated} 段，失败 ${result.failed} 段。`);
  } catch (error) {
    setStatus(`翻译失败：${error.message}`);
  } finally {
    translateBtn.disabled = false;
  }
});

readingRows.addEventListener("click", async (event) => {
  const restoreButton = event.target.closest(".restore-translation");
  if (restoreButton) {
    const paragraphId = Number(restoreButton.dataset.paragraphId);
    const textarea = readingRows.querySelector(`[data-translation-id="${paragraphId}"]`);
    const paragraph = paragraphById(paragraphId);
    if (!textarea || !paragraph) return;
    textarea.value = paragraph.translation_text || "";
    setStatus("已恢复为上次保存的翻译。");
    return;
  }

  const retranslateButton = event.target.closest(".retranslate-translation");
  if (retranslateButton) {
    const paragraphId = Number(retranslateButton.dataset.paragraphId);
    retranslateButton.disabled = true;
    setStatus("正在重新翻译本段...");
    try {
      await api(`/api/paragraphs/${paragraphId}/translate`, { method: "POST" });
      await loadPaper(currentPaperId);
      setStatus("本段已重新翻译，检查后可确认保存。");
    } catch (error) {
      setStatus(`重新翻译失败：${error.message}`);
    } finally {
      retranslateButton.disabled = false;
    }
    return;
  }

  const button = event.target.closest(".save-translation");
  if (!button) return;
  const paragraphId = Number(button.dataset.paragraphId);
  const textarea = readingRows.querySelector(`[data-translation-id="${paragraphId}"]`);
  if (!textarea.value.trim()) {
    setStatus("翻译内容为空，不能确认。请先点击“重新翻译本段”或手动填写。");
    return;
  }
  try {
    await api(`/api/paragraphs/${paragraphId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        translation_text: textarea.value,
        translation_status: "confirmed",
      }),
    });
    await loadPaper(currentPaperId);
    setStatus("翻译已保存并确认。");
  } catch (error) {
    setStatus(`保存失败：${error.message}`);
  }
});

saveSummaryBtn.addEventListener("click", async () => {
  if (!currentPaperId) return;
  try {
    await api(`/api/papers/${currentPaperId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ summary: summaryInput.value, notes: notesInput.value }),
    });
    await loadPaper(currentPaperId);
    setStatus("摘要已保存。");
  } catch (error) {
    setStatus(`保存摘要失败：${error.message}`);
  }
});

explainBtn.addEventListener("click", async () => {
  const context = getSelectionContext();
  if (!context) return;
  setStatus("正在生成解释草稿...");
  explainBtn.disabled = true;
  try {
    const result = await api("/api/explanations/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paragraph_id: context.paragraphId,
        selected_text: context.text,
      }),
    });
    currentDraftExplanationId = result.id;
    selectedParagraphId = context.paragraphId;
    selectedTextInput.value = context.text;
    explanationText.value = result.explanation || "";
    uncertaintyText.value = result.uncertainty || "";
    explanationEditor.classList.remove("hidden");
    setStatus("解释草稿已生成，确认后才会进入导出。");
  } catch (error) {
    setStatus(`解释失败：${error.message}`);
  } finally {
    explainBtn.disabled = false;
  }
});

document.querySelector("#confirmExplanationBtn").addEventListener("click", async () => {
  if (!currentDraftExplanationId) return;
  try {
    await api(`/api/explanations/${currentDraftExplanationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        explanation_text: explanationText.value,
        uncertainty: uncertaintyText.value,
        status: "confirmed",
      }),
    });
    explanationEditor.classList.add("hidden");
    currentDraftExplanationId = null;
    await loadPaper(currentPaperId);
    setStatus("解释已确认。");
  } catch (error) {
    setStatus(`确认失败：${error.message}`);
  }
});

document.querySelector("#discardExplanationBtn").addEventListener("click", async () => {
  if (!currentDraftExplanationId) {
    explanationEditor.classList.add("hidden");
    return;
  }
  try {
    await api(`/api/explanations/${currentDraftExplanationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        explanation_text: explanationText.value,
        uncertainty: uncertaintyText.value,
        status: "discarded",
      }),
    });
    explanationEditor.classList.add("hidden");
    currentDraftExplanationId = null;
    await loadPaper(currentPaperId);
    setStatus("解释已丢弃。");
  } catch (error) {
    setStatus(`丢弃失败：${error.message}`);
  }
});

exportBtn.addEventListener("click", async () => {
  if (!currentPaperId) return;
  try {
    const result = await api(`/api/papers/${currentPaperId}/export`, { method: "POST" });
    setStatus(`已导出：${result.path}`);
  } catch (error) {
    setStatus(`导出失败：${error.message}`);
  }
});

loadPapers().catch((error) => setStatus(`初始化失败：${error.message}`));
