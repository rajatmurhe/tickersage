import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  cleanAnswers,
  errorDetail,
  flowRows,
  hasErrors,
  nearestIndex,
  parseFlow,
  postJson,
  signedPct,
  sparkline,
  unescapeMermaid,
  usd,
  utc,
} from "./strategy";

/** What backend/app/strategy/diagram.py writes for "RSI(14) crosses below 30, +5% / -3%, 72h, 10%". */
const MERMAID = `flowchart TD
  candles["ETH/USDT 1h candles<br/>checked at each candle close"]
  entry{"Entry rule (all must hold)<br/>RSI(14) crosses below 30"}
  buy["Buy at the next candle open<br/>10% of the $10,000 demo balance"]
  hold{"While holding, every candle"}
  tp(["Take profit at +5%<br/>sell at that price"])
  sl(["Stop loss at -3%<br/>sell at that price"])
  timeout(["Time exit after 3 days<br/>sell at the next candle open"])
  eod(["End of data<br/>close at the last candle"])
  flat["Flat: look for the next entry"]
  candles --> entry
  entry -->|no| candles
  entry -->|yes| buy
  buy --> hold
  hold -->|high reaches +5%| tp
  hold -->|low reaches -3%| sl
  hold -->|72 candles held| timeout
  hold -->|data runs out| eod
  tp --> flat
  sl --> flat
  timeout --> flat
  flat --> candles
`;

describe("parseFlow", () => {
  it("reads nodes with their shapes and <br/>-separated lines", () => {
    const flow = parseFlow(MERMAID);
    expect(flow.nodes.map((n) => [n.id, n.shape])).toEqual([
      ["candles", "box"],
      ["entry", "decision"],
      ["buy", "box"],
      ["hold", "decision"],
      ["tp", "terminal"],
      ["sl", "terminal"],
      ["timeout", "terminal"],
      ["eod", "terminal"],
      ["flat", "box"],
    ]);
    expect(flow.nodes[1].lines).toEqual(["Entry rule (all must hold)", "RSI(14) crosses below 30"]);
  });

  it("reads edges with and without labels", () => {
    const { edges } = parseFlow(MERMAID);
    expect(edges).toHaveLength(12);
    expect(edges[0]).toEqual({ from: "candles", to: "entry", label: null });
    expect(edges[1]).toEqual({ from: "entry", to: "candles", label: "no" });
    expect(edges[4]).toEqual({ from: "hold", to: "tp", label: "high reaches +5%" });
  });

  it("unescapes Mermaid entities into plain text and never produces markup", () => {
    const flow = parseFlow('  x["#lt;img src=x onerror=alert(1)#gt; #quot;q#quot; #35;1 #124; #91;a#93;"]\n  x -->|a #124; b| x');
    expect(flow.nodes[0].lines).toEqual(['<img src=x onerror=alert(1)> "q" #1 | [a]']);
    expect(flow.edges[0].label).toBe("a | b");
    expect(unescapeMermaid("#unknown; #amp;")).toBe("#unknown; &");
  });

  it("ignores lines it doesn't understand", () => {
    expect(parseFlow("flowchart TD\n%% comment\n  a --- b\n  click a callback\n")).toEqual({ nodes: [], edges: [] });
    expect(parseFlow("")).toEqual({ nodes: [], edges: [] });
  });
});

describe("flowRows", () => {
  it("stacks the main path and puts the exits side by side", () => {
    const rows = flowRows(parseFlow(MERMAID));
    expect(rows.map((r) => r.nodes.map((n) => n.node.id))).toEqual([
      ["candles"],
      ["entry"],
      ["buy"],
      ["hold"],
      ["tp", "sl", "timeout", "eod"],
      ["flat"],
    ]);
    expect(rows[2].nodes[0].via).toBe("yes");
    expect(rows[4].nodes.map((n) => n.via)).toEqual(["high reaches +5%", "low reaches -3%", "72 candles held", "data runs out"]);
    expect(rows[5].nodes[0].via).toBeNull(); // several exits lead in, none labelled
  });

  it("lists edges back to an earlier row as loops", () => {
    const rows = flowRows(parseFlow(MERMAID));
    expect(rows[1].loops.map((l) => [l.label, l.to.id])).toEqual([["no", "candles"]]);
    expect(rows[5].loops.map((l) => [l.label, l.to.id])).toEqual([[null, "candles"]]);
    expect(rows[0].loops).toEqual([]);
  });
});

