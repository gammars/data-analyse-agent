const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file-input");
const uploadDatasetButton = document.querySelector("#upload-dataset-button");
const appendTableForm = document.querySelector("#append-table-form");
const appendFileInput = document.querySelector("#append-file-input");
const appendDatasetButton = document.querySelector("#append-dataset-button");
const statusBox = document.querySelector("#status");
const summary = document.querySelector("#summary");
const columnsBody = document.querySelector("#columns-body");
const tableList = document.querySelector("#table-list");
const chatForm = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question-input");
const chatLog = document.querySelector("#chat-log");
const datasetSelect = document.querySelector("#dataset-select");
const renameDatasetButton = document.querySelector("#rename-dataset-button");
const deleteDatasetButton = document.querySelector("#delete-dataset-button");
const conversationSelect = document.querySelector("#conversation-select");
const conversationList = document.querySelector("#conversation-list");
const newConversationButton = document.querySelector("#new-conversation-button");
const contextTokenLabel = document.querySelector("#context-token-label");
const contextPercentLabel = document.querySelector("#context-percent-label");
const contextBarFill = document.querySelector("#context-bar-fill");
const contextHint = document.querySelector("#context-hint");
const relationshipPanel = document.querySelector("#relationship-panel");
const analyzeRelationshipsButton = document.querySelector("#analyze-relationships-button");
const saveRelationshipsButton = document.querySelector("#save-relationships-button");
const relationshipStatus = document.querySelector("#relationship-status");
const relationshipEditor = document.querySelector("#relationship-editor");
const relationshipDialog = document.querySelector("#relationship-dialog");
const relationshipDialogStatus = document.querySelector("#relationship-dialog-status");
const relationshipAdvice = document.querySelector("#relationship-advice");
const refreshRelationshipAdviceButton = document.querySelector(
  "#refresh-relationship-advice-button",
);
const closeRelationshipDialogButton = document.querySelector(
  "#close-relationship-dialog-button",
);

let currentDatasetId = null;
let currentConversationId = null;
let datasets = [];
let conversations = [];
let pendingToolCards = new Map();
let thinkingMessage = null;
let relationshipSuggestions = null;
let relationshipConfigurationStatus = "confirmed";
let relationshipLoading = false;

function showStatus(message, isError = false) {
  statusBox.hidden = false;
  statusBox.textContent = message;
  statusBox.classList.toggle("error", isError);
}

function formatErrorDetail(detail, fallback) {
  if (!detail) {
    return fallback;
  }
  if (typeof detail === "string") {
    return detail;
  }
  return JSON.stringify(detail, null, 2);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(formatErrorDetail(data.detail, "请求失败"));
  }
  return data;
}

function renderDataset(data, resetChat = true) {
  currentDatasetId = data.dataset_id;
  datasetSelect.value = data.dataset_id;
  document.querySelector("#dataset-id").textContent = data.dataset_id;
  document.querySelector("#filename").textContent = data.filename;
  document.querySelector("#table-count").textContent = data.table_count || 1;
  document.querySelector("#row-count").textContent = data.row_count;
  document.querySelector("#column-count").textContent = data.column_count;
  document.querySelector("#schema").textContent = data.schema;

  columnsBody.replaceChildren(
    ...data.columns.map((column) => {
      const row = document.createElement("tr");
      const nameCell = document.createElement("td");
      const dtypeCell = document.createElement("td");
      const missingCell = document.createElement("td");

      nameCell.textContent = column.name;
      dtypeCell.textContent = column.dtype;
      missingCell.textContent = column.missing_count;

      row.append(nameCell, dtypeCell, missingCell);
      return row;
    }),
  );

  renderTableList(data.tables || []);
  relationshipSuggestions = null;
  relationshipEditor.replaceChildren();
  relationshipAdvice.replaceChildren();
  saveRelationshipsButton.disabled = true;
  relationshipConfigurationStatus = data.relationship_configuration?.status || "confirmed";
  setRelationshipGate(relationshipConfigurationStatus);

  summary.hidden = false;
  if (resetChat) {
    chatLog.hidden = true;
    chatLog.replaceChildren();
  }
  if (relationshipConfigurationStatus !== "confirmed") {
    window.setTimeout(() => {
      void openRelationshipConfiguration(false);
    }, 0);
  }
}

function optionLabel(columns) {
  return columns.map((column) => `“${column}”`).join(" + ");
}

function createChoice(type, name, checked, labelText, value, recommended = false) {
  const label = document.createElement("label");
  label.className = "relationship-choice";
  label.classList.toggle("recommended", recommended);
  const input = document.createElement("input");
  input.type = type;
  input.name = name;
  input.checked = checked;
  input.value = JSON.stringify(value);
  const text = document.createElement("span");
  text.textContent = recommended ? `${labelText} · AI 推荐` : labelText;
  label.append(input, text);
  return label;
}

function renderRelationshipSuggestions(data) {
  const currentByTable = new Map(data.current.map((item) => [item.table_name, item]));
  const foreignKeysByTable = new Map();
  for (const candidate of data.foreign_key_candidates) {
    const items = foreignKeysByTable.get(candidate.table_name) || [];
    items.push(candidate);
    foreignKeysByTable.set(candidate.table_name, items);
  }

  relationshipEditor.replaceChildren(
    ...data.tables.map((table) => {
      const current = currentByTable.get(table.table_name);
      const section = document.createElement("section");
      section.className = "relationship-table";
      const heading = document.createElement("h3");
      heading.textContent = table.table_name;

      const primaryTitle = document.createElement("h4");
      primaryTitle.textContent = "主键";
      const primaryChoices = document.createElement("div");
      primaryChoices.className = "relationship-choices";
      const recommendedPrimary = table.primary_key_candidates.find(
        (candidate) => candidate.llm_recommended,
      );
      primaryChoices.append(
        createChoice(
          "radio",
          `pk-${table.table_name}`,
          !current.primary_key.length && !recommendedPrimary,
          "不设置主键",
          [],
        ),
      );
      for (const candidate of table.primary_key_candidates) {
        const isCurrent = JSON.stringify(candidate.columns) === JSON.stringify(current.primary_key);
        const selected = isCurrent || (!current.primary_key.length && candidate.llm_recommended);
        primaryChoices.append(
          createChoice(
            "radio",
            `pk-${table.table_name}`,
            selected,
            `${optionLabel(candidate.columns)} · 可信度 ${Math.round(candidate.score * 100)}%`,
            candidate.columns,
            Boolean(candidate.llm_recommended),
          ),
        );
      }

      const foreignTitle = document.createElement("h4");
      foreignTitle.textContent = "外键";
      const foreignChoices = document.createElement("div");
      foreignChoices.className = "relationship-choices";
      const foreignCandidates = foreignKeysByTable.get(table.table_name) || [];
      for (const candidate of foreignCandidates) {
        foreignChoices.append(
          createChoice(
            "checkbox",
            `fk-${table.table_name}`,
            Boolean(candidate.current || candidate.llm_recommended),
            `${optionLabel(candidate.columns)} → ${candidate.referenced_table}.${optionLabel(candidate.referenced_columns)} · 匹配率 ${Math.round(candidate.match_ratio * 100)}%`,
            candidate,
            Boolean(candidate.llm_recommended),
          ),
        );
      }
      if (!foreignCandidates.length) {
        foreignChoices.textContent = "未发现满足完整性要求的候选外键。";
      }

      const indexTitle = document.createElement("h4");
      indexTitle.textContent = "索引";
      const indexChoices = document.createElement("div");
      indexChoices.className = "relationship-choices";
      for (const candidate of table.index_candidates) {
        indexChoices.append(
          createChoice(
            "checkbox",
            `index-${table.table_name}`,
            Boolean(candidate.current || candidate.llm_recommended),
            `${candidate.name} (${optionLabel(candidate.columns)})`,
            candidate,
            Boolean(candidate.llm_recommended),
          ),
        );
      }
      if (!table.index_candidates.length) {
        indexChoices.textContent = "暂无索引候选。";
      }

      section.append(
        heading,
        primaryTitle,
        primaryChoices,
        foreignTitle,
        foreignChoices,
        indexTitle,
        indexChoices,
      );
      return section;
    }),
  );
  renderRelationshipAdvice(data.llm_advice || {});
  relationshipDialogStatus.textContent = "请选择后确认";
  saveRelationshipsButton.disabled = false;
}

