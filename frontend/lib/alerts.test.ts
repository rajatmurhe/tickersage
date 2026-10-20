import { afterEach, describe, expect, it, vi } from "vitest";
import { ACTION_LABELS, cleanAnswers, clientId, cooldownLabel, deliveryNotes, newId, timeAgo } from "./alerts";

const NOW = Date.parse("2026-10-20T12:00:00Z");

describe("timeAgo", () => {
  it("reads recent times in plain words", () => {
    expect(timeAgo(null, NOW)).toBe("never");
    expect(timeAgo("2026-10-20T11:59:40Z", NOW)).toBe("just now");
    expect(timeAgo("2026-10-20T11:55:00Z", NOW)).toBe("5 min ago");
    expect(timeAgo("2026-10-20T09:00:00Z", NOW)).toBe("3 h ago");
    expect(timeAgo("2026-10-18T12:00:00Z", NOW)).toBe("2 days ago");
    expect(timeAgo("not a date", NOW)).toBe("unknown");
  });
});

describe("cooldownLabel", () => {
  it("uses the largest whole unit", () => {
    expect(cooldownLabel(60)).toBe("1 hour");
    expect(cooldownLabel(120)).toBe("2 hours");
    expect(cooldownLabel(1440)).toBe("1 day");
    expect(cooldownLabel(2880)).toBe("2 days");
    expect(cooldownLabel(90)).toBe("90 min");
  });
});

describe("cleanAnswers", () => {
  it("keeps known fields, trimmed and capped", () => {
    expect(cleanAnswers({ symbol: " ETH ", window: "", bogus: "x", threshold: "5".repeat(300) })).toEqual({
      symbol: "ETH",
      threshold: "5".repeat(200),
    });
  });
});

describe("deliveryNotes", () => {
  it("lists only the external channels", () => {
    expect(deliveryNotes({ in_app: "delivered", telegram: "delivered", email: "failed: timeout" })).toEqual([
      "Telegram: delivered",
      "Email: failed: timeout",
    ]);
  });
});

describe("clientId", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is stored once and reused", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) },
    });
    const id = clientId();
    expect(id).toMatch(/^c-[0-9a-f]{24}$/);
    expect(clientId()).toBe(id);
  });

  it("falls back to a per-page id when storage is blocked", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => {
          throw new Error("blocked");
        },
      },
    });
    expect(clientId()).toBe(clientId());
  });

  it("new ids are random and header-safe", () => {
    expect(newId()).not.toBe(newId());
    expect(newId()).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
  });
});

it("labels every scheduler action", () => {
  expect(Object.keys(ACTION_LABELS).sort()).toEqual(["cooldown", "error", "fire", "idle", "rearm", "wait_rearm"]);
});
