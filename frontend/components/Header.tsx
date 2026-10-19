"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { API_URL, getJson } from "@/lib/api";
import type { Health } from "@/lib/types";

export default function Header() {
  const path = usePathname();
  const [health, setHealth] = useState<Health | null | "down">(null);

  useEffect(() => {
    const controller = new AbortController();
    getJson<Health>("/api/health", controller.signal)
      .then(setHealth)
      .catch(() => {
        if (!controller.signal.aborted) setHealth("down");
      });
    return () => controller.abort();
  }, []);

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