function renderRelationshipAdvice(advice) {
  const heading = document.createElement("strong");
  heading.textContent = advice.status === "success" ? "AI 建议" : "候选分析";
  const summaryText = document.createElement("p");
  summaryText.textContent = advice.summary || "请根据候选证据确认关系配置。";
  relationshipAdvice.replaceChildren(heading, summaryText);
  const details = document.createElement("div");
  details.className = "relationship-advice-details";
  for (const recommendation of advice.table_recommendations || []) {
    const line = document.createElement("p");
    const primary = recommendation.primary_key?.length
      ? `主键 ${optionLabel(recommendation.primary_key)}`
      : "不建议设置主键";
    const indexes = recommendation.indexes?.length
      ? `；索引 ${recommendation.indexes.join("、")}`
      : "；不额外建议索引";
    const reason = [recommendation.primary_key_reason, recommendation.index_reason]
      .filter(Boolean)
      .join("；");
    line.textContent = `${recommendation.table_name}：${primary}${indexes}${reason ? `。${reason}` : ""}`;
    details.append(line);
  }
  const foreignKeys = new Map(
    (relationshipSuggestions?.foreign_key_candidates || []).map((item) => [
      item.candidate_id,
      item,
    ]),
  );
  for (const recommendation of advice.foreign_key_recommendations || []) {
    const candidate = foreignKeys.get(recommendation.candidate_id);
    if (!candidate) {
      continue;
    }
    const line = document.createElement("p");
    line.textContent = `${candidate.table_name}.${candidate.columns.join("+")} → ${candidate.referenced_table}.${candidate.referenced_columns.join("+")}。${recommendation.reason}`;
    details.append(line);
  }
  if (details.childElementCount) {
    relationshipAdvice.append(details);
  }
  if (advice.warnings?.length) {
    const warningList = document.createElement("ul");
    for (const warning of advice.warnings.slice(0, 4)) {
      const item = document.createElement("li");
      item.textContent = warning;
      warningList.append(item);
    }
    relationshipAdvice.append(warningList);
  }
}

function setRelationshipGate(status) {
  const pending = status !== "confirmed";
  relationshipStatus.textContent = pending
    ? "待确认，完成后才能使用 Agent"
    : "已确认，可随时重新配置";
  questionInput.disabled = pending;
  const submitButton = chatForm.querySelector('button[type="submit"]');
  submitButton.disabled = pending;
  questionInput.placeholder = pending
    ? "请先完成关系配置"
    : "统计每个类别的销售额总和。";
  closeRelationshipDialogButton.hidden = pending;
}

async function openRelationshipConfiguration(refreshLLM = false) {
  if (!currentDatasetId || relationshipLoading) {
    return;
  }
  relationshipLoading = true;
  analyzeRelationshipsButton.disabled = true;
  refreshRelationshipAdviceButton.disabled = true;
  saveRelationshipsButton.disabled = true;
  relationshipDialogStatus.textContent = "正在分析候选并请求 AI 建议...";
  relationshipAdvice.textContent = "AI 正在根据字段结构、唯一率和关联匹配率生成建议。";
  relationshipEditor.replaceChildren();
  if (!relationshipDialog.open) {
    relationshipDialog.showModal();
  }
  try {
    const query = refreshLLM ? "?refresh_llm=true" : "";
    relationshipSuggestions = await fetchJson(
      `/api/datasets/${currentDatasetId}/relationships/suggestions${query}`,
    );
    renderRelationshipSuggestions(relationshipSuggestions);
  } catch (error) {
    relationshipDialogStatus.textContent = "建议生成失败";
    relationshipAdvice.textContent = error.message;
    showStatus(error.message, true);
  } finally {
    relationshipLoading = false;
    analyzeRelationshipsButton.disabled = false;
    refreshRelationshipAdviceButton.disabled = false;
  }
}

function collectRelationshipConfig() {
  return relationshipSuggestions.tables.map((table) => {
    const primaryInput = relationshipEditor.querySelector(
      `input[name="pk-${CSS.escape(table.table_name)}"]:checked`,
    );
    const foreignInputs = relationshipEditor.querySelectorAll(
      `input[name="fk-${CSS.escape(table.table_name)}"]:checked`,
    );
    const indexInputs = relationshipEditor.querySelectorAll(
      `input[name="index-${CSS.escape(table.table_name)}"]:checked`,
    );
    return {
      table_name: table.table_name,
      primary_key: primaryInput ? JSON.parse(primaryInput.value) : [],
      foreign_keys: Array.from(foreignInputs, (input) => {
        const value = JSON.parse(input.value);
        return {
          columns: value.columns,
          referenced_table: value.referenced_table,
          referenced_columns: value.referenced_columns,
          name: value.name,
        };
      }),
      indexes: Array.from(indexInputs, (input) => {
        const value = JSON.parse(input.value);
        return { name: value.name, columns: value.columns, unique: value.unique };
      }),
    };
  });
}

