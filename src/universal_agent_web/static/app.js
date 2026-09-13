/* Universal Agent Web Console — pure HTTP API client (no runtime state). */

const $view = document.getElementById("view");
const $heroStatus = document.getElementById("hero-status");
const $nav = document.getElementById("nav");

const NAV_LINKS = [
  ["#/sessions", "Sessions"],
  ["#/settings", "Settings"],
  ["#/doctor", "Doctor"],
  ["#/distributed", "Distributed"],
  ["#/multi-agent", "Multi-Agent"],
  ["#/evaluations", "Evaluations"],
  ["#/profiles", "Profiles"],
  ["#/domain-packages", "Domain Packages"],
];

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      payload && payload.error
        ? payload.error.message
        : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(
      child instanceof Node ? child : document.createTextNode(String(child)),
    );
  }
  return node;
}

function panel(title, ...content) {
  return el("section", { class: "panel" }, el("h2", {}, title), ...content);
}

function table(headers, rows) {
  if (!rows.length) return el("p", { class: "muted" }, "none");
  const head = el("tr", {}, ...headers.map((header) => el("th", {}, header)));
  const body = rows.map((row) =>
    el(
      "tr",
      {},
      ...row.map((cell) =>
        el("td", {}, cell === null || cell === undefined ? "—" : cell),
      ),
    ),
  );
  return el(
    "div",
    { class: "table-wrap" },
    el("table", {}, el("thead", {}, head), el("tbody", {}, body)),
  );
}

function pills(items) {
  return el(
    "div",
    { class: "status" },
    ...items.map(([label, value]) =>
      el("span", { class: `pill ${value ? "ok" : "warn"}` }, label),
    ),
  );
}

function renderError(error) {
  $view.replaceChildren(
    panel("Error", el("p", { class: "muted" }, String(error.message || error))),
  );
}

function linked(text, href) {
  return el("a", { href }, text);
}

/* ---------- views ---------- */

async function viewOverview() {
  const [health, ready, metrics, cost, doctor, domains] = await Promise.all([
    api("/health"),
    api("/ready"),
    api("/v1/metrics"),
    api("/v1/cost"),
    api("/v1/doctor"),
    api("/v1/domains"),
  ]);
  $heroStatus.textContent = `health=${health.status} · ready=${ready.ready} · domains=${ready.domain_count}`;
  const metricCards = el(
    "div",
    { class: "grid cards" },
    ...[
      ["Sessions", metrics.session_count],
      ["Active", metrics.active_session_count],
      ["Completed Goals", metrics.completed_goal_count],
      ["Failed Goals", metrics.failed_goal_count],
      ["Events", metrics.event_count],
      ["Model Calls", metrics.model_call_count],
    ].map(([label, value]) =>
      el(
        "div",
        { class: "card" },
        el("span", {}, label),
        el("strong", {}, String(value)),
      ),
    ),
  );
  $view.replaceChildren(
    panel(
      "Runtime health",
      pills([
        ["health", health.status === "ok"],
        ["ready", ready.ready === true],
      ]),
      metricCards,
    ),
    panel(
      "Doctor",
      table(
        ["Check", "Status", "Message"],
        (doctor.checks || []).map((check) => [
          check.name,
          check.status,
          check.message,
        ]),
      ),
    ),
    panel(
      "Active domains",
      table(
        ["Name", "Version"],
        (domains.domains || []).map((domain) => [domain.name, domain.version]),
      ),
    ),
    panel(
      "Model cost",
      table(
        ["Model", "Calls", "Tokens", "Cost (µ)"],
        (cost.by_model || []).map((item) => [
          item.model,
          item.call_count,
          item.total_tokens,
          item.estimated_cost_micros,
        ]),
      ),
    ),
  );
}

