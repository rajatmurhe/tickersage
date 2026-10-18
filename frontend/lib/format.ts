import type { Status } from "./types";

export const STATUS_PILL: Record<Status, { label: string; tone: "" | "warn" | "bad" }> = {
  ok: { label: "verified", tone: "" },
  refused: { label: "refused", tone: "warn" },
  out_of_scope: { label: "out of scope", tone: "warn" },
  clarify: { label: "needs detail", tone: "warn" },
  safe_fallback: { label: "safe fallback", tone: "warn" },
  error: { label: "error", tone: "bad" },
};

export const money = (x: number | null | undefined) => (x ? `$${x.toFixed(6)}` : "$0");

export const pct = (x: number | null | undefined, digits = 1) => (x == null ? "n/a" : `${(x * 100).toFixed(digits)}%`);

export const ms = (x: number) => `${Math.round(x)} ms`;

/** Citation URLs come from tool output (news feeds), so only plain http(s) links are rendered. */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}
