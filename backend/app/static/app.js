"use strict";

const EXAMPLES = [
  "What's BTC trading at, and how much did it move in 24 hours?",
  "Compare ETH and SOL volume and volatility over the last 7 days.",
  "Why might BTC have dropped today?",
  "What is a funding rate?",
  "What's the spread on ETH right now?",
  "Should I buy SOL now?",
];

const $ = (sel) => document.querySelector(sel);
const log = $("#log");

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Small markdown subset: paragraphs, **bold**, _italic_, lists, pipe tables, [n] citations. Escapes first.
function md(src) {
  const inline = (t) => esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\s)_(.+?)_(?=\s|$|[.,])/g, "$1<em>$2</em>")
    .replace(/\[(\d+)\]/g, '<sup class="mono">[$1]</sup>');
  const blocks = src.trim().split(/\n{2,}/);
  return blocks.map((block) => {
    const lines = block.split("\n");
    if (lines.length >= 2 && /^\|.*\|$/.test(lines[0].trim()) && /^\|[-| :]+\|$/.test(lines[1].trim())) {
      const cells = (l) => l.trim().slice(1, -1).split("|").map((c) => c.trim());
      const head = cells(lines[0]).map((c) => `<th>${inline(c)}</th>`).join("");
      const body = lines.slice(2).map((l) => `<tr>${cells(l).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("");
      return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }
    const listStart = lines.findIndex((l) => /^\s*- /.test(l));
    if (listStart >= 0) {
      const before = lines.slice(0, listStart).join(" ");
      const items = lines.slice(listStart).filter((l) => /^\s*- /.test(l)).map((l) => `<li>${inline(l.replace(/^\s*- /, ""))}</li>`);
      return (before ? `<p>${inline(before)}</p>` : "") + `<ul>${items.join("")}</ul>`;
    }
    return `<p>${inline(lines.join(" "))}</p>`;
  }).join("");
}

const STATUS_PILL = {
  ok: ["verified", ""], refused: ["refused", "warn"], out_of_scope: ["out of scope", "warn"],
  clarify: ["needs detail", "warn"], safe_fallback: ["safe fallback", "warn"], error: ["error", "bad"],
};

function money(x) { return x ? `$${x.toFixed(6)}` : "$0"; }

function traceHtml(r) {
  const t = r.trace;
  const faith = t.faithfulness == null ? "n/a" : `${Math.round(t.faithfulness * 100)}%`;
  const stats = [
    ["intent", t.intent ?? "n/a"], ["latency", `${Math.round(t.latency_ms)} ms`], ["cost", money(t.cost_usd)],
    ["tokens in/out", `${t.input_tokens} / ${t.output_tokens}`], ["number faithfulness", faith],
    ["models", t.models.length ? t.models.join(", ") : "none (offline)"], ["provider fallback", t.fell_back ? "yes" : "no"],
  ].map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
  const steps = t.steps.map((s) => `<tr><td>${esc(s.name)}</td><td>${s.latency_ms} ms</td><td>${esc(s.model ?? "")}</td>
    <td>${s.input_tokens || ""}${s.output_tokens ? " / " + s.output_tokens : ""}</td><td>${s.cost_usd ? money(s.cost_usd) : ""}</td>
    <td class="mono">${esc(JSON.stringify(s.detail))}</td></tr>`).join("");
  const tools = t.tools.map((x) => `<tr><td>${esc(x.tool)}</td><td class="mono">${esc(JSON.stringify(x.args))}</td>
    <td>${x.ok ? '<span class="ok">ok</span>' : `<span class="fail">${esc(x.error)}</span>`}</td>
    <td>${x.cached ? "cache" : x.latency_ms + " ms"}</td><td>${esc(x.source ?? "")}</td></tr>`).join("");
  const unsupported = t.unsupported_numbers.length ? `<p class="fail">Unsupported numbers: ${esc(t.unsupported_numbers.join(", "))}</p>` : "";
  return `<details class="trace"><summary>Trace · ${t.tools.length} tool call(s) · ${Math.round(t.latency_ms)} ms · ${money(t.cost_usd)}</summary>
    <div class="trace-grid">${stats}</div>${unsupported}
    ${t.tools.length ? `<table><thead><tr><th>tool</th><th>args</th><th>result</th><th>time</th><th>source</th></tr></thead><tbody>${tools}</tbody></table>` : ""}
    <table><thead><tr><th>step</th><th>time</th><th>model</th><th>tokens</th><th>cost</th><th>detail</th></tr></thead><tbody>${steps}</tbody></table>
    <div class="mono">request ${esc(t.request_id)}${t.refusal_reason ? " · " + esc(t.refusal_reason) : ""}</div></details>`;
}

function citationsHtml(r) {
  if (!r.citations.length) return "";
  const items = r.citations.map((c) => `<li>${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a>` : esc(c.title)}</li>`).join("");
  return `<div class="citations">Sources<ol>${items}</ol></div>`;
}

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  log.appendChild(el);
}

async function ask(question) {
  addUser(question);
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `<div class="bubble"><div class="progress"></div><div class="answer"></div></div>`;
  log.appendChild(el);
  const progress = el.querySelector(".progress");
  const answer = el.querySelector(".answer");
  el.scrollIntoView({ behavior: "smooth", block: "end" });
  let text = "";

  const resp = await fetch("/api/chat/stream", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }),
  });
  if (!resp.ok || !resp.body) {
    answer.innerHTML = `<p class="fail">Request failed (${resp.status}).</p>`;
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      let event = "message", data = "";
      for (const line of raw.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "step") {
        const row = document.createElement("div");
        row.className = "done";
        row.textContent = payload.label;
        progress.appendChild(row);
      } else if (event === "token") {
        text += payload.delta;
        answer.innerHTML = md(text);
      } else if (event === "done") {
        progress.remove();
        answer.innerHTML = md(payload.answer);
        const [label, cls] = STATUS_PILL[payload.status] ?? [payload.status, ""];
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.innerHTML = `<span class="pill ${cls}">${esc(label)}</span>` +
          (payload.trace.models.length ? `<span class="pill">${esc(payload.trace.models.join(" → "))}</span>` : "");
        el.querySelector(".bubble").insertAdjacentHTML("beforeend", meta.outerHTML + citationsHtml(payload) + traceHtml(payload));
      }
    }
  }
  el.scrollIntoView({ behavior: "smooth", block: "end" });
}

for (const ex of EXAMPLES) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = ex;
  b.onclick = () => submit(ex);
  $("#examples").appendChild(b);
}

async function submit(question) {
  const btn = $("#send");
  btn.disabled = true;
  try { await ask(question); } finally { btn.disabled = false; $("#q").focus(); }
}

$("#ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#q").value = "";
  submit(q);
});

fetch("/api/health").then((r) => r.json()).then((h) => {
  $("#mode").textContent = `LLM ${h.llm} · tools ${h.tool_mode}`;
  if (h.llm !== "live") $("#mode").classList.add("warn");
}).catch(() => {});