function renderTableList(tables) {
  tableList.replaceChildren(
    ...tables.map((table) => {
      const item = document.createElement("article");
      item.className = "table-item";

      const title = document.createElement("div");
      title.className = "table-item-title";
      title.textContent = table.table_name;

      const meta = document.createElement("div");
      meta.className = "table-item-meta";
      const source = table.sheet_name ? `${table.filename} / ${table.sheet_name}` : table.filename;
      meta.textContent = `${table.row_count} 行 · ${table.column_count} 列 · ${source}`;

      const sqlName = document.createElement("code");
      sqlName.textContent = table.sql_name || `"${table.table_name}"`;

      const actions = document.createElement("div");
      actions.className = "table-item-actions";

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "danger-button compact-button";
      deleteButton.textContent = "删除表";
      deleteButton.disabled = tables.length <= 1;
      deleteButton.title = tables.length <= 1 ? "数据集至少需要保留一张表" : `删除 ${table.table_name}`;
      deleteButton.addEventListener("click", async () => {
        await deleteDatasetTable(table.table_name);
      });

      actions.append(deleteButton);
      item.append(title, meta, sqlName, actions);
      return item;
    }),
  );
}

function getCurrentDataset() {
  return datasets.find((dataset) => dataset.dataset_id === currentDatasetId) || null;
}

function selectedFiles(input) {
  return Array.from(input.files || []);
}

async function refreshDatasets(selectedDatasetId = currentDatasetId) {
  const data = await fetchJson("/api/datasets");
  datasets = data.datasets || [];

  datasetSelect.replaceChildren(
    ...[
      new Option(datasets.length ? "选择数据集" : "暂无数据集", ""),
      ...datasets.map(
        (dataset) =>
          new Option(
            `${dataset.filename} (${dataset.table_count || 1}表)`,
            dataset.dataset_id,
          ),
      ),
    ],
  );

  if (selectedDatasetId && datasets.some((dataset) => dataset.dataset_id === selectedDatasetId)) {
    datasetSelect.value = selectedDatasetId;
  }
}

async function refreshConversations(selectedConversationId = currentConversationId) {
  const data = await fetchJson("/api/conversations");
  conversations = data.conversations || [];

  conversationSelect.replaceChildren(
    new Option("新对话", ""),
    ...conversations.map((conversation) => {
      const dataset = datasets.find((item) => item.dataset_id === conversation.dataset_id);
      const suffix = dataset ? ` · ${dataset.filename}` : "";
      return new Option(`${conversation.title}${suffix}`, conversation.conversation_id);
    }),
  );

  if (
    selectedConversationId &&
    conversations.some((conversation) => conversation.conversation_id === selectedConversationId)
  ) {
    conversationSelect.value = selectedConversationId;
  }
  const activeConversation = conversations.find(
    (conversation) => conversation.conversation_id === currentConversationId,
  );
  if (activeConversation) {
    updateContextMeter(activeConversation.context);
  }
  renderConversationList();
}

function renderConversationList() {
  conversationList.replaceChildren(
    ...conversations.map((conversation) => {
      const dataset = datasets.find((item) => item.dataset_id === conversation.dataset_id);
      const row = document.createElement("div");
      row.className = "conversation-row";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation-item";
      button.classList.toggle("active", conversation.conversation_id === currentConversationId);
      button.dataset.conversationId = conversation.conversation_id;

      const title = document.createElement("span");
      title.className = "conversation-title";
      title.textContent = conversation.title || "新对话";

      const meta = document.createElement("span");
      meta.className = "conversation-meta";
      const turnCount = conversation.turn_count ?? conversation.message_count ?? 0;
      meta.textContent = dataset
        ? `${dataset.filename} · ${turnCount}轮`
        : `${turnCount}轮`;

      button.append(title, meta);
      button.addEventListener("click", async () => {
        try {
          await loadConversation(conversation.conversation_id);
          showStatus("对话已恢复。");
        } catch (error) {
          showStatus(error.message, true);
        }
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "conversation-delete-button";
      deleteButton.textContent = "删除";
      deleteButton.title = `删除对话：${conversation.title || "新对话"}`;
      deleteButton.setAttribute("aria-label", deleteButton.title);
      deleteButton.addEventListener("click", async () => {
        await deleteConversation(conversation);
      });

      row.append(button, deleteButton);
      return row;
    }),
  );
}

async function deleteConversation(conversation) {
  const title = conversation.title || "新对话";
  if (!window.confirm(`确定删除对话「${title}」吗？此操作无法撤销。`)) {
    return;
  }

  const isActive = conversation.conversation_id === currentConversationId;
  try {
    await fetchJson(`/api/conversations/${conversation.conversation_id}`, {
      method: "DELETE",
    });

    if (isActive) {
      currentConversationId = null;
      conversationSelect.value = "";
      chatLog.hidden = true;
      chatLog.replaceChildren();
      pendingToolCards = new Map();
      thinkingMessage = null;
      updateContextMeter(null);
    }

    await refreshConversations(isActive ? "" : currentConversationId);
    showStatus("对话已删除。");
  } catch (error) {
    showStatus(error.message, true);
  }
}

function updateContextMeter(context) {
  if (!context) {
    contextTokenLabel.textContent = "0 / 0 tokens";
    contextPercentLabel.textContent = "0%";
    contextBarFill.style.width = "0%";
    contextBarFill.classList.remove("warning");
    contextHint.textContent = "暂无对话上下文";
    return;
  }

  const estimated = context.estimated_tokens || 0;
  const limit = context.context_limit_tokens || 0;
  const percent = limit ? Math.min(100, Math.round((estimated / limit) * 100)) : 0;
  const threshold = Math.round((context.compact_threshold || 0.8) * 100);
  const sourceLabel = context.token_source_label || "字符估算";
  const summarySourceLabel = context.summary_token_source_label || sourceLabel;

  contextTokenLabel.textContent = `${estimated} / ${limit} tokens`;
  contextPercentLabel.textContent = `${percent}%`;
  contextBarFill.style.width = `${percent}%`;
  contextBarFill.classList.toggle("warning", percent >= threshold);
  contextHint.textContent =
    `统计方式：${sourceLabel} · 压缩阈值 ${threshold}% · 摘要约 ${context.summary_tokens || 0} tokens（${summarySourceLabel}）`;
}

async function loadDataset(datasetId, clearConversation = true) {
  if (!datasetId) {
    currentDatasetId = null;
    summary.hidden = true;
    chatLog.hidden = true;
    chatLog.replaceChildren();
    return;
  }
  const data = await fetchJson(`/api/datasets/${datasetId}`);
  renderDataset(data, clearConversation);
  if (clearConversation) {
    currentConversationId = null;
    conversationSelect.value = "";
    updateContextMeter(null);
  }
}

async function loadConversation(conversationId) {
  if (!conversationId) {
    currentConversationId = null;
    chatLog.hidden = true;
    chatLog.replaceChildren();
    return;
  }

  const conversation = await fetchJson(`/api/conversations/${conversationId}`);
  currentConversationId = conversation.conversation_id;
  conversationSelect.value = conversation.conversation_id;
  await loadDataset(conversation.dataset_id, false);
  updateContextMeter(conversation.context);
  renderConversationMessages(conversation.messages || []);
  renderConversationList();
}

function renderConversationMessages(messages) {
  chatLog.replaceChildren();
  pendingToolCards = new Map();
  thinkingMessage = null;
  chatLog.hidden = messages.length === 0;

  for (const message of messages) {
    if (message.role === "user") {
      appendChatMessage("user", message.content || "");
    } else if (message.role === "assistant") {
      appendChatMessage("assistant", message.content || "");
    } else if (message.role === "tool") {
      if (message.type === "tool_start") {
        createToolCard(message.name, message.args || {});
      } else {
        completeToolCard(message.name, message.args || {}, parseToolResult(message.result || ""), {
          duration_ms: message.duration_ms,
          duration_label: message.duration_label,
          success: message.success,
        });
      }
    } else if (message.role === "chart") {
      appendChartMessage(message);
    } else if (message.role === "plan") {
      appendPlanMessage(message.plan || {});
    }
  }
}

uploadDatasetButton.addEventListener("click", () => {
  fileInput.value = "";
  fileInput.click();
});

fileInput.addEventListener("change", () => {
  if (selectedFiles(fileInput).length) {
    form.requestSubmit();
  }
});

appendDatasetButton.addEventListener("click", () => {
  if (!currentDatasetId) {
    showStatus("请先选择一个数据集。", true);
    return;
  }
  appendFileInput.value = "";
  appendFileInput.click();
});

appendFileInput.addEventListener("change", () => {
  if (selectedFiles(appendFileInput).length) {
    appendTableForm.requestSubmit();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const files = selectedFiles(fileInput);
  if (!files.length) {
    showStatus("请选择一个或多个 CSV / Excel 文件。", true);
    return;
  }

  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }

  const button = form.querySelector("button");
  button.disabled = true;
  showStatus(`正在上传并解析 ${files.length} 个文件...`);

  try {
    const data = await fetchJson("/api/upload", {
      method: "POST",
      body: formData,
    });

    currentConversationId = null;
    renderDataset(data);
    await refreshDatasets(data.dataset_id);
    await refreshConversations("");
    showStatus("上传成功，数据集结构已生成。");
  } catch (error) {
    summary.hidden = true;
    showStatus(error.message, true);
  } finally {
    button.disabled = false;
    fileInput.value = "";
  }
});

appendTableForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!currentDatasetId) {
    showStatus("请先选择一个数据集。", true);
    return;
  }

  const files = selectedFiles(appendFileInput);
  if (!files.length) {
    showStatus("请选择要追加的一个或多个数据文件。", true);
    return;
  }

  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }

  const button = appendTableForm.querySelector("button");
  button.disabled = true;
  showStatus(`正在追加 ${files.length} 个文件的数据表...`);

  try {
    const data = await fetchJson(`/api/datasets/${currentDatasetId}/tables`, {
      method: "POST",
      body: formData,
    });

    renderDataset(data);
    await refreshDatasets(data.dataset_id);
    showStatus("数据表已追加，schema 已更新。");
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    button.disabled = false;
    appendFileInput.value = "";
  }
});