async function viewSessions() {
  const payload = await api("/v1/sessions?limit=25");
  $view.replaceChildren(
    panel(
      "Sessions",
      table(
        ["Session", "Goal status", "Goal"],
        (payload.sessions || []).map((session) => [
          linked(session.session_id, `#/sessions/${session.session_id}`),
          session.goal_status,
          session.goal_description,
        ]),
      ),
    ),
  );
}

async function actionButton(label, path, body) {
  return el(
    "button",
    {
      class: "action",
      onclick: async () => {
        try {
          await api(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
          });
          await route();
        } catch (error) {
          renderError(error);
        }
      },
    },
    label,
  );
}

function evidenceDrilldownView(sessionId) {
  const container = el("div", {});
  const controls = el("div", {});
  const list = el("div", {});
  container.append(controls, list);
  api(`/console/sessions/${sessionId}/evidence-drilldown`)
    .then((payload) => {
      const records = payload.evidence || [];
      if (records.length === 0) {
        list.replaceChildren(
          el("p", {}, "No evidence recorded for this session."),
        );
        return;
      }
      const subjectFilter = selectFilter(
        "All subjects",
        payload.subjects || [],
        (record) => renderRecords(records, record.value),
      );
      const domainFilter = selectFilter(
        "All domains",
        payload.domains || [],
        (record) => renderRecords(records, subjectFilter.value, record.value),
      );
      controls.replaceChildren(subjectFilter, " ", domainFilter);
      function renderRecords(all, subject, domain) {
        const visible = all.filter(
          (item) =>
            (!subject || item.subject === subject) &&
            (!domain || item.domain === domain),
        );
        list.replaceChildren(
          table(
            ["Evidence", "Claim", "Confidence", "Domain", "Action", "Source"],
            visible.map((item) => [
              item.evidence_id,
              item.claim,
              item.confidence,
              item.domain || "-",
              item.action_id,
              item.source,
            ]),
          ),
        );
      }
      renderRecords(records, "", "");
      subjectFilter.addEventListener("change", () =>
        renderRecords(records, subjectFilter.value, domainFilter.value),
      );
      domainFilter.addEventListener("change", () =>
        renderRecords(records, subjectFilter.value, domainFilter.value),
      );
    })
    .catch((error) => {
      list.replaceChildren(
        el("p", {}, `Evidence drill-down unavailable: ${error.message}`),
      );
    });
  return container;
}

function selectFilter(label, values, _onChange) {
  const select = document.createElement("select");
  select.append(el("option", { value: "" }, label));
  for (const value of values || []) {
    select.append(el("option", { value }, value));
  }
  return select;
}

function worldExplorerView(sessionId) {
  const container = el("div", {});
  const summary = el("p", {}, "Loading world explorer…");
  const detail = el("div", {});
  container.append(summary, detail);
  api(`/console/sessions/${sessionId}/world-explorer`)
    .then((payload) => {
      summary.textContent =
        `${payload.entity_count} entities · ${payload.relation_count} relations · ` +
        `${payload.conflict_count} conflicts`;
      detail.replaceChildren(
        table(
          ["Entity", "Kind", "Domains", "Out", "In"],
          (payload.entities || []).map((entity) => [
            linked(
              entity.entity_id,
              `#/sessions/${sessionId}/world/${encodeURIComponent(entity.entity_id)}`,
            ),
            entity.kind,
            (entity.contributing_domains || []).join(", ") || "-",
            (entity.outgoing_relations || []).length,
            (entity.incoming_relations || []).length,
          ]),
        ),
        conflictsPanel(payload.conflicts || []),
      );
    })
    .catch((error) => {
      summary.textContent = `World explorer unavailable: ${error.message}`;
    });
  return container;
}

function conflictsPanel(conflicts) {
  if (conflicts.length === 0)
    return el("p", {}, "No cross-domain fact conflicts.");
  return panel(
    "Cross-domain conflicts",
    table(
      ["Subject", "Claim", "Selected value", "Candidates"],
      conflicts.map((conflict) => [
        conflict.subject,
        conflict.claim,
        String(conflict.current_value),
        (conflict.candidates || [])
          .map(
            (candidate) =>
              `${candidate.value} (${candidate.domain || candidate.source})`,
          )
          .join(", "),
      ]),
    ),
  );
}

