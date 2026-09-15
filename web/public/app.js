/* Universal Agent chat UI — vanilla JS over the agentd API via /api proxy. */

const $ = (id) => document.querySelector(`#${CSS.escape(id)}`);

const state = {
  sessions: [],
  selected: null, // session view (GET /v1/sessions/{id})
  events: [], // transcript events for the selected session
  eventSource: null,
  searchQuery: "",
};

/* ------------------------------------------------------------------ */
/* API helpers (proxied to agentd by the Node server)                  */
/* ------------------------------------------------------------------ */

async function api(path, options) {
  const response = await fetch(`/api${path}`, options);
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { raw: text };
  }
  if (!response.ok) {
    const message =
      payload && payload.error
        ? payload.error.message
        : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

const get = (path) => api(path);
const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

const EVENT_RENDER = {
  GoalCreated: () => null, // the goal bubble already covers this
  TaskCreated: () => null,
  TaskStarted: () => null,
  StateUpdated: () => null,
  DomainActivated: () => null,
  SessionResumed: () => entry("system", "▶ session resumed"),
  Heartbeat: () => null,
  DecisionGenerated: (data) =>
    entry("agent", data.reason || "thinking…", "decision"),
  DecisionRejected: (data) =>
    entry("err", `decision rejected: ${data.reason || ""}`),
  DecisionValidated: () => null,
  ActionStarted: (data) =>
    entry("agent", `⚙️ ${data.capability} → ${data.target || ""}`, "action"),
  ActionCompleted: (data) =>
    entry("agent", `✔ ${data.capability || "action"} completed`, "action"),
  PolicyChecked: (data) => {
    if (data.effect === "deny")
      return entry("err", `⛔ policy denied ${data.capability || ""}`);
    if (data.effect === "require_confirmation") return null; // confirmation banner covers it
    return null;
  },
  EvidenceRecorded: (data) =>
    entry(
      "agent",
      `🔎 evidence: ${data.claim || data.evidence_id || ""}`,
      "evidence",
    ),
  RecoveryPlanned: (data) =>
    entry("system", `↻ recovery: ${data.strategy || ""}`),
  RecoveryExhausted: () => entry("err", "recovery exhausted"),
  GoalCompleted: () => entry("system", "✓ goal completed"),
  GoalFailed: (data) => entry("err", `✗ goal failed: ${data.reason || ""}`),
  SessionPaused: (data) => entry("system", `⏸ paused: ${data.reason || ""}`),
};

function entry(kind, text, tag) {
  return { kind, text, tag };
}

function eventToEntry(event) {
  const renderer = EVENT_RENDER[event.type];
  const result = renderer ? renderer(event.data || {}) : null;
  if (result === null) return null;
  return result;
}

function goalBubble(description) {
  return { kind: "user", text: description };
}

function renderTranscript() {
  const box = $("transcript");
  box.replaceChildren();
  const session = state.selected;

  if (!session) {
    const empty = document.createElement("div");
    empty.className = "bubble system";
    empty.textContent = "Select a chat on the left, or create a new one.";
    box.append(empty);
    return;
  }

  for (const item of state.items) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${item.kind}`;
    if (item.tag) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = item.tag;
      bubble.append(tag);
    }
    bubble.append(document.createTextNode(item.text));
    box.append(bubble);
  }
  box.scrollTop = box.scrollHeight;
}

function renderSessionList() {
  const list = $("session-list");
  list.replaceChildren();
  const query = state.searchQuery.trim().toLowerCase();
  const visible = query
    ? state.sessions.filter(
        (s) =>
          (s.goal_description || "").toLowerCase().includes(query) ||
          String(s.session_id).toLowerCase().includes(query),
      )
    : state.sessions;
  for (const session of visible) {
    const li = document.createElement("li");
    li.dataset.sessionId = session.session_id;
    if (state.selected && session.session_id === state.selected.session_id) {
      li.classList.add("active");
    }
    const goal = document.createElement("div");
    goal.className = "goal";
    goal.textContent = session.goal_description || session.session_id;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${session.goal_status}${session.created_at ? ` · ${session.created_at.slice(0, 19).replace("T", " ")}` : ""}`;
    li.append(goal, meta);
    li.addEventListener("click", () => selectSession(session.session_id));
    list.append(li);
  }
}

function renderChatHeader() {
  const session = state.selected;
  const title = $("chat-title");
  const badge = $("chat-status");
  if (session) {
    title.textContent = session.goal_description || session.session_id;
    badge.textContent = session.goal_status;
    badge.className = `status-badge ${session.goal_status}`;
  } else {
    title.textContent = "Select or create a chat";
    badge.classList.add("hidden");
  }
  $("btn-pause").classList.toggle("hidden", !isRunning(session));
  $("btn-cancel").classList.toggle("hidden", !isLive(session));
}

function isLive(session) {
  return Boolean(
    session && ["running", "waiting"].includes(String(session.goal_status)),
  );
}

function isRunning(session) {
  return Boolean(session && String(session.goal_status) === "running");
}

