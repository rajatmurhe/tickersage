"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, streamChat } from "@/lib/api";
import { STATUS_PILL, ms, safeUrl } from "@/lib/format";
import type { ChatResponse, StepEvent } from "@/lib/types";
import Markdown from "./Markdown";
import TracePanel from "./TracePanel";

const EXAMPLES = [
  "What's BTC trading at, and how much did it move in 24 hours?",
  "Compare ETH and SOL volume and volatility over the last 7 days.",
  "Why might BTC have dropped today?",
  "What is a funding rate?",
  "What's the spread on ETH right now?",
  "Should I buy SOL now?",
];

interface Turn {
  id: number;
  question: string;
  steps: StepEvent[];
  text: string;
  response: ChatResponse | null;
  error: string | null;
}

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const nextId = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const follow = useRef(false);
  const lastHeight = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => () => abort.current?.abort(), []);

  // Follow the stream while the reader is at the bottom of what was there before this update;
  // if they have scrolled up to read, leave them there. A new question always jumps down.
  useEffect(() => {
    const height = document.documentElement.scrollHeight;
    const wasAtBottom = window.scrollY + window.innerHeight >= lastHeight.current - 160;
    if (follow.current || wasAtBottom) window.scrollTo({ top: height });
    follow.current = false;
    lastHeight.current = height;
  }, [turns]);

  async function ask(question: string) {
    const id = nextId.current++;
    const update = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));
    follow.current = true;
    setTurns((ts) => [...ts, { id, question, steps: [], text: "", response: null, error: null }]);
    setBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      await streamChat(
        question,
        {
          onStep: (step) => update((t) => ({ ...t, steps: [...t.steps, step] })),
          onToken: (delta) => update((t) => ({ ...t, text: t.text + delta })),
          onDone: (response) => update((t) => ({ ...t, text: response.answer, response })),
        },
        controller.signal,
      );
    } catch (err) {
      if (controller.signal.aborted) return;
      const message = err instanceof ApiError ? err.message : "Something went wrong reading the answer.";
      update((t) => ({ ...t, error: message }));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        if (window.matchMedia("(pointer: fine)").matches) inputRef.current?.focus();
      }
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    void ask(q);
  }

  return (
    <>
      <p className="intro">
        Ask about prices, moves, volatility, order books, news sentiment or crypto concepts. Every number is computed in
        Python from public exchange data and checked against the tool output before you see it. No trading, no advice.
      </p>
      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button key={ex} type="button" disabled={busy} onClick={() => void ask(ex)}>
            {ex}
          </button>
        ))}
      </div>

      <div className="log">
        {turns.map((t) => (
          <div key={t.id}>
            <div className="msg user">
              <div className="bubble">{t.question}</div>
            </div>
            <div className="msg bot">
              <BotBubble turn={t} />
            </div>
          </div>
        ))}
      </div>

      <form className="ask" onSubmit={onSubmit}>
        <div className="inner">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            autoComplete="off"
            maxLength={1000}
            placeholder="e.g. Compare ETH and SOL over the last 7 days"
            aria-label="Your question"
            required
          />
          <button type="submit" disabled={busy || !input.trim()}>
            {busy ? "…" : "Ask"}
          </button>
        </div>
      </form>
    </>
  );
}

function BotBubble({ turn }: { turn: Turn }) {
  const r = turn.response;
  const working = !r && !turn.error;
  const anchor = r ? `src-${r.request_id}` : undefined;
  const citationIds = r ? new Set(r.citations.map((c) => c.id)) : undefined;
  const pill = r ? STATUS_PILL[r.status] ?? { label: r.status, tone: "" } : null;

  return (
    <div className="bubble" aria-busy={working}>
      {working && (
        <ol className="progress" aria-label="Progress">
          {turn.steps.map((s, i) => (
            <li key={i} className="done">
              {s.label}
              {s.detail.latency_ms != null && <span className="muted"> · {ms(s.detail.latency_ms)}</span>}
              {s.detail.model && <span className="muted"> · {s.detail.model}</span>}
            </li>
          ))}
          {!turn.text && <li className="running">{turn.steps.length ? "Working" : "Starting"}</li>}
        </ol>
      )}

      {turn.text && (
        <div className="answer">
          <Markdown text={turn.text} citationIds={citationIds} anchor={anchor} />
        </div>
      )}

      {turn.error && <p className="fail">{turn.error}</p>}

      {r && pill && (
        <>
          <div className="meta">
            <span className={`pill ${pill.tone}`}>{pill.label}</span>
            {r.trace.models.length > 0 && <span className="pill">{r.trace.models.join(" → ")}</span>}
          </div>
          {r.citations.length > 0 && (
            <div className="citations">
              Sources
              <ol>
                {r.citations.map((c) => {
                  const href = safeUrl(c.url);
                  return (
                    <li key={c.id} id={`${anchor}-${c.id}`} value={c.id}>
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer">
                          {c.title}
                        </a>
                      ) : (
                        c.title
                      )}
                    </li>
                  );
                })}
              </ol>
            </div>
          )}
          <TracePanel trace={r.trace} />
        </>
      )}
    </div>
  );
}