function timelineView(sessionId) {
  const container = el("div", {});
  const stepsList = el("ol", { class: "timeline" });
  container.append(
    el(
      "p",
      {},
      "Correlated steps: decision → policy → action → observation → evidence.",
    ),
    stepsList,
  );
  api(`/console/sessions/${sessionId}/timeline`)
    .then((payload) => {
      const steps = payload.steps || [];
      if (steps.length === 0) {
        stepsList.append(el("li", {}, "No execution steps recorded."));
        return;
      }
      for (const step of steps) {
        const facts = [];
        if (step.policy_effect) facts.push(`policy=${step.policy_effect}`);
        if (step.observation_id)
          facts.push(`observation=${step.observation_id}`);
        if ((step.evidence_ids || []).length > 0) {
          facts.push(`evidence=${step.evidence_ids.join(", ")}`);
        }
        stepsList.append(
          el(
            "li",
            { class: "timeline-step" },
            el("strong", {}, step.label),
            el(
              "span",
              { class: "timeline-meta" },
              ` · action=${step.action_id || "-"} · ${facts.join(" · ") || step.event_count + " events"}`,
            ),
          ),
        );
      }
    })
    .catch((error) => {
      stepsList.replaceChildren(
        el("li", {}, `Timeline unavailable: ${error.message}`),
      );
    });
  return container;
}

async function viewSessionDetail(sessionId) {
  const [session, events, evidence, world] = await Promise.all([
    api(`/v1/sessions/${sessionId}`),
    api(`/v1/sessions/${sessionId}/events?limit=15`),
    api(`/v1/sessions/${sessionId}/evidence`),
    api(`/v1/sessions/${sessionId}/world`),
  ]);
  const goalStatus = session.goal_status;
  const actions = el(
    "div",
    { class: "status" },
    await actionButton("Pause", `/v1/sessions/${sessionId}/pause`, {
      reason: "paused from web console",
    }),
    await actionButton("Resume", `/v1/sessions/${sessionId}/resume`, {
      confirmed: true,
    }),
    await actionButton("Cancel", `/v1/sessions/${sessionId}/cancel`, {
      reason: "cancelled from web console",
    }),
  );
  $view.replaceChildren(
    panel(
      `Session ${sessionId}`,
      el("p", {}, `goal: ${session.goal_description}`),
      el("p", {}, `status: ${goalStatus} · iteration: ${session.iteration}`),
      actions,
      table(
        ["Task", "Status", "Required criteria"],
        (session.tasks || []).map((task) => [
          task.description,
          task.status,
          (task.required_criteria || []).join(", "),
        ]),
      ),
      panel(
        "Recent events",
        table(
          ["Type", "Time"],
          (events.events || []).map((event) => [
            event.type,
            new Date(event.occurred_at).toLocaleTimeString(),
          ]),
        ),
      ),
      panel("Execution timeline", timelineView(sessionId)),
      panel("World explorer", worldExplorerView(sessionId)),
      panel(
        "Evidence",
        table(
          ["Subject", "Claim", "Value"],
          (evidence.evidence || []).map((item) => [
            item.subject,
            item.claim,
            String(item.value),
          ]),
        ),
      ),
      panel("Evidence drill-down", evidenceDrilldownView(sessionId)),
      panel(
        "World facts",
        table(
          ["Subject", "Claim", "Value", "Confidence"],
          (world.world_facts || []).map((fact) => [
            fact.subject,
            fact.claim,
            String(fact.value),
            fact.confidence,
          ]),
        ),
      ),
    ),
  );
}