function renderConfirmation() {
  const box = $("confirmation");
  const session = state.selected;
  const pending = session && session.pending_action;
  if (!pending || String(session.goal_status) !== "waiting") {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  $("confirm-title").textContent =
    `Confirm ${pending.capability} on ${pending.target || "target"}?`;
  $("confirm-detail").textContent = pending.arguments
    ? `arguments: ${JSON.stringify(pending.arguments)}`
    : "";
}

function render() {
  renderSessionList();
  renderChatHeader();
  renderTranscript();
  renderConfirmation();
}

/* ------------------------------------------------------------------ */
/* Data loading                                                        */
/* ------------------------------------------------------------------ */

function rebuildItems() {
  const session = state.selected;
  const items = [];
  if (session) items.push(goalBubble(session.goal_description));
  for (const event of state.events) {
    const mapped = eventToEntry(event);
    if (mapped) items.push(mapped);
  }
  state.items = items;
}

async function loadSessions() {
  const batch = await get("/v1/sessions");
  state.sessions = (batch && batch.sessions) || [];
  if (state.selected) {
    const match = state.sessions.find(
      (s) => s.session_id === state.selected.session_id,
    );
    if (match) state.selected = match;
  }
  render();
}

async function selectSession(sessionId) {
  closeEventStream();
  const [session, batch] = await Promise.all([
    get(`/v1/sessions/${sessionId}`),
    get(`/v1/sessions/${sessionId}/events`),
  ]);
  state.selected = session;
  state.events = (batch && batch.events) || [];
  rebuildItems();
  render();
  openEventStream(sessionId);
}

function closeEventStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

function openEventStream(sessionId) {
  closeEventStream();
  const source = new EventSource(
    `/api/v1/sessions/${sessionId}/events/stream?wait=true`,
  );
  source.onmessage = (message) => {
    let event = null;
    try {
      event = JSON.parse(message.data);
    } catch {
      return;
    }
    if (!event || !event.type) return;
    state.events.push(event);
    rebuildItems();
    renderTranscript();
    // A confirmation or a terminal state changes the header/banner; refresh
    // the authoritative session view cheaply.
    if (
      event.type === "ConfirmationRequired" ||
      event.type === "GoalCompleted" ||
      event.type === "GoalFailed"
    ) {
      refreshSelected();
    }
  };
  state.eventSource = source;
}

async function refreshSelected() {
  const session = state.selected;
  if (!session) return;
  state.selected = await get(`/v1/sessions/${session.session_id}`);
  await loadSessions();
  rebuildItems();
  render();
}

/* ------------------------------------------------------------------ */
/* Actions                                                             */
/* ------------------------------------------------------------------ */

async function createChat(goalText) {
  const run = await post("/v1/sessions", {
    goal: { description: goalText, success_criteria: [] },
    compile_goal: true,
    timeout_seconds: 900,
  });
  const sessionId =
    (run.session && run.session.session_id) ||
    (run.result && run.result.session_id);
  await loadSessions();
  if (sessionId) await selectSession(sessionId);
}

async function resumeSelected(confirmed) {
  const session = state.selected;
  if (!session) return;
  await post(`/v1/sessions/${session.session_id}/resume`, { confirmed });
  await refreshSelected();
}

async function pauseSelected() {
  if (!state.selected) return;
  await post(`/v1/sessions/${state.selected.session_id}/pause`, {});
  await refreshSelected();
}

async function cancelSelected() {
  if (!state.selected) return;
  await post(`/v1/sessions/${state.selected.session_id}/cancel`, {});
  await refreshSelected();
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */

$("new-chat").addEventListener("click", () => {
  state.selected = null;
  state.events = [];
  state.items = [];
  closeEventStream();
  render();
  $("goal-input").focus();
});

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("goal-input");
  const goalText = input.value.trim();
  if (!goalText) return;
  $("btn-send").disabled = true;
  try {
    await createChat(goalText);
    input.value = "";
  } catch (error) {
    state.items.push({ kind: "err", text: String(error.message || error) });
    renderTranscript();
  } finally {
    $("btn-send").disabled = false;
  }
});

$("session-search").addEventListener("input", (event) => {
  state.searchQuery = event.target.value || "";
  renderSessionList();
});

// Live multi-session view: refresh the sidebar periodically so sessions
// started elsewhere (CLI, another browser) appear without a manual reload.
setInterval(() => {
  loadSessions().catch(() => {});
}, 5000);

$("btn-confirm").addEventListener("click", () => resumeSelected(true));
$("btn-reject").addEventListener("click", () => resumeSelected(false));
$("btn-pause").addEventListener("click", pauseSelected);
$("btn-cancel").addEventListener("click", cancelSelected);

async function boot() {
  try {
    const config = await get("/config");
    const footer = $("server-status");
    if (config.server_configured) {
      footer.textContent = `server: ${config.agentd_url}`;
      footer.className = "sidebar-footer ok";
    } else {
      footer.textContent = "server: not configured (set AGENTD_URL)";
      footer.className = "sidebar-footer bad";
    }
  } catch {
    /* non-fatal */
  }
  await loadSessions();
}

boot();