renameDatasetButton.addEventListener("click", async () => {
  if (!currentDatasetId) {
    showStatus("请先选择一个数据集。", true);
    return;
  }

  const currentDataset = getCurrentDataset();
  const nextName = window.prompt("输入新的数据集名称", currentDataset?.filename || "");
  if (nextName === null) {
    return;
  }

  const cleanName = nextName.trim();
  if (!cleanName) {
    showStatus("数据集名称不能为空。", true);
    return;
  }

  try {
    const data = await fetchJson(`/api/datasets/${currentDatasetId}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: cleanName }),
    });
    renderDataset(data, false);
    await refreshDatasets(data.dataset_id);
    await refreshConversations(currentConversationId);
    showStatus("数据集已重命名。");
  } catch (error) {
    showStatus(error.message, true);
  }
});

deleteDatasetButton.addEventListener("click", async () => {
  if (!currentDatasetId) {
    showStatus("请先选择一个数据集。", true);
    return;
  }

  const currentDataset = getCurrentDataset();
  const label = currentDataset?.filename || currentDatasetId;
  if (!window.confirm(`确定删除数据集「${label}」吗？这个操作会删除本地保存的数据文件。`)) {
    return;
  }

  try {
    await fetchJson(`/api/datasets/${currentDatasetId}`, {
      method: "DELETE",
    });
    currentDatasetId = null;
    currentConversationId = null;
    summary.hidden = true;
    chatLog.hidden = true;
    chatLog.replaceChildren();
    updateContextMeter(null);
    await refreshDatasets("");
    await refreshConversations("");
    if (datasets.length) {
      await loadDataset(datasets[0].dataset_id);
    }
    showStatus("数据集已删除。");
  } catch (error) {
    showStatus(error.message, true);
  }
});

analyzeRelationshipsButton.addEventListener("click", async () => {
  if (!currentDatasetId) {
    showStatus("请先选择一个数据集。", true);
    return;
  }
  await openRelationshipConfiguration(false);
});

refreshRelationshipAdviceButton.addEventListener("click", async () => {
  await openRelationshipConfiguration(true);
});

relationshipDialog.addEventListener("cancel", (event) => {
  if (relationshipConfigurationStatus !== "confirmed") {
    event.preventDefault();
    relationshipDialogStatus.textContent = "必须确认一次关系配置后才能继续";
  }
});

closeRelationshipDialogButton.addEventListener("click", () => {
  if (relationshipConfigurationStatus === "confirmed") {
    relationshipDialog.close();
  }
});

saveRelationshipsButton.addEventListener("click", async () => {
  if (!currentDatasetId || !relationshipSuggestions) {
    return;
  }
  if (!window.confirm("确认使用当前选择重建 SQLite 数据库并保存主键、外键和索引吗？")) {
    return;
  }

  saveRelationshipsButton.disabled = true;
  analyzeRelationshipsButton.disabled = true;
  refreshRelationshipAdviceButton.disabled = true;
  relationshipDialogStatus.textContent = "正在验证完整性并重建 SQLite...";
  try {
    const result = await fetchJson(`/api/datasets/${currentDatasetId}/relationships`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirmed: true,
        tables: collectRelationshipConfig(),
      }),
    });
    const data = await fetchJson(`/api/datasets/${currentDatasetId}`);
    renderDataset(data, false);
    await refreshDatasets(data.dataset_id);
    relationshipDialog.close();
    relationshipStatus.textContent = "已确认，完整性验证通过";
    relationshipPanel.open = true;
    showStatus(
      `关系配置已保存：${result.validation.tables.length} 张表验证通过。`,
    );
  } catch (error) {
    relationshipDialogStatus.textContent = "配置保存失败";
    showStatus(error.message, true);
    saveRelationshipsButton.disabled = false;
  } finally {
    analyzeRelationshipsButton.disabled = false;
    refreshRelationshipAdviceButton.disabled = false;
  }
});

async function deleteDatasetTable(tableName) {
  if (!currentDatasetId) {
    showStatus("请先选择一个数据集。", true);
    return;
  }
  if (relationshipConfigurationStatus !== "confirmed") {
    showStatus("请先完成当前数据集的关系配置。", true);
    await openRelationshipConfiguration(false);
    return;
  }
  if (!window.confirm(`确定删除数据表「${tableName}」吗？`)) {
    return;
  }

  try {
    const data = await fetchJson(
      `/api/datasets/${currentDatasetId}/tables/${encodeURIComponent(tableName)}`,
      {
        method: "DELETE",
      },
    );
    renderDataset(data, false);
    await refreshDatasets(data.dataset_id);
    showStatus("数据表已删除，schema 已更新。");
  } catch (error) {
    showStatus(error.message, true);
  }
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!currentDatasetId) {
    showStatus("请先上传数据文件。", true);
    return;
  }
  if (relationshipConfigurationStatus !== "confirmed") {
    showStatus("请先完成当前数据集的关系配置。", true);
    await openRelationshipConfiguration(false);
    return;
  }

  const message = questionInput.value.trim();
  if (!message) {
    showStatus("请输入分析问题。", true);
    return;
  }

  const button = chatForm.querySelector("button");
  button.disabled = true;
  chatLog.hidden = false;
  appendChatMessage("user", message);
  questionInput.value = "";
  let assistantContent = null;
  showStatus("Agent 正在流式处理...");

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        dataset_id: currentConversationId ? undefined : currentDatasetId,
        conversation_id: currentConversationId,
        message,
      }),
    });

    if (!response.ok) {
      const data = await response.json();
      throw new Error(formatErrorDetail(data.detail, "查询失败"));
    }

    await readSseStream(response, (eventData) => {
      assistantContent = handleChatEvent(eventData, assistantContent);
    });
    await refreshConversations(currentConversationId);
    showStatus("查询完成。");
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
});

datasetSelect.addEventListener("change", async () => {
  try {
    await loadDataset(datasetSelect.value);
    showStatus("数据集已切换。");
  } catch (error) {
    showStatus(error.message, true);
  }
});

conversationSelect.addEventListener("change", async () => {
  try {
    await loadConversation(conversationSelect.value);
    showStatus(conversationSelect.value ? "对话已恢复。" : "已切换为新对话。");
  } catch (error) {
    showStatus(error.message, true);
  }
});

newConversationButton.addEventListener("click", async () => {
  currentConversationId = null;
  conversationSelect.value = "";
  chatLog.hidden = true;
  chatLog.replaceChildren();
  updateContextMeter(null);
  pendingToolCards = new Map();
  renderConversationList();
  showStatus("已准备新对话，发送第一条消息后会自动保存。");
});

function appendChatMessage(role, content) {
  if (role !== "thinking") {
    clearThinkingMessage();
  }

  const message = document.createElement("div");
  message.className = `chat-message ${role}`;

  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "你" : "Agent";

  const body = document.createElement("div");
  body.className = "message-body";
  if (role === "assistant") {
    body.classList.add("markdown-body");
    body.dataset.markdown = content;
    renderMarkdown(body, content);
  } else {
    body.textContent = content;
  }

  message.append(label, body);
  chatLog.append(message);
  chatLog.scrollTop = chatLog.scrollHeight;
  return body;
}

function setThinkingMessage(content) {
  chatLog.hidden = false;
  if (!thinkingMessage) {
    thinkingMessage = document.createElement("div");
    thinkingMessage.className = "chat-message thinking";

    const label = document.createElement("div");
    label.className = "message-label";
    label.textContent = "Agent";

    const body = document.createElement("div");
    body.className = "message-body thinking-body";

    const dot = document.createElement("span");
    dot.className = "thinking-dot";
    dot.setAttribute("aria-hidden", "true");

    const text = document.createElement("span");
    text.className = "thinking-text";

    body.append(dot, text);
    thinkingMessage.append(label, body);
    chatLog.append(thinkingMessage);
  }

  thinkingMessage.querySelector(".thinking-text").textContent = content;
  chatLog.scrollTop = chatLog.scrollHeight;
}

function clearThinkingMessage() {
  if (!thinkingMessage) {
    return;
  }
  thinkingMessage.remove();
  thinkingMessage = null;
}

function renderMarkdown(target, markdown) {
  target.innerHTML = markdownToHtml(markdown);
}

function markdownToHtml(markdown) {
  const lines = escapeHtml(normalizeMarkdown(markdown)).split(/\r?\n/);
  const blocks = [];
  let paragraph = [];
  let listItems = [];
  let tableLines = [];

  function flushParagraph() {
    if (!paragraph.length) {
      return;
    }
    blocks.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  }

  function flushList() {
    if (!listItems.length) {
      return;
    }
    blocks.push(`<ul>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
    listItems = [];
  }

  function flushTable() {
    if (!tableLines.length) {
      return;
    }
    const table = renderMarkdownTable(tableLines);
    if (table) {
      blocks.push(table);
    } else {
      paragraph.push(...tableLines);
    }
    tableLines = [];
  }

  for (const line of lines) {
    const trimmed = line.trim();

    if (!trimmed) {
      flushTable();
      flushList();
      flushParagraph();
      continue;
    }

    if (trimmed.includes("|")) {
      flushList();
      flushParagraph();
      tableLines.push(trimmed);
      continue;
    }

    flushTable();

    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      flushList();
      flushParagraph();
      const level = headingMatch[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
      continue;
    }

    const listMatch = trimmed.match(/^[-*]\s+(.+)$/);
    if (listMatch) {
      flushParagraph();
      listItems.push(listMatch[1]);
      continue;
    }

    const orderedListMatch = trimmed.match(/^\d+\.\s+(.+)$/);
    if (orderedListMatch) {
      flushParagraph();
      listItems.push(orderedListMatch[1]);
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  flushTable();
  flushList();
  flushParagraph();

  return blocks.join("");
}

function renderMarkdownTable(lines) {
  if (lines.length < 2 || !/^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$/.test(lines[1])) {
    return null;
  }

  const rows = lines
    .filter((_, index) => index !== 1)
    .map((line) =>
      line
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim()),
    );

  const [headers, ...bodyRows] = rows;
  const thead = `<thead><tr>${headers
    .map((header) => `<th>${renderInlineMarkdown(header)}</th>`)
    .join("")}</tr></thead>`;
  const tbody = `<tbody>${bodyRows
    .map(
      (row) =>
        `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`,
    )
    .join("")}</tbody>`;

  return `<div class="markdown-table-wrap"><table>${thead}${tbody}</table></div>`;
}

function renderInlineMarkdown(text) {
  return text
    .replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_match, alt, url) => {
      const safeUrl = sanitizeMarkdownUrl(url);
      if (!safeUrl) {
        return "";
      }
      return `<img class="markdown-image" src="${safeUrl}" alt="${alt}" loading="lazy" />`;
    })
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function normalizeMarkdown(markdown) {
  return markdown
    .split(/\r?\n/)
    .flatMap((line) => expandCompactTableLine(line))
    .join("\n");
}

function expandCompactTableLine(line) {
  const tableStart = line.indexOf("|");
  if (tableStart < 0) {
    return [line];
  }

  const prefix = line.slice(0, tableStart).trim();
  const tablePart = line.slice(tableStart).trim();
  if (!tablePart.startsWith("|") || !tablePart.includes("| |")) {
    return [line];
  }

  const rows = tablePart
    .replace(/\|\s+\|(?=\s*[:\-\w\u4e00-\u9fff\d])/g, "|\n|")
    .split("\n");
  return prefix ? [prefix, ...rows] : rows;
}

function sanitizeMarkdownUrl(url) {
  const value = url.trim().replace(/&amp;/g, "&");
  if (/^(https?:\/\/|\/charts\/|\/)/i.test(value)) {
    return value;
  }
  return "";
}

function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function toolCardKey(name, args) {
  return `${name}:${JSON.stringify(args || {})}`;
}

function createToolCard(name, args) {
  clearThinkingMessage();
  const key = toolCardKey(name, args);
  const card = document.createElement("details");
  card.className = "chat-message tool tool-card";
  card.open = true;

  const summary = document.createElement("summary");
  summary.className = "tool-summary";

  const title = document.createElement("span");
  title.className = "tool-title";
  title.textContent = `调用工具：${name}`;

  const status = document.createElement("span");
  status.className = "tool-status running";
  status.textContent = "运行中";

  summary.append(title, status);

  const body = document.createElement("div");
  body.className = "message-body tool-body";
  renderToolBody(body, name, args, null, { running: true });

  card.append(summary, body);
  chatLog.append(card);
  pendingToolCards.set(key, { card, body, status });
  chatLog.scrollTop = chatLog.scrollHeight;
  return card;
}

function completeToolCard(name, args, result, metadata = {}) {
  const key = toolCardKey(name, args);
  const existing = pendingToolCards.get(key);
  const cardInfo = existing || getLatestToolCardByName(name) || createDetachedToolCard(name, args);

  const durationLabel = metadata.duration_label || formatDuration(metadata.duration_ms);
  const success = metadata.success !== false;
  renderToolBody(cardInfo.body, name, args, result, metadata);
  cardInfo.status.textContent = `${success ? "完成" : "失败"}${durationLabel ? ` · ${durationLabel}` : ""}`;
  cardInfo.status.classList.remove("running");
  cardInfo.status.classList.add(success ? "done" : "error");
  cardInfo.card.open = false;
  pendingToolCards.delete(key);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function formatDuration(durationMs) {
  if (durationMs === undefined || durationMs === null || Number.isNaN(Number(durationMs))) {
    return "";
  }
  const safeDuration = Math.max(Number(durationMs), 0);
  if (safeDuration < 1000) {
    return `${Math.round(safeDuration)}ms`;
  }
  const seconds = safeDuration / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(2)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds - minutes * 60;
  return `${minutes}m ${remainingSeconds.toFixed(1)}s`;
}

function renderToolBody(target, name, args, result, metadata = {}) {
  target.replaceChildren();
  if (name === "python_analysis") {
    renderPythonAnalysisToolBody(target, args, result, metadata);
    return;
  }
  renderGenericToolBody(target, args, result, metadata);
}

function renderPythonAnalysisToolBody(target, args, result, metadata = {}) {
  const normalizedResult = result && typeof result === "object" ? result : {};
  const resultPayload =
    normalizedResult.result && typeof normalizedResult.result === "object"
      ? normalizedResult.result
      : {};
  const meta = document.createElement("div");
  meta.className = "tool-meta-grid";
  meta.append(
    createToolMetaItem("数据集", shortText(args.dataset_id || "-")),
    createToolMetaItem("输入行数", normalizedResult.input_rows ?? "-"),
    createToolMetaItem("最大行数", args.max_rows ?? "-"),
    createToolMetaItem("运行 ID", shortText(normalizedResult.run_id || "-")),
  );
  target.append(meta);

  const goal = args.analysis_goal || normalizedResult.analysis_goal;
  if (goal) {
    target.append(createTextSection("分析目标", goal));
  }
  if (args.sql) {
    target.append(createCodeSection("SQL 查询", args.sql, "sql", { open: true }));
  }
  if (args.python_code) {
    target.append(createCodeSection("Python 代码", args.python_code, "python", { open: false }));
  }

  if (metadata.running) {
    target.append(createNotice("info", "工具正在执行，结果生成后会显示摘要、图表和日志。"));
    return;
  }
  if (normalizedResult.error) {
    target.append(createErrorSection(normalizedResult.error));
    return;
  }

  const summary =
    resultPayload.summary ||
    resultPayload.conclusion ||
    normalizedResult.message ||
    "Python 沙箱分析已完成。";
  target.append(createTextSection("结果摘要", summary));

  if (resultPayload.metrics && typeof resultPayload.metrics === "object") {
    target.append(createMetricsSection(resultPayload.metrics));
  }

  const warnings = Array.isArray(normalizedResult.warnings) ? normalizedResult.warnings : [];
  for (const warning of warnings) {
    target.append(createNotice("warning", warning));
  }
  if (normalizedResult.stdout) {
    target.append(createCodeSection("stdout", normalizedResult.stdout, "text", { open: false }));
  }
  if (normalizedResult.stderr) {
    target.append(createCodeSection("stderr", normalizedResult.stderr, "text", { open: false }));
  }
}

function renderGenericToolBody(target, args, result, metadata = {}) {
  const restArgs = { ...(args || {}) };
  const sql = restArgs.sql;
  delete restArgs.sql;

  if (sql) {
    target.append(createCodeSection("SQL 查询", sql, "sql", { open: true }));
  }
  if (Object.keys(restArgs).length) {
    target.append(createJsonSection("工具参数", restArgs, { open: true }));
  }
  if (metadata.running) {
    target.append(createNotice("info", "工具正在执行。"));
    return;
  }
  target.append(createJsonSection("输出结果", result, { open: true }));
}

function createToolMetaItem(label, value) {
  const item = document.createElement("div");
  item.className = "tool-meta-item";
  const labelEl = document.createElement("span");
  labelEl.textContent = label;
  const valueEl = document.createElement("strong");
  valueEl.textContent = String(value);
  item.append(labelEl, valueEl);
  return item;
}

function createTextSection(title, text) {
  const section = document.createElement("section");
  section.className = "tool-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const paragraph = document.createElement("p");
  paragraph.textContent = String(text);
  section.append(heading, paragraph);
  return section;
}

function createMetricsSection(metrics) {
  const section = document.createElement("section");
  section.className = "tool-section";
  const heading = document.createElement("h4");
  heading.textContent = "核心指标";
  const grid = document.createElement("div");
  grid.className = "tool-metrics-grid";
  Object.entries(metrics).forEach(([key, value]) => {
    grid.append(createToolMetaItem(key, formatMetricValue(value)));
  });
  section.append(heading, grid);
  return section;
}

function createErrorSection(error) {
  const section = document.createElement("section");
  section.className = "tool-section tool-error-section";
  const heading = document.createElement("h4");
  heading.textContent = error.type || "工具执行失败";
  const message = document.createElement("p");
  message.textContent = error.message || String(error);
  section.append(heading, message);
  return section;
}

function createJsonSection(title, value, options = {}) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return createCodeSection(title, text, "json", options);
}

function createCodeSection(title, code, language, options = {}) {
  const details = document.createElement("details");
  details.className = "tool-section tool-code-section";
  details.open = options.open !== false;

  const summary = document.createElement("summary");
  summary.className = "tool-section-header";
  const label = document.createElement("span");
  label.textContent = title;
  const meta = document.createElement("span");
  meta.className = "tool-code-meta";
  meta.textContent = `${language.toUpperCase()} · ${countLines(code)} 行`;
  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.className = "tool-copy-button";
  copyButton.textContent = "复制";
  copyButton.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await copyTextToClipboard(String(code || ""));
    copyButton.textContent = "已复制";
    window.setTimeout(() => {
      copyButton.textContent = "复制";
    }, 1200);
  });
  summary.append(label, meta, copyButton);

  const pre = document.createElement("pre");
  pre.className = `tool-code language-${language}`;
  const codeEl = document.createElement("code");
  codeEl.innerHTML = highlightCode(String(code || ""), language);
  pre.append(codeEl);
  details.append(summary, pre);
  return details;
}