async function viewSettings() {
  const config = await api("/v1/config");
  $view.replaceChildren(
    panel(
      "Runtime configuration",
      el("pre", { class: "code" }, JSON.stringify(config, null, 2)),
    ),
  );
}

async function viewDoctor() {
  const doctor = await api("/v1/doctor");
  $view.replaceChildren(
    panel(
      "Doctor",
      table(
        ["Check", "Status", "Message"],
        (doctor.checks || []).map((check) => [
          check.name,
          check.status,
          check.message,
        ]),
      ),
    ),
  );
}

async function viewDistributed() {
  const [snapshot, health] = await Promise.all([
    api("/v1/distributed/snapshot"),
    api("/v1/distributed/health"),
  ]);
  $view.replaceChildren(
    panel(
      "Distributed health",
      el("pre", { class: "code" }, JSON.stringify(health, null, 2)),
    ),
    panel(
      "Snapshot",
      el("pre", { class: "code" }, JSON.stringify(snapshot, null, 2)),
    ),
  );
}

async function viewMultiAgent() {
  const payload = await api("/v1/multi-agent");
  $view.replaceChildren(
    panel(
      "Multi-Agent",
      el("pre", { class: "code" }, JSON.stringify(payload, null, 2)),
    ),
  );
}

async function viewProfiles() {
  const payload = await api("/v1/profiles");
  $view.replaceChildren(
    panel(
      "Profiles",
      table(
        ["Name", "Version", "Domains"],
        (payload.profiles || []).map((profile) => [
          profile.name,
          profile.version,
          (profile.domains || [])
            .map((domain) => `${domain.name}@${domain.version}`)
            .join(", "),
        ]),
      ),
    ),
  );
}

async function viewDomainPackages() {
  const payload = await api("/v1/domain-packages");
  $view.replaceChildren(
    panel(
      "Domain packages",
      table(
        ["Name", "Version", "Tags"],
        (payload.domain_packages || []).map((item) => [
          item.name,
          item.version,
          (item.tags || []).join(", "),
        ]),
      ),
    ),
  );
}

async function viewEvaluations() {
  const payload = await api("/v1/evaluations");
  if (payload.status === "not_configured") {
    $view.replaceChildren(
      panel(
        "Evaluations",
        el(
          "p",
          { class: "muted" },
          "evaluation report dir is not configured for this agentd instance",
        ),
      ),
    );
    return;
  }
  $view.replaceChildren(
    panel(
      "Evaluation reports",
      table(
        ["Suite", "Passed", "Scenarios", "Gate"],
        (payload.reports || []).map((report) => [
          report.suite_name,
          String(report.passed),
          report.scenario_count,
          String(report.gate_passed),
        ]),
      ),
    ),
  );
}

const ROUTES = [
  [/^#\/?$|^#\/console$/, () => viewOverview()],
  [/^#\/sessions$/, () => viewSessions()],
  [
    /^#\/sessions\/([^/]+)$/,
    (match) => viewSessionDetail(decodeURIComponent(match[1])),
  ],
  [/^#\/settings$/, () => viewSettings()],
  [/^#\/doctor$/, () => viewDoctor()],
  [/^#\/distributed$/, () => viewDistributed()],
  [/^#\/multi-agent$/, () => viewMultiAgent()],
  [/^#\/profiles$/, () => viewProfiles()],
  [/^#\/domain-packages$/, () => viewDomainPackages()],
  [/^#\/evaluations$/, () => viewEvaluations()],
];

async function route() {
  const hash = window.location.hash || "#/";
  for (const [pattern, handler] of ROUTES) {
    const match = hash.match(pattern);
    if (match) {
      try {
        await handler(match);
      } catch (error) {
        renderError(error);
      }
      return;
    }
  }
  renderError(`unknown route: ${hash}`);
}

window.addEventListener("hashchange", route);

(async function init() {
  for (const [href, label] of NAV_LINKS) {
    $nav.append(el("a", { href, class: "nav-link" }, label));
  }
  await route();
})();