describe("sparkline", () => {
  it("scales the series into the box with the reference line", () => {
    const s = sparkline([100, 110, 90], 104, 44, 100);
    expect(s.path).toBe("M2,22 L52,2 L102,42");
    expect(s.baseline).toBe(22);
    expect(s.xs).toEqual([2, 52, 102]);
  });

  it("widens the range to include the reference", () => {
    expect(sparkline([110, 120], 100, 22, 100, 1).baseline).toBe(21);
  });

  it("draws a flat series through the middle", () => {
    expect(sparkline([5, 5, 5], 10, 20).path).toBe("M2,10 L5,10 L8,10");
  });

  it("handles one point and no points", () => {
    expect(sparkline([7], 10, 20).path).toBe("M5,10");
    expect(sparkline([], 10, 20)).toEqual({ path: "", baseline: null, xs: [] });
  });

  it("snaps the crosshair to the nearest point", () => {
    expect(nearestIndex([2, 52, 102], 0)).toBe(0);
    expect(nearestIndex([2, 52, 102], 70)).toBe(1);
    expect(nearestIndex([2, 52, 102], 500)).toBe(2);
  });
});

describe("answers and findings", () => {
  it("keeps only known, non-empty, trimmed answers", () => {
    expect(cleanAnswers({ interval: " 4h ", position_size: "", wallet: "connect", stop_loss: "  " })).toEqual({
      interval: "4h",
    });
    expect(cleanAnswers({ entry: "x".repeat(300) }).entry).toHaveLength(200);
  });

  it("blocks the backtest only on error findings", () => {
    expect(hasErrors([{ rule: "no_stop_loss", severity: "warn", field: "stop_loss_pct", message: "" }])).toBe(false);
    expect(hasErrors([{ rule: "rsi_out_of_range", severity: "error", field: "entry", message: "" }])).toBe(true);
    expect(hasErrors([])).toBe(false);
  });
});

describe("formatting", () => {
  it("formats signed percentages and dollars", () => {
    expect(signedPct(2.5)).toBe("+2.50%");
    expect(signedPct(-0.333)).toBe("−0.33%");
    expect(signedPct(0)).toBe("0.00%");
    expect(signedPct(null)).toBe("n/a");
    expect(usd(10033.5)).toBe("$10,033.50");
    expect(usd(-32.42)).toBe("−$32.42");
  });

  it("shortens UTC timestamps", () => {
    expect(utc("2026-10-07T02:00:00Z")).toBe("2026-10-07 02:00");
    expect(utc("2026-10-07T02:00:00.123+00:00")).toBe("2026-10-07 02:00");
  });
});

describe("postJson", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts JSON and returns the body", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(postJson("/api/strategy/compile", { text: "x" })).resolves.toEqual({ status: "ok" });
    const init = fetchMock.mock.calls[0][1]!;
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ text: "x" });
  });

  it("turns a 422 validation list into one readable line", async () => {
    const body = { detail: [{ loc: ["body", "spec", "symbol"], msg: "Field required", type: "missing" }] };
    expect(await errorDetail(Response.json(body, { status: 422 }))).toBe("spec.symbol: Field required");
    expect(await errorDetail(Response.json({ detail: "Rate limit reached." }, { status: 429 }))).toBe(
      "Rate limit reached.",
    );
    expect(await errorDetail(new Response("oops", { status: 500 }))).toBe("Request failed (500).");
  });

  it("raises ApiError with the status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "slow down" }, { status: 429 })));
    const err = await postJson("/api/strategy/backtest", {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).message).toBe("slow down");
  });

  it("reports an unreachable API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    await expect(postJson("/api/strategy/compile", {})).rejects.toThrow(/Could not reach the API/);
  });
});