function createNotice(type, text) {
  const notice = document.createElement("div");
  notice.className = `tool-notice ${type}`;
  notice.textContent = String(text);
  return notice;
}

function highlightCode(code, language) {
  if (language === "python") {
    return highlightPython(code);
  }
  if (language === "sql") {
    return highlightSql(code);
  }
  if (language === "json") {
    return highlightJson(code);
  }
  return escapeHtml(code);
}

function highlightPython(code) {
  let html = escapeHtml(code);
  const placeholders = [];
  html = protectCodeSpans(
    html,
    /(?:&quot;&quot;&quot;[\s\S]*?&quot;&quot;&quot;|&#39;&#39;&#39;[\s\S]*?&#39;&#39;&#39;|&quot;(?:\\.|[^\\])*?&quot;|&#39;(?:\\.|[^\\])*?&#39;)/g,
    placeholders,
    "string",
  );
  html = protectCodeSpans(html, /(^|\s)(#.*)$/gm, placeholders, "comment");
  html = html.replace(
    /\b(False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b/g,
    '<span class="tok-keyword">$1</span>',
  );
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-number">$1</span>');
  return restoreCodeSpans(html, placeholders);
}

function highlightSql(code) {
  let html = escapeHtml(code);
  const placeholders = [];
  html = protectCodeSpans(html, /(?:&quot;(?:\\.|[^\\])*?&quot;|&#39;(?:\\.|[^\\])*?&#39;)/g, placeholders, "string");
  html = protectCodeSpans(html, /(^|\s)(--.*$)/gm, placeholders, "comment");
  html = html.replace(
    /\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|LIMIT|OFFSET|HAVING|WITH|AS|AND|OR|NOT|NULL|IS|IN|LIKE|CASE|WHEN|THEN|ELSE|END|COUNT|SUM|AVG|MIN|MAX|DISTINCT|UNION|ALL|DESC|ASC)\b/gi,
    '<span class="tok-keyword">$1</span>',
  );
  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-number">$1</span>');
  return restoreCodeSpans(html, placeholders);
}

function highlightJson(code) {
  return escapeHtml(code)
    .replace(/(&quot;[^&]*?&quot;)(?=\s*:)/g, '<span class="tok-property">$1</span>')
    .replace(/:\s*(&quot;.*?&quot;)/g, ': <span class="tok-string">$1</span>')
    .replace(/\b(true|false|null)\b/g, '<span class="tok-keyword">$1</span>')
    .replace(/\b(-?\d+(?:\.\d+)?)\b/g, '<span class="tok-number">$1</span>');
}

function protectCodeSpans(html, pattern, placeholders, tokenClass) {
  return html.replace(pattern, (...args) => {
    const match = args[0];
    const prefix = tokenClass === "comment" && typeof args[1] === "string" ? args[1] : "";
    const value = prefix && match.startsWith(prefix) ? match.slice(prefix.length) : match;
    const token = makePlaceholder(placeholders.length);
    placeholders.push(`<span class="tok-${tokenClass}">${value}</span>`);
    return `${prefix}${token}`;
  });
}

function restoreCodeSpans(html, placeholders) {
  return placeholders.reduce(
    (current, value, index) => current.replaceAll(makePlaceholder(index), value),
    html,
  );
}

function makePlaceholder(index) {
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  let remaining = index;
  let value = "";
  do {
    value = letters[remaining % letters.length] + value;
    remaining = Math.floor(remaining / letters.length) - 1;
  } while (remaining >= 0);
  return `§CODETOKEN${value}§`;
}

function countLines(code) {
  const text = String(code || "");
  return text ? text.split(/\r?\n/).length : 0;
}

function shortText(value) {
  const text = String(value || "");
  if (text.length <= 14) {
    return text;
  }
  return `${text.slice(0, 8)}...${text.slice(-4)}`;
}

function formatMetricValue(value) {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value : Number(value.toFixed(6));
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value);
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall back for non-secure origins or browser clipboard restrictions.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function getLatestToolCardByName(name) {
  const entries = Array.from(pendingToolCards.values()).reverse();
  return entries.find((item) => item.card.querySelector(".tool-title")?.textContent.includes(name));
}

function createDetachedToolCard(name, args) {
  createToolCard(name, args);
  return pendingToolCards.get(toolCardKey(name, args));
}

function appendToolMessage(title, data) {
  const message = document.createElement("div");
  message.className = "chat-message tool";

  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = title;

  const body = document.createElement("pre");
  body.className = "message-body tool-body";
  body.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);

  message.append(label, body);
  chatLog.append(message);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function appendPlanMessage(plan) {
  clearThinkingMessage();
  const message = document.createElement("div");
  message.className = "chat-message plan";

  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = "执行计划";

  const body = document.createElement("div");
  body.className = "message-body plan-body";

  const summary = document.createElement("div");
  summary.className = "plan-summary";
  summary.append(
    createPlanPill("模式", plan.mode || "-"),
    createPlanPill("主意图", plan.primary_intent || "-"),
    createPlanPill("步骤", `${Array.isArray(plan.steps) ? plan.steps.length : 0}/5`),
  );

  const goal = document.createElement("p");
  goal.className = "plan-goal";
  goal.textContent = plan.user_goal || "将按计划执行当前分析任务。";

  const list = document.createElement("ol");
  list.className = "plan-step-list";
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  for (const step of steps) {
    const item = document.createElement("li");
    item.className = "plan-step";

    const title = document.createElement("div");
    title.className = "plan-step-title";
    title.textContent = step.goal || step.step_id || "计划步骤";

    const meta = document.createElement("div");
    meta.className = "plan-step-meta";
    meta.append(
      createPlanPill("意图", step.intent || "-"),
      createPlanPill("工具", (step.allowed_tools || []).join(" / ") || "-"),
      createPlanPill("重试", step.retry_limit ?? 0),
    );

    item.append(title, meta);
    list.append(item);
  }

  body.append(summary, goal, list);
  message.append(label, body);
  chatLog.append(message);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function createPlanPill(label, value) {
  const pill = document.createElement("span");
  pill.className = "plan-pill";
  pill.textContent = `${label}: ${value}`;
  return pill;
}

function appendChartMessage(chart) {
  clearThinkingMessage();
  const message = document.createElement("div");
  message.className = "chat-message chart";

  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = chart.title || "生成图表";

  const figure = document.createElement("figure");
  figure.className = "chart-figure";

  const image = document.createElement("img");
  image.src = chart.chart_url;
  image.alt = chart.title || chart.chart_id;
  image.loading = "lazy";

  const caption = document.createElement("figcaption");
  caption.textContent = `${chart.chart_type || "chart"} · ${chart.chart_id}`;

  figure.append(image, caption);
  message.append(label, figure);
  chatLog.append(message);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function parseToolResult(result) {
  if (typeof result !== "string") {
    return result;
  }

  try {
    return JSON.parse(result);
  } catch {
    return result;
  }
}

function handleChatEvent(eventData, assistantContent) {
  if (eventData.type === "context_compacting") {
    showStatus(eventData.content || "正在进行上下文压缩...");
    setThinkingMessage(eventData.content || "上下文较长，正在压缩早期对话，请稍候...");
    return assistantContent;
  }

  if (eventData.type === "context") {
    updateContextMeter(eventData);
    if (eventData.compacted) {
      clearThinkingMessage();
      showStatus("上下文压缩完成，早期对话已整理为摘要。");
    }
    return assistantContent;
  }

  if (eventData.type === "conversation") {
    currentConversationId = eventData.conversation_id;
    currentDatasetId = eventData.dataset_id;
    conversationSelect.value = eventData.conversation_id;
    return assistantContent;
  }

  if (eventData.type === "status") {
    showStatus(eventData.content);
    return assistantContent;
  }

  if (eventData.type === "thinking") {
    setThinkingMessage(eventData.content || "模型正在思考下一步...");
    return assistantContent;
  }

  if (eventData.type === "plan") {
    appendPlanMessage(eventData.plan || {});
    return assistantContent;
  }

  if (eventData.type === "tool_reason") {
    appendChatMessage("assistant", eventData.content || "我需要先调用工具获取准确结果。");
    return null;
  }

  if (eventData.type === "tool_start") {
    createToolCard(eventData.name, eventData.args || {});
    return null;
  }

  if (eventData.type === "tool_end") {
    completeToolCard(
      eventData.name,
      eventData.args || {},
      parseToolResult(eventData.result),
      {
        duration_ms: eventData.duration_ms,
        duration_label: eventData.duration_label,
        success: eventData.success,
      },
    );
    return assistantContent;
  }

  if (eventData.type === "chart") {
    appendChartMessage(eventData);
    return assistantContent;
  }

  if (eventData.type === "text_delta") {
    clearThinkingMessage();
    const contentBox = assistantContent || appendChatMessage("assistant", "");
    contentBox.dataset.markdown = `${contentBox.dataset.markdown || ""}${eventData.content}`;
    renderMarkdown(contentBox, contentBox.dataset.markdown);
    chatLog.scrollTop = chatLog.scrollHeight;
    return contentBox;
  }

  if (eventData.type === "error") {
    clearThinkingMessage();
    throw new Error(formatErrorDetail(eventData.detail, "查询失败"));
  }

  if (eventData.type === "done") {
    clearThinkingMessage();
    return assistantContent;
  }

  return assistantContent;
}

async function readSseStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() || "";

    for (const part of parts) {
      const eventData = parseSseMessage(part);
      if (!eventData) {
        continue;
      }
      onEvent(eventData);
    }
  }

  if (buffer.trim()) {
    const eventData = parseSseMessage(buffer);
    if (eventData) {
      onEvent(eventData);
    }
  }
}

function parseSseMessage(rawMessage) {
  const dataLines = rawMessage
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart());

  if (!dataLines.length) {
    return null;
  }

  return JSON.parse(dataLines.join("\n"));
}

async function initializeApp() {
  try {
    await refreshDatasets();
    await refreshConversations();
    if (datasets.length) {
      await loadDataset(datasets[0].dataset_id);
      showStatus("已加载本地保存的数据集。");
    }
  } catch (error) {
    showStatus(error.message, true);
  }
}

initializeApp();
