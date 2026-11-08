"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {
  conversations: [],
  conversation: null,
  nextConversationCursor: null,
  showArchived: false,
  messages: [],
  providers: [],
  profiles: [],
  runTraces: new Map(),
  streamController: null,
  planning: null,
  planningPoll: null,
  planningProgress: [],
  planningError: null,
  planningNotice: null,
  planningDisclosure: null,
  composerMode: "chat",
  streamKind: null,
  toastTimer: null,
};

const activePlanningStatuses = new Set([
  "drafting", "awaiting_approval", "running", "awaiting_tool_approval", "paused",
]);

const terminalPlanningStatuses = new Set([
  "completed", "failed", "cancelled", "superseded",
]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || "请求失败");
  return data;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { node.className = "toast"; }, 2600);
}

function button(label, action, className = "") {
  const node = document.createElement("button");
  node.type = "button";
  node.textContent = label;
  node.className = className;
  node.addEventListener("click", action);
  return node;
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function emptyList(text) {
  const node = document.createElement("div");
  node.className = "empty-list";
  node.textContent = text;
  return node;
}

function closeDrawers() {
  $("#conversation-drawer").classList.remove("open");
  $("#inspector-drawer").classList.remove("open");
  $("#scrim").classList.remove("show");
}

async function loadConversations(append = false) {
  const params = new URLSearchParams({
    page_size: "50",
    status: state.showArchived ? "archived" : "active",
  });
  if (append && state.nextConversationCursor) {
    params.set("before_created_at", state.nextConversationCursor.created_at);
    params.set("before_id", state.nextConversationCursor.id);
  }
  const page = await api(`/conversations?${params}`);
  state.conversations = append ? [...state.conversations, ...page.items] : page.items;
  state.nextConversationCursor = page.next_cursor;
  renderConversations();
}

function renderConversations() {
  const list = $("#conversation-list");
  list.replaceChildren();
  if (!state.conversations.length) list.append(emptyList(state.showArchived ? "没有已归档会话" : "还没有会话"));
  for (const conversation of state.conversations) {
    const item = document.createElement("div");
    item.className = `conversation-item${state.conversation?.id === conversation.id ? " active" : ""}`;
    item.tabIndex = 0;
    const title = document.createElement("strong");
    title.textContent = conversation.title;
    const date = document.createElement("small");
    date.textContent = formatDate(conversation.created_at);
    const more = button("•••", (event) => {
      event.stopPropagation();
      item.classList.toggle("open");
    }, "conversation-more");
    const actions = document.createElement("div");
    actions.className = "conversation-actions";
    actions.append(
      button("重命名", async (event) => {
        event.stopPropagation();
        const next = prompt("会话名称", conversation.title);
        if (!next?.trim()) return;
        await api(`/conversations/${conversation.id}`, { method: "PATCH", body: JSON.stringify({ title: next.trim() }) });
        await loadConversations();
        if (state.conversation?.id === conversation.id) {
          state.conversation.title = next.trim();
          $("#conversation-title").textContent = next.trim();
        }
      }),
      button(conversation.status === "archived" ? "恢复" : "归档", async (event) => {
        event.stopPropagation();
        await api(`/conversations/${conversation.id}`, { method: "PATCH", body: JSON.stringify({ status: conversation.status === "archived" ? "active" : "archived" }) });
        if (state.conversation?.id === conversation.id) selectConversation(null);
        await loadConversations();
      }),
      button("删除", async (event) => {
        event.stopPropagation();
        if (!confirm(`删除“${conversation.title}”及其消息？`)) return;
        await api(`/conversations/${conversation.id}`, { method: "DELETE" });
        if (state.conversation?.id === conversation.id) selectConversation(null);
        await loadConversations();
      }, "delete"),
    );
    item.append(title, date, more, actions);
    const open = () => selectConversation(conversation);
    item.addEventListener("click", open);
    item.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
    list.append(item);
  }
  $("#load-more-conversations").classList.toggle("hidden", !state.nextConversationCursor);
}

async function selectConversation(conversation) {
  clearInterval(state.planningPoll);
  state.planningPoll = null;
  state.conversation = conversation;
  state.planning = null;
  state.planningProgress = [];
  state.planningError = null;
  state.planningNotice = null;
  state.planningDisclosure = null;
  state.composerMode = conversation?.mode === "plan" ? "plan" : "chat";
  $("#conversation-title").textContent = conversation?.title || "选择或新建一个会话";
  $("#message-input").disabled = !conversation;
  $("#send-message").disabled = !conversation;
  closeDrawers();
  renderConversations();
  if (!conversation) {
    state.messages = [];
    renderMessages();
    renderPlanning();
    renderComposerMode();
    return;
  }
  await Promise.all([loadMessages(), loadPlanning()]);
  syncPlanningProgressFromMessages();
  renderMessages();
  renderPlanning();
}

async function loadMessages() {
  const page = await api(`/conversations/${state.conversation.id}/messages?page_size=100`);
  state.messages = page.items;
  syncPlanningProgressFromMessages();
  renderMessages();
  requestAnimationFrame(() => { $("#chat-stage").scrollTop = $("#chat-stage").scrollHeight; });
}

async function loadPlanning() {
  if (!state.conversation) return;
  const conversationId = state.conversation.id;
  const planning = await api(`/conversations/${conversationId}/planning`);
  if (state.conversation?.id !== conversationId) return;
  const previousRevision = state.planning?.row_revision;
  state.planning = planning;
  syncPlanningProgressFromMessages();
  if (planning && activePlanningStatuses.has(planning.status)) {
    state.conversation.mode = "plan";
    if (planning.status === "drafting") state.composerMode = "plan";
  } else if (state.conversation.mode === "plan") {
    state.conversation.mode = "default";
    state.composerMode = "chat";
  }
  renderPlanning();
  revealPlanningUpdate(previousRevision);
  renderComposerMode();
  schedulePlanningPoll();
}

function schedulePlanningPoll() {
  clearInterval(state.planningPoll);
  state.planningPoll = null;
  if (!state.conversation || !["drafting", "running"].includes(state.planning?.status)) return;
  state.planningPoll = setInterval(async () => {
    if (state.streamController) return;
    try {
      await loadPlanning();
      if (state.planning?.status === "drafting") await loadMessages();
    } catch (error) {
      clearInterval(state.planningPoll);
      state.planningPoll = null;
      toast(error.message, true);
    }
  }, 1000);
}

function planningComposerBlockReason() {
  if (state.composerMode !== "plan") return null;
  const reasons = {
    awaiting_approval: "计划已经生成，请在输入框上方操作区批准、修订或取消，或切换到聊天。",
    running: "计划正在执行；你可以查看任务进度，或切换到聊天继续提问。",
    awaiting_tool_approval: "计划正在等待工具审批，请在输入框上方操作区打开审批，或切换到聊天。",
    paused: "计划已暂停，请在输入框上方操作区重试、修订或取消，或切换到聊天。",
  };
  return reasons[state.planning?.status] || null;
}

function renderComposerMode() {
  document.querySelectorAll("#mode-switch button").forEach((node) => {
    const active = node.dataset.mode === state.composerMode;
    node.classList.toggle("active", active);
    node.setAttribute("aria-pressed", String(active));
    node.disabled = Boolean(state.streamController) || !state.conversation;
  });
  const input = $("#message-input");
  const blockReason = planningComposerBlockReason();
  const blocked = Boolean(state.streamController) || !state.conversation || Boolean(blockReason);
  input.disabled = blocked;
  $("#send-message").disabled = blocked;
  input.placeholder = blockReason || (state.composerMode === "plan"
    ? "描述任务目标或回答规划问题，Enter 发送"
    : "输入消息，Enter 发送，Shift + Enter 换行");
  $("#composer-hint").textContent = state.streamController
    ? "Agent 正在运行"
    : blockReason || "等待输入";
}

const planningStatusLabels = {
  drafting: "正在规划", awaiting_approval: "等待审批", running: "执行中",
  awaiting_tool_approval: "等待工具审批", paused: "已暂停", completed: "已完成",
  failed: "失败", cancelled: "已取消", superseded: "已修订",
};

const planningAttentionStatuses = new Set([
  "awaiting_approval", "awaiting_tool_approval", "paused", "completed",
]);

const taskStatusLabels = {
  pending: "待执行", running: "执行中", completed: "已完成",
  failed: "失败", blocked: "已阻塞", cancelled: "已取消",
};

const planningErrorLabels = {
  "planning model step failed": "模型没有完成当前步骤",
  "planning tool step failed": "当前步骤的工具执行失败",
  "planning verification failed": "最终验证未通过",
  "planning verification could not run": "最终验证命令无法运行",
  "planning recovery failed": "服务重启后恢复计划失败",
  "planning run could not continue": "计划无法继续，请查看服务日志",
};

function planningErrorText(value) {
  return planningErrorLabels[value] || value;
}

function planningAction(label, action, className = "quiet") {
  const node = button(label, async () => {
    if (state.streamController) return;
    node.disabled = true;
    try { await action(); } catch (error) { toast(error.message, true); }
    finally { node.disabled = false; }
  }, className);
  node.disabled = Boolean(state.streamController);
  return node;
}

function scrollPlanningToLatest() {
  requestAnimationFrame(() => {
    const stage = $("#chat-stage");
    stage.scrollTop = stage.scrollHeight;
  });
}

function revealPlanningUpdate(previousRevision) {
  const plan = state.planning;
  if (
    plan
    && plan.row_revision !== previousRevision
    && planningAttentionStatuses.has(plan.status)
  ) scrollPlanningToLatest();
}

function planningControlActions(plan) {
  const actions = document.createElement("div");
  actions.className = "planning-actions";
  const allowed = new Set(plan.allowed_actions || []);
  if (allowed.has("approve")) actions.append(planningAction("批准并执行", approvePlanning, "primary"));
  if (allowed.has("retry_current")) actions.append(planningAction("重试当前步骤", () => runPlanningAction("retry", {}), "primary"));
  if (allowed.has("revise")) actions.append(planningAction("修订计划", async () => {
    const feedback = prompt("请输入修订要求");
    if (feedback?.trim()) await runPlanningAction("revise", { feedback: feedback.trim() });
  }));
  if (allowed.has("decide_tool_approval")) actions.append(planningAction("查看并处理工具审批", scrollPlanningToLatest, "primary"));
  if (allowed.has("cancel")) actions.append(planningAction("取消计划", cancelPlanning, "danger"));
  return actions;
}

function renderPlanningInlineActions(plan) {
  const inline = $("#planning-inline-actions");
  inline.replaceChildren();
  if (!plan) {
    inline.classList.add("hidden");
    return;
  }
  const actions = planningControlActions(plan);
  if (!actions.childElementCount) {
    inline.classList.add("hidden");
    return;
  }
  actions.append(button("查看任务详情", scrollPlanningToLatest, "quiet"));
  const label = document.createElement("strong");
  label.textContent = planningInlineLabel(plan);
  inline.append(label, actions);
  inline.classList.remove("hidden");
}

function planningInlineLabel(plan) {
  const completed = plan.steps.filter((step) => step.status === "completed").length;
  const current = plan.steps.find((step) => step.step_id === plan.current_step_id);
  const progress = `${completed}/${plan.steps.length}`;
  if (plan.status === "awaiting_approval") return `计划等待你确认 · ${progress}`;
  if (plan.status === "running") return `正在执行：${current?.title || "准备下一步"} · ${progress}`;
  if (plan.status === "awaiting_tool_approval") return `工具调用等待你审批：${current?.title || "当前步骤"} · ${progress}`;
  if (plan.status === "paused") return `计划已暂停：${planningErrorText(plan.last_error)} · ${progress}`;
  return `当前计划操作 · ${progress}`;
}

function planningProgressText(entry) {
  const stage = traceLabels[entry.stage] || "运行";
  const step = entry.step_number ? `第 ${entry.step_number} 轮 · ` : "";
  return `${stage} · ${step}${entry.message || "规划运行中"}`;
}

function syncPlanningProgressFromMessages() {
  if (state.planningProgress.length || !state.planning) return;
  const expected = state.planning.plan_markdown;
  const message = [...state.messages].reverse().find((item) => (
    item.role === "assistant"
      && item.run_trace?.length
      && (!expected || item.content === expected)
  ));
  if (!message) return;
  state.planningProgress = message.run_trace
    .map(planningProgressText)
    .slice(-200);
}

function planningShellOpen(plan) {
  const previous = state.planningDisclosure;
  const terminal = terminalPlanningStatuses.has(plan.status);
  let open = !terminal;
  if (previous?.runId === plan.run_id) {
    open = terminal && !terminalPlanningStatuses.has(previous.status)
      ? false
      : previous.open;
  }
  state.planningDisclosure = {
    runId: plan.run_id,
    status: plan.status,
    open,
  };
  return open;
}

function renderPlanning() {
  const panel = $("#planning-panel");
  panel.replaceChildren();
  const plan = state.planning;
  renderPlanningInlineActions(plan);
  panel.classList.toggle("hidden", !plan);
  if (!plan) {
    renderPlanningFinalReply(null);
    return;
  }

  const shell = document.createElement("details");
  shell.className = "planning-shell";
  shell.open = planningShellOpen(plan);

  const head = document.createElement("summary");
  head.className = "planning-head";
  const heading = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = `PLAN V${plan.version}`;
  const title = document.createElement("h2");
  title.textContent = plan.objective;
  heading.append(eyebrow, title);
  const completed = plan.steps.filter((step) => step.status === "completed").length;
  const status = document.createElement("span");
  status.className = `planning-status ${plan.status}`;
  status.textContent = `${planningStatusLabels[plan.status] || plan.status} · ${completed}/${plan.steps.length}`;
  head.append(heading, status);

  const actions = planningControlActions(plan);
  const allowed = new Set(plan.allowed_actions || []);

  const list = document.createElement("div");
  list.className = "planning-steps";
  for (const step of plan.steps) {
    const card = document.createElement("details");
    card.className = `planning-step ${step.status}${step.step_id === plan.current_step_id ? " current" : ""}`;
    card.open = step.step_id === plan.current_step_id || ["failed", "blocked"].includes(step.status);
    const summary = document.createElement("summary");
    const marker = document.createElement("span");
    marker.className = "step-marker";
    marker.textContent = step.status === "completed" ? "✓" : step.status === "running" ? "●" : "○";
    const name = document.createElement("strong");
    name.textContent = step.title;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = step.is_final_verification ? "最终验证" : taskStatusLabels[step.status] || step.status;
    summary.append(marker, name, badge);
    const body = document.createElement("div");
    body.className = "planning-step-body";
    const instruction = document.createElement("p");
    instruction.textContent = step.instruction;
    const criteria = document.createElement("p");
    criteria.textContent = `完成标准：${step.completion_criteria}`;
    const dependencies = document.createElement("small");
    dependencies.textContent = `依赖：${step.dependency_ids.join("、") || "无"}${step.blocked_by.length ? ` · 阻塞于：${step.blocked_by.join("、")}` : ""}`;
    body.append(instruction, criteria, dependencies);
    if (step.result || step.error) {
      const output = document.createElement("pre");
      output.textContent = step.result || step.error;
      body.append(output);
    }
    card.append(summary, body);
    list.append(card);
  }

  const approvals = document.createElement("div");
  approvals.className = "planning-approvals";
  for (const approval of plan.pending_approvals || []) {
    const card = document.createElement("article");
    card.className = "approval-card";
    const title = document.createElement("strong");
    title.textContent = `需要审批：${approval.tool_name}`;
    const summary = document.createElement("pre");
    summary.textContent = approval.summary;
    card.append(title, summary);
    if (allowed.has("decide_tool_approval")) {
      const controls = document.createElement("div");
      controls.className = "planning-actions";
      controls.append(
        planningAction("批准", () => decideToolApproval(approval, "approve"), "primary"),
        planningAction("拒绝", () => decideToolApproval(approval, "reject"), "danger"),
      );
      card.append(controls);
    }
    approvals.append(card);
  }
  const progress = document.createElement("details");
  progress.className = "planning-progress";
  progress.open = false;
  const progressTitle = document.createElement("summary");
  progressTitle.textContent = "运行进度（不包含模型私有推理）";
  const progressList = document.createElement("ol");
  for (const item of state.planningProgress) {
    const row = document.createElement("li");
    row.textContent = item;
    progressList.append(row);
  }
  if (!state.planningProgress.length) {
    const empty = document.createElement("p");
    empty.textContent = plan.status === "drafting"
      ? "提交目标后，这里会显示模型请求、文件读取和工具运行状态。"
      : "这份计划生成时尚未保存运行详情，旧过程无法还原；修订或重新提交后会完整记录。";
    progress.append(progressTitle, empty);
  } else {
    progress.append(progressTitle, progressList);
  }

  const errorMessage = state.planningError || plan.last_error;
  const notice = document.createElement("p");
  notice.className = "planning-notice";
  notice.textContent = state.planningNotice || "";
  notice.classList.toggle("hidden", !state.planningNotice);
  const content = document.createElement("div");
  content.className = "planning-content";
  if (errorMessage) {
    const error = document.createElement("p");
    error.className = "planning-error";
    error.textContent = planningErrorText(errorMessage);
    content.append(progress, error, notice, list, actions, approvals);
  } else {
    content.append(progress, notice, list, actions, approvals);
  }
  shell.append(head, content);
  panel.append(shell);
  shell.addEventListener("toggle", () => {
    if (state.planning?.run_id !== plan.run_id) return;
    state.planningDisclosure = {
      runId: plan.run_id,
      status: plan.status,
      open: shell.open,
    };
  });
  renderPlanningFinalReply(plan);
}

function appendRichText(container, text) {
  const parts = String(text || "").split("```");
  parts.forEach((part, index) => {
    if (index % 2 === 0) {
      const paragraphs = part.split(/\n{2,}/);
      for (const paragraph of paragraphs) {
        if (!paragraph) continue;
        const p = document.createElement("p");
        p.textContent = paragraph;
        container.append(p);
      }
      return;
    }
    const block = document.createElement("div");
    block.className = "code-block";
    const pre = document.createElement("pre");
    const firstBreak = part.indexOf("\n");
    const code = firstBreak >= 0 ? part.slice(firstBreak + 1) : part;
    pre.textContent = code;
    block.append(pre, button("复制", async () => {
      await navigator.clipboard.writeText(code);
      toast("代码已复制");
    }, "copy-code"));
    container.append(block);
  });
}

const traceLabels = {
  retrieval: "检索",
  model: "模型",
  retry: "重试",
  tool: "工具",
  saving: "保存",
  done: "完成",
  error: "错误",
};

function addRunStatus(track, payload) {
  const entry = {
    stage: payload.stage || "model",
    message: payload.message || payload.stage || "运行中",
  };
  if (payload.step_number != null) entry.step_number = payload.step_number;
  track.entries.push(entry);
  track.title.textContent = entry.message;
  track.count.textContent = `${track.entries.length} 步`;
  const line = document.createElement("div");
  line.className = `run-event ${entry.stage}`;
  const marker = document.createElement("span");
  marker.className = "run-event-marker";
  marker.textContent = traceLabels[entry.stage] || "运行";
  const message = document.createElement("span");
  message.textContent = entry.message;
  line.append(marker, message);
  if (entry.step_number != null) {
    const step = document.createElement("code");
    step.textContent = `STEP ${entry.step_number}`;
    line.append(step);
  }
  track.events.append(line);
}

function createRunTrace(trace = { entries: [] }, streaming = false) {
  const details = document.createElement("details");
  details.className = `run-trace${streaming ? " streaming" : ""}`;
  details.open = streaming;
  const summary = document.createElement("summary");
  const signal = document.createElement("span");
  signal.className = "run-signal";
  const title = document.createElement("span");
  title.className = "run-title";
  title.textContent = streaming ? "正在启动" : "运行详情";
  const count = document.createElement("span");
  count.className = "run-count";
  count.textContent = "0 步";
  summary.append(signal, title, count);
  const events = document.createElement("div");
  events.className = "run-events";
  details.append(summary, events);
  const track = { details, title, count, events, entries: [] };
  for (const entry of trace.entries || []) addRunStatus(track, entry);
  if (!track.entries.length && !streaming) {
    count.textContent = "未记录";
    const empty = document.createElement("p");
    empty.className = "run-empty";
    empty.textContent = "这条回复生成时尚未启用运行详情记录；新回复会保留完整流程。";
    events.append(empty);
  }
  return track;
}

function renderMessage(message, temporary = false, showAssistantDetails = true) {
  const row = document.createElement("article");
  row.className = `message ${message.role}`;
  row.dataset.messageId = message.id || "";
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = message.role === "user" ? "你" : "AI";
  const content = document.createElement("div");
  const head = document.createElement("div");
  head.className = "message-head";
  const name = document.createElement("span");
  name.textContent = message.role === "user" ? "你" : "MyAgent";
  const time = document.createElement("time");
  time.textContent = temporary ? "生成中" : formatDate(message.created_at);
  head.append(name, time);
  const body = document.createElement("div");
  body.className = "message-body";
  appendRichText(body, message.content);
  content.append(head, body);
  if (message.role === "assistant" && !temporary && showAssistantDetails) {
    content.append(createRunTrace(message.run_trace || { entries: [] }).details);
  }
  if (message.role === "assistant" && !temporary && showAssistantDetails) {
    const tools = document.createElement("div");
    tools.className = "message-tools";
    tools.append(
      button("复制", async () => {
        await navigator.clipboard.writeText(message.content);
        toast("回复已复制");
      }),
      button("保存为记忆", () => openMemoryForm(message)),
    );
    content.append(tools);
    if (message.citations?.length) {
      const citations = document.createElement("div");
      citations.className = "citations";
      for (const citation of message.citations) {
        const tag = document.createElement("span");
        tag.className = "citation";
        tag.textContent = `${citation.marker} ${citation.title || citation.source_type}`;
        citations.append(tag);
      }
      content.append(citations);
    }
  }
  row.append(avatar, content);
  return { row, body, content, time };
}

function isPlanningDefinitionMessage(message) {
  if (message.role !== "assistant") return false;
  if (message.content === state.planning?.plan_markdown) return true;
  const trace = Array.isArray(message.run_trace)
    ? message.run_trace
    : message.run_trace?.entries || [];
  return trace.some((entry) => (
    entry.stage === "done" && entry.message === "计划已生成，等待你的审批"
  ));
}

function planningResultHeadline(plan) {
  const step = [...plan.steps].reverse().find((item) => (
    !item.is_final_verification
      && typeof item.result === "string"
      && item.result.trim()
  ));
  if (!step) return "";
  const line = step.result.split(/\r?\n/)
    .map((item) => item.replaceAll("**", "").trim())
    .find((item) => item && !item.startsWith("#") && !item.startsWith("|") && item !== "```");
  if (!line) return "";
  return line.length > 280 ? `${line.slice(0, 280)}…` : line;
}

function renderPlanningFinalReply(plan) {
  const container = $("#planning-final");
  container.replaceChildren();
  if (plan?.status !== "completed") return;
  const completed = plan.steps.filter((step) => step.status === "completed").length;
  const finalStep = plan.steps.find((step) => step.is_final_verification);
  const headline = planningResultHeadline(plan);
  const lines = [
    "计划执行完成。",
    "",
    `最终结果：${completed}/${plan.steps.length} 个步骤已完成，最终验证通过。`,
  ];
  if (headline) lines.push(`执行摘要：${headline}`);
  if (finalStep?.result?.trim()) lines.push(`验证结果：${finalStep.result.trim()}`);
  const message = {
    role: "assistant",
    content: lines.join("\n"),
    created_at: null,
  };
  const node = renderMessage(message, false, false);
  node.row.classList.add("planning-final-message");
  container.append(node.row);
}

function renderMessages() {
  const list = $("#messages");
  list.replaceChildren();
  $("#empty-chat").classList.toggle("hidden", state.messages.length > 0);
  state.messages.forEach((message, index) => {
    if (message.role !== "user" && message.role !== "assistant") return;
    if (isPlanningDefinitionMessage(message)) return;
    const enriched = { ...message };
    if (message.role === "assistant") {
      enriched.source_user_id = [...state.messages.slice(0, index)].reverse().find((item) => item.role === "user")?.id;
      enriched.run_trace = state.runTraces.get(message.id)
        || (message.run_trace?.length ? { entries: message.run_trace } : null);
    }
    list.append(renderMessage(enriched).row);
  });
  renderPlanningFinalReply(state.planning);
}

function makeRunTrace(container, expanded = true) {
  const track = createRunTrace({ entries: [] }, true);
  track.details.open = expanded;
  container.append(track.details);
  return track;
}

async function streamMessage(content) {
  if (!state.conversation || state.streamController) return false;
  let succeeded = false;
  const user = { role: "user", content, created_at: new Date().toISOString() };
  state.messages.push(user);
  $("#empty-chat").classList.add("hidden");
  const userNode = renderMessage(user, true);
  const assistant = { role: "assistant", content: "", created_at: new Date().toISOString() };
  const assistantNode = renderMessage(assistant, true);
  $("#messages").append(userNode.row, assistantNode.row);
  const track = makeRunTrace(assistantNode.content);
  state.streamController = new AbortController();
  state.streamKind = "chat";
  setStreaming(true);
  $("#chat-stage").scrollTop = $("#chat-stage").scrollHeight;
  try {
    const response = await fetch(`/conversations/${state.conversation.id}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
      signal: state.streamController.signal,
    });
    if (!response.ok || !response.body) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || "无法启动流式请求");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalReply = null;
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary).replace(/\r/g, "");
        buffer = buffer.slice(boundary + 2);
        let eventName = "message";
        const dataLines = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
        }
        if (!dataLines.length) continue;
        const payload = JSON.parse(dataLines.join("\n"));
        if (eventName === "status") addRunStatus(track, payload);
        if (eventName === "delta") {
          if (payload.reset) assistant.content = "";
          assistant.content += payload.text;
          assistantNode.body.replaceChildren();
          appendRichText(assistantNode.body, assistant.content);
          assistantNode.time.textContent = "生成中";
          $("#chat-stage").scrollTop = $("#chat-stage").scrollHeight;
        }
        if (eventName === "error") throw new Error(payload.message || "生成失败");
        if (eventName === "done") finalReply = payload;
      }
      if (done) break;
    }
    if (!finalReply) throw new Error("连接已结束，但没有收到最终回复");
    assistant.content = finalReply.content;
    assistant.citations = finalReply.citations;
    addRunStatus(track, { stage: "done", message: "回复已保存" });
    track.details.classList.remove("streaming");
    track.details.open = false;
    state.runTraces.set(finalReply.message_id, {
      outcome: "done",
      entries: finalReply.run_trace?.length ? finalReply.run_trace : [...track.entries],
    });
    await loadMessages();
    succeeded = true;
  } catch (error) {
    addRunStatus(track, {
      stage: "error",
      message: error.name === "AbortError" ? "已停止生成" : "运行失败",
    });
    track.details.classList.remove("streaming");
    if (error.name !== "AbortError") toast(error.message, true);
    await loadMessages().catch(() => {});
  } finally {
    state.streamController = null;
    state.streamKind = null;
    setStreaming(false);
  }
  return succeeded;
}

async function readPlanningStream(response, track) {
  if (!response.ok || !response.body) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || "无法启动规划请求");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = null;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, boundary).replace(/\r/g, "");
      buffer = buffer.slice(boundary + 2);
      let eventName = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      if (!dataLines.length) continue;
      const payload = JSON.parse(dataLines.join("\n"));
      if (eventName === "error") {
        state.planningError = payload.message || "规划失败";
        renderPlanning();
        throw new Error(state.planningError);
      }
      if (eventName === "status") {
        const item = planningProgressText(payload);
        if (state.planningProgress.at(-1) !== item) {
          state.planningProgress.push(item);
          state.planningProgress = state.planningProgress.slice(-200);
        }
        if (track) addRunStatus(track, payload);
        $("#composer-hint").textContent = payload.message || "规划运行中";
        renderPlanning();
      }
      if (eventName === "snapshot") {
        const previousRevision = state.planning?.row_revision;
        state.planning = payload;
        renderPlanning();
        revealPlanningUpdate(previousRevision);
        renderComposerMode();
      }
      if (eventName === "done" && payload.snapshot) {
        completed = payload;
        const previousRevision = state.planning?.row_revision;
        state.planning = payload.snapshot;
        state.planningError = null;
        renderPlanning();
        revealPlanningUpdate(previousRevision);
      }
    }
    if (done) break;
  }
  if (!completed) throw new Error("连接已结束，但没有收到最终规划结果");
  return completed;
}

async function streamPlanning(path, payload, track = null) {
  if (!state.conversation || state.streamController) return false;
  let succeeded = false;
  clearInterval(state.planningPoll);
  state.planningPoll = null;
  state.streamController = new AbortController();
  state.streamKind = "planning";
  state.planningProgress = [];
  state.planningError = null;
  setStreaming(true);
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: state.streamController.signal,
    });
    const done = await readPlanningStream(response, track);
    if (track && done.message_id) {
      track.details.classList.remove("streaming");
      track.details.open = false;
      state.runTraces.set(done.message_id, {
        outcome: "done",
        entries: done.run_trace?.length ? done.run_trace : [...track.entries],
      });
    }
    succeeded = true;
  } catch (error) {
    if (track) {
      addRunStatus(track, {
        stage: "error",
        message: error.name === "AbortError" ? "已停止接收" : "规划失败",
      });
      track.details.classList.remove("streaming");
      track.details.open = true;
    }
    if (error.name === "AbortError") toast("已停止接收；计划仍在服务端运行");
    else {
      state.planningError = error.message;
      renderPlanning();
      toast(error.message, true);
    }
  } finally {
    state.streamController = null;
    state.streamKind = null;
    setStreaming(false);
    await Promise.all([
      loadPlanning().catch(() => {}),
      loadMessages().catch(() => {}),
      loadConversations().catch(() => {}),
    ]);
    syncPlanningProgressFromMessages();
    renderPlanning();
  }
  return succeeded;
}

function beginPlanningDraft(content) {
  const user = { role: "user", content, created_at: new Date().toISOString() };
  const assistant = {
    role: "assistant",
    content: "正在分析任务并生成可执行计划……",
    created_at: new Date().toISOString(),
  };
  const userNode = renderMessage(user, true);
  const assistantNode = renderMessage(assistant, true);
  $("#empty-chat").classList.add("hidden");
  $("#messages").append(userNode.row, assistantNode.row);
  const track = makeRunTrace(assistantNode.content, false);
  $("#chat-stage").scrollTop = $("#chat-stage").scrollHeight;
  return track;
}

async function streamPlanningDraft(content) {
  const payload = { content };
  if (state.planning?.status === "drafting") {
    payload.expected_row_revision = state.planning.row_revision;
  }
  state.composerMode = "plan";
  renderComposerMode();
  const track = beginPlanningDraft(content);
  return streamPlanning(
    `/conversations/${state.conversation.id}/planning/draft/stream`,
    payload,
    track,
  );
}

async function runPlanningAction(action, extra) {
  const plan = state.planning;
  if (!plan) return;
  return streamPlanning(
    `/planning-runs/${plan.run_id}/${action}/stream`,
    { expected_row_revision: plan.row_revision, ...extra },
  );
}

function showPlanningNotice(message) {
  state.planningNotice = message;
  renderPlanning();
  scrollPlanningToLatest();
}

async function approvePlanning() {
  showPlanningNotice("计划批准已提交，正在开始执行……");
  const succeeded = await runPlanningAction("approve", {});
  if (!succeeded) {
    state.planningNotice = null;
    renderPlanning();
    return;
  }
  if (state.planning?.status === "awaiting_tool_approval") {
    showPlanningNotice("计划已批准并开始执行；现在需要处理工具审批。");
  } else if (state.planning?.status === "completed") {
    showPlanningNotice("计划已批准并执行完成。");
  } else {
    showPlanningNotice("计划已批准，任务正在继续执行。");
  }
}

async function decideToolApproval(approval, decision) {
  let rejectionMessage = null;
  if (decision === "reject") {
    rejectionMessage = prompt("请输入拒绝原因");
    if (!rejectionMessage?.trim()) return;
    rejectionMessage = rejectionMessage.trim();
  }
  showPlanningNotice(decision === "approve"
    ? `正在批准 ${approval.tool_name}……`
    : `正在拒绝 ${approval.tool_name}……`);
  const succeeded = await runPlanningAction("tool-approval", {
    tool_name: approval.tool_name,
    call_id: approval.call_id,
    decision,
    rejection_message: rejectionMessage,
  });
  if (!succeeded) {
    state.planningNotice = null;
    renderPlanning();
    return;
  }
  const sameCallPending = state.planning?.pending_approvals?.some((item) => (
    item.tool_name === approval.tool_name && item.call_id === approval.call_id
  ));
  if (sameCallPending) {
    showPlanningNotice("审批状态没有更新，请刷新后重试。");
  } else if (decision === "reject") {
    showPlanningNotice(`已拒绝 ${approval.tool_name}，任务状态已经更新。`);
  } else if (state.planning?.pending_approvals?.length) {
    showPlanningNotice("本次工具调用已批准；任务又请求了其他工具审批，请继续处理。");
  } else {
    showPlanningNotice("本次工具调用已批准，任务已经继续执行。");
  }
}

async function cancelPlanning() {
  const plan = state.planning;
  if (!plan || !confirm("确定取消当前计划？已经发生的外部改动不会自动撤销。")) return;
  state.planning = await api(`/planning-runs/${plan.run_id}/cancel`, {
    method: "POST",
    body: JSON.stringify({
      expected_row_revision: plan.row_revision,
      reason: "用户从前端取消计划",
    }),
  });
  state.conversation.mode = "default";
  state.composerMode = "chat";
  renderPlanning();
  renderComposerMode();
  await loadConversations();
}

function setStreaming(active) {
  $("#stop-generation").classList.toggle("hidden", !active);
  $("#send-message").classList.toggle("hidden", active);
  $("#message-input").disabled = active || !state.conversation;
  $("#stop-generation").textContent = state.streamKind === "planning" ? "停止接收" : "停止";
  $("#composer-hint").textContent = active ? "Agent 正在运行" : "等待输入";
  renderPlanning();
  renderComposerMode();
}

async function loadProfiles() {
  [state.providers, state.profiles] = await Promise.all([api("/model-providers"), api("/model-profiles")]);
  renderProviderOptions();
  renderProfiles();
}

function renderProviderOptions() {
  const select = $("#profile-provider");
  const selected = select.value;
  select.replaceChildren();
  for (const provider of state.providers) {
    const option = document.createElement("option");
    option.value = provider.id;
    option.textContent = provider.name;
    select.append(option);
  }
  if (selected && state.providers.some((item) => item.id === selected)) select.value = selected;
  providerChanged(false);
}

function providerChanged(resetUrl = true) {
  const provider = state.providers.find((item) => item.id === $("#profile-provider").value);
  if (!provider) return;
  if (resetUrl || !$("#profile-base-url").value) $("#profile-base-url").value = provider.base_url;
  $("#profile-api-key").required = provider.api_key_required && !$("#profile-id").value;
  $("#provider-note").textContent = provider.compatibility_note || "";
  $("#provider-note").classList.toggle("hidden", !provider.compatibility_note);
}

function renderProfiles() {
  const list = $("#profile-list");
  list.replaceChildren();
  const active = state.profiles.find((item) => item.is_active);
  $("#active-model").classList.toggle("active", Boolean(active));
  $("#active-model").lastChild.textContent = active ? `${active.name} · ${active.model}` : "未激活模型";
  renderConversationProfile();
  if (!state.profiles.length) {
    list.append(emptyList("先添加一个模型配置，然后可在任意会话中复用"));
    $("#profile-editor").open = true;
  }
  for (const profile of state.profiles) {
    const card = document.createElement("article");
    card.className = `stack-card${profile.is_active ? " active" : ""}`;
    const head = document.createElement("div"); head.className = "stack-card-head";
    const title = document.createElement("strong"); title.textContent = profile.name;
    const badge = document.createElement("span"); badge.className = `badge${profile.is_active ? " good" : ""}`; badge.textContent = profile.is_active ? "ACTIVE" : profile.provider;
    head.append(title, badge);
    const detail = document.createElement("p"); detail.textContent = profile.model;
    const meta = document.createElement("div"); meta.className = "stack-meta"; meta.textContent = `${profile.protocol} · ${profile.has_api_key ? "密钥已保存" : "无需密钥"}`;
    const actions = document.createElement("div"); actions.className = "card-actions";
    if (!profile.is_active) actions.append(button("设为当前", async () => { await activateProfile(profile.id); }));
    actions.append(
      button("测试", async () => testProfile(profile.id)),
      button("编辑", () => editProfile(profile)),
      button("删除", async () => {
        if (!confirm(`删除模型配置“${profile.name}”？`)) return;
        await api(`/model-profiles/${profile.id}`, { method: "DELETE" });
        await loadProfiles(); resetProfileForm(false);
      }, "delete"),
    );
    card.append(head, detail, meta, actions); list.append(card);
  }
}

function renderConversationProfile() {
  const select = $("#conversation-profile");
  const active = state.profiles.find((item) => item.is_active);
  select.replaceChildren();
  if (!state.profiles.length) {
    const option = document.createElement("option");
    option.textContent = "先添加模型配置";
    select.append(option);
  } else {
    if (!active) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "选择一个模型配置";
      select.append(placeholder);
    }
    for (const profile of state.profiles) {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = `${profile.name} · ${profile.model}`;
      select.append(option);
    }
    select.value = active?.id || "";
  }
  select.disabled = !state.profiles.length;
  $("#new-conversation").disabled = !active;
  $("#model-reuse-note").textContent = active
    ? `${active.name} 对所有会话生效，可重复使用`
    : "一个配置可以用于多个会话";
}

async function activateProfile(profileId) {
  await api(`/model-profiles/${profileId}/activate`, { method: "POST" });
  await loadProfiles();
  toast("当前模型已切换，后续消息将使用此配置");
}

function resetProfileForm(openEditor = false) {
  $("#profile-form").reset();
  $("#profile-id").value = "";
  $("#profile-api-key").value = "";
  $("#profile-editor-title").textContent = "新增模型配置";
  $("#profile-editor").open = openEditor;
  if (state.providers.length) $("#profile-provider").value = state.providers[0].id;
  providerChanged(true);
}

function editProfile(profile) {
  $("#profile-id").value = profile.id;
  $("#profile-name").value = profile.name;
  $("#profile-provider").value = profile.provider;
  $("#profile-model").value = profile.model;
  $("#profile-base-url").value = profile.base_url;
  $("#profile-api-key").value = "";
  $("#profile-editor-title").textContent = `编辑 ${profile.name}`;
  $("#profile-editor").open = true;
  providerChanged(false);
  $("#profile-name").focus();
}

function profilePayload() {
  const payload = {
    name: $("#profile-name").value.trim(),
    provider: $("#profile-provider").value,
    model: $("#profile-model").value.trim(),
    base_url: $("#profile-base-url").value.trim(),
  };
  const key = $("#profile-api-key").value;
  if (key) payload.api_key = key;
  return payload;
}

async function loadModelOptions() {
  const target = $("#profile-id").value;
  const key = $("#profile-api-key").value;
  const provider = state.providers.find((item) => item.id === $("#profile-provider").value);
  if (!target && provider?.api_key_required && !key) {
    toast("请先输入 API Key。", true);
    return false;
  }
  const payload = target && !key ? { profile_id: target } : {
    provider: $("#profile-provider").value,
    base_url: $("#profile-base-url").value.trim(),
  };
  if (key) payload.api_key = key;
  const result = await api("/model-profiles/models", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const options = $("#profile-model-options");
  options.replaceChildren();
  for (const model of result.models) {
    const option = document.createElement("option");
    option.value = model;
    options.append(option);
  }
  if (!result.ok) {
    toast(result.message, true);
    return false;
  }
  const input = $("#profile-model");
  if (!result.models.includes(input.value.trim())) {
    input.value = result.models[0];
  }
  toast(`${result.message} 已选择 ${input.value}`);
  return true;
}

async function testProfile(profileId = null) {
  const target = profileId || $("#profile-id").value;
  if (!target && !$("#profile-model").value.trim() && !await loadModelOptions()) return;
  const payload = target && !$("#profile-api-key").value ? { profile_id: target } : profilePayload();
  delete payload.name;
  const result = await api("/model-profiles/test", { method: "POST", body: JSON.stringify(payload) });
  toast(result.message, !result.ok);
}

async function loadMemories() {
  const params = new URLSearchParams({ page_size: "50" });
  if ($("#memory-status").value) params.set("status", $("#memory-status").value);
  const page = await api(`/memories?${params}`);
  const list = $("#memory-list"); list.replaceChildren();
  if (!page.items.length) list.append(emptyList("没有符合条件的长期记忆"));
  for (const memory of page.items) {
    const card = document.createElement("article"); card.className = "stack-card";
    const head = document.createElement("div"); head.className = "stack-card-head";
    const title = document.createElement("strong"); title.textContent = memory.kind;
    const badge = document.createElement("span"); badge.className = `badge${memory.status === "active" ? " good" : ""}`; badge.textContent = memory.status;
    head.append(title, badge);
    const content = document.createElement("p"); content.textContent = memory.content;
    const meta = document.createElement("div"); meta.className = "stack-meta"; meta.textContent = `${memory.scope} · ${formatDate(memory.updated_at)}`;
    const actions = document.createElement("div"); actions.className = "card-actions";
    actions.append(button("编辑", async () => {
      const next = prompt("修改记忆", memory.content);
      if (!next?.trim()) return;
      await api(`/memories/${memory.id}`, { method: "PATCH", body: JSON.stringify({ content: next.trim() }) });
      await loadMemories();
    }));
    if (memory.status === "active") actions.append(button("停用", async () => { await api(`/memories/${memory.id}/disable`, { method: "POST" }); await loadMemories(); }));
    actions.append(button("删除", async () => { if (!confirm("删除这条记忆？")) return; await api(`/memories/${memory.id}`, { method: "DELETE" }); await loadMemories(); }, "delete"));
    card.append(head, content, meta, actions); list.append(card);
  }
}

function openMemoryForm(message) {
  const sourceUser = message.source_user_id || [...state.messages].reverse().find((item) => item.role === "user" && item.sequence < message.sequence)?.id;
  if (!state.conversation || !sourceUser || !message.id) { toast("请先等待回复保存完成", true); return; }
  $("#memory-conversation-id").value = state.conversation.id;
  $("#memory-user-id").value = sourceUser;
  $("#memory-assistant-id").value = message.id;
  $("#memory-content").value = message.content.slice(0, 2000);
  $("#memory-scope").value = "conversation";
  $("#memory-form").classList.remove("hidden");
  switchTab("memories");
  $("#memory-content").focus();
}

async function loadKnowledge() {
  const page = await api("/knowledge?page_size=50");
  const list = $("#knowledge-list"); list.replaceChildren();
  if (!page.items.length) list.append(emptyList("还没有知识正文"));
  for (const source of page.items) {
    const card = document.createElement("article"); card.className = "stack-card";
    const head = document.createElement("div"); head.className = "stack-card-head";
    const title = document.createElement("strong"); title.textContent = source.title;
    const badge = document.createElement("span"); badge.className = `badge${source.status === "indexed" ? " good" : ""}`; badge.textContent = source.status;
    head.append(title, badge);
    const content = document.createElement("p"); content.textContent = source.text.slice(0, 180) + (source.text.length > 180 ? "…" : "");
    const meta = document.createElement("div"); meta.className = "stack-meta"; meta.textContent = source.embedding_model || "尚未建立索引";
    const actions = document.createElement("div"); actions.className = "card-actions";
    actions.append(
      button("查看", () => alert(source.text)),
      button("重建索引", async () => { try { await api(`/knowledge/${source.id}/reindex`, { method: "POST" }); await loadKnowledge(); toast("索引已重建"); } catch (error) { toast(error.message, true); } }),
      button("删除", async () => { if (!confirm(`删除知识“${source.title}”？`)) return; await api(`/knowledge/${source.id}`, { method: "DELETE" }); await loadKnowledge(); }, "delete"),
    );
    card.append(head, content, meta, actions); list.append(card);
  }
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
  if (name === "memories") loadMemories().catch((error) => toast(error.message, true));
  if (name === "knowledge") loadKnowledge().catch((error) => toast(error.message, true));
}

function bindEvents() {
  $("#new-conversation").addEventListener("click", async () => {
    if (!state.profiles.some((profile) => profile.is_active)) {
      toast("请先选择一个模型配置", true);
      return;
    }
    const conversation = await api("/conversations", { method: "POST", body: JSON.stringify({ title: "新会话" }) });
    state.showArchived = false;
    await loadConversations();
    await selectConversation(conversation);
  });
  $("#toggle-archived").addEventListener("click", async () => { state.showArchived = !state.showArchived; $("#toggle-archived").textContent = state.showArchived ? "当前" : "归档"; await loadConversations(); });
  $("#load-more-conversations").addEventListener("click", () => loadConversations(true));
  $("#composer").addEventListener("submit", async (event) => {
    event.preventDefault(); const input = $("#message-input"); const content = input.value.trim(); if (!content) return;
    const blockReason = planningComposerBlockReason();
    if (blockReason) { toast(blockReason, true); return; }
    const accepted = state.composerMode === "plan"
      ? await streamPlanningDraft(content)
      : await streamMessage(content);
    if (accepted) {
      input.value = "";
      input.style.height = "auto";
    }
  });
  document.querySelectorAll("#mode-switch button").forEach((node) => node.addEventListener("click", () => {
    if (!state.conversation || state.streamController) return;
    state.composerMode = node.dataset.mode;
    renderComposerMode();
    $("#message-input").focus();
  }));
  $("#message-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#composer").requestSubmit(); } });
  $("#message-input").addEventListener("input", (event) => { event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`; });
  $("#stop-generation").addEventListener("click", () => state.streamController?.abort());
  $("#conversation-profile").addEventListener("change", (event) => {
    if (event.target.value) activateProfile(event.target.value).catch((error) => toast(error.message, true));
  });
  $("#open-model-settings").addEventListener("click", () => {
    switchTab("models");
    $("#inspector-drawer").classList.add("open");
    $("#scrim").classList.add("show");
  });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
  $("#profile-provider").addEventListener("change", () => providerChanged(true));
  $("#profile-api-key").addEventListener("change", () => { if ($("#profile-api-key").value) loadModelOptions().catch((error) => toast(error.message, true)); });
  $("#load-models").addEventListener("click", () => loadModelOptions().catch((error) => toast(error.message, true)));
  $("#reset-profile").addEventListener("click", () => resetProfileForm(true));
  $("#test-profile").addEventListener("click", () => testProfile().catch((error) => toast(error.message, true)));
  $("#profile-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const id = $("#profile-id").value; const payload = profilePayload();
    const hadActiveProfile = state.profiles.some((profile) => profile.is_active);
    const saved = await api(id ? `/model-profiles/${id}` : "/model-profiles", { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) });
    if (!hadActiveProfile) await api(`/model-profiles/${saved.id}/activate`, { method: "POST" });
    $("#profile-api-key").value = ""; resetProfileForm(false); await loadProfiles(); toast("模型配置已保存，可用于多个会话");
  });
  $("#memory-status").addEventListener("change", () => loadMemories());
  $("#cancel-memory").addEventListener("click", () => $("#memory-form").classList.add("hidden"));
  $("#memory-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api("/memories", { method: "POST", body: JSON.stringify({
      kind: $("#memory-kind").value, scope: $("#memory-scope").value, content: $("#memory-content").value.trim(),
      source: { conversation_id: $("#memory-conversation-id").value, user_message_id: $("#memory-user-id").value, assistant_message_id: $("#memory-assistant-id").value },
    }) });
    $("#memory-form").classList.add("hidden"); await loadMemories(); toast("记忆已保存");
  });
  $("#toggle-knowledge-form").addEventListener("click", () => $("#knowledge-form").classList.toggle("hidden"));
  $("#cancel-knowledge").addEventListener("click", () => $("#knowledge-form").classList.add("hidden"));
  $("#knowledge-form").addEventListener("submit", async (event) => {
    event.preventDefault(); await api("/knowledge", { method: "POST", body: JSON.stringify({ title: $("#knowledge-title").value.trim(), text: $("#knowledge-text").value, metadata: {} }) });
    event.target.reset(); event.target.classList.add("hidden"); await loadKnowledge(); toast("知识正文已保存");
  });
  $("#open-conversations").addEventListener("click", () => { $("#conversation-drawer").classList.add("open"); $("#scrim").classList.add("show"); });
  $("#open-inspector").addEventListener("click", () => { $("#inspector-drawer").classList.add("open"); $("#scrim").classList.add("show"); });
  $("#close-inspector").addEventListener("click", closeDrawers);
  $("#scrim").addEventListener("click", closeDrawers);
}

async function init() {
  bindEvents();
  $("#message-input").disabled = true;
  $("#send-message").disabled = true;
  renderComposerMode();
  try {
    await Promise.all([loadConversations(), loadProfiles()]);
    if (state.conversations.length) await selectConversation(state.conversations[0]);
  } catch (error) {
    toast(`${error.message}。请确认 PostgreSQL 与服务已启动。`, true);
  }
}

init();
