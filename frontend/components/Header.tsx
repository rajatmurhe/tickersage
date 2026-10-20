"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { alertsApi } from "@/lib/alerts";
import { API_URL, getJson } from "@/lib/api";
import type { Health } from "@/lib/types";

const UNREAD_REFRESH_MS = 60_000;

export default function Header() {
  const path = usePathname();
  const [health, setHealth] = useState<Health | null | "down">(null);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getJson<Health>("/api/health", controller.signal)
      .then(setHealth)
      .catch(() => {
        if (!controller.signal.aborted) setHealth("down");
      });
    return () => controller.abort();
  }, []);

  // The alert feed's unread count, as an in-app notification badge on every page.
  useEffect(() => {
    let live = true;
    const poll = () =>
      alertsApi<{ unread: number }>("/api/alerts/status")
        .then((s) => live && setUnread(s.unread))
        .catch(() => undefined);
    void poll();
    const id = window.setInterval(poll, UNREAD_REFRESH_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [path]);

  let mode = <span className="pill muted-pill">connecting…</span>;
  if (health === "down") mode = <span className="pill bad" title={API_URL}>API offline</span>;
  else if (health)
    mode = (
      <span className={`pill ${health.llm === "live" ? "" : "warn"}`} title={`routing ${health.routing_mode}`}>
        LLM {health.llm} · tools {health.tool_mode}
      </span>
    );

  return (
    <header className="top">
      <div className="brand">
        <Link href="/" className="brand-name">
          TickerSage
        </Link>
        <span>
          {path.startsWith("/evals")
            ? "eval results"
            : path.startsWith("/traces")
              ? "request traces"
              : path.startsWith("/strategy")
                ? "strategy backtests · read-only"
                : path.startsWith("/alerts")
                  ? "smart alerts · read-only"
                  : "crypto market research · read-only"}
        </span>
      </div>
      <nav>
        {mode}
        <Link href="/" aria-current={path === "/" ? "page" : undefined}>
          Chat
        </Link>
        <Link href="/evals" aria-current={path.startsWith("/evals") ? "page" : undefined}>
          Evals
        </Link>
        <Link href="/strategy" aria-current={path.startsWith("/strategy") ? "page" : undefined}>
          Strategy
        </Link>
        <Link href="/alerts" aria-current={path.startsWith("/alerts") ? "page" : undefined}>
          Alerts
          {unread > 0 && (
            <span className="badge" aria-label={`${unread} unread`}>
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </Link>
        <Link href="/traces" aria-current={path.startsWith("/traces") ? "page" : undefined}>
          Traces
        </Link>
        <a href={`${API_URL}/docs`} target="_blank" rel="noopener noreferrer">
          API
        </a>
      </nav>
    </header>
  );
}
