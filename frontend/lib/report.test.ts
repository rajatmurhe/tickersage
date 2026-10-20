import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError } from "./api";
import { CHART, polyline, priceScale, slug, streamReport, type PriceSeries, type Report } from "./report";
import type { StepEvent } from "./types";

const STEP: StepEvent = { node: "asset_agent", label: "Sub-agent researched BTC and wrote its section", detail: {} };
const DONE = { request_id: "r1", status: "ok", title: "Weekly research report: BTC" } as unknown as Report;

function mockFetch(body: string, init: ResponseInit = { status: 200 }) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(body, init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("streamReport", () => {
  it("posts the question and dispatches step and done events", async () => {
    const fetchMock = mockFetch(
      `: ping\r\n\r\nevent: step\r\ndata: ${JSON.stringify(STEP)}\r\n\r\nevent: done\r\ndata: ${JSON.stringify(DONE)}\r\n\r\n`,
    );
    const steps: StepEvent[] = [];
    const done: Report[] = [];
    await streamReport("Weekly report on BTC", { onStep: (s) => steps.push(s), onDone: (r) => done.push(r) });
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}/api/report`);
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({ question: "Weekly report on BTC" });
    expect(steps).toEqual([STEP]);
    expect(done[0].title).toBe("Weekly research report: BTC");
  });

  it("fails when the stream ends without a report", async () => {
    mockFetch(`event: step\ndata: ${JSON.stringify(STEP)}\n\n`);
    await expect(streamReport("x", { onStep: () => {}, onDone: () => {} })).rejects.toThrow(/ended early/);
  });

  it("surfaces the API's error detail", async () => {
    mockFetch(JSON.stringify({ detail: "Too many requests" }), { status: 429 });
    const err = await streamReport("x", { onStep: () => {}, onDone: () => {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Too many requests");
  });
});

const series = (asset: string, idx: number[]): PriceSeries => ({
  asset, interval: "4h", cite: null, points: idx.map((index, i) => ({ t: `2026-10-1${i}T00:00:00Z`, close: index, index })),
});

describe("chart helpers", () => {
  it("pads the y range around every series", () => {
    const s = priceScale([series("BTC", [100, 98, 97]), series("SOL", [100, 92, 91.7])])!;
    expect(s.lo).toBeLessThan(91.7);
    expect(s.hi).toBeGreaterThan(100);
    expect(s.ticks).toHaveLength(5);
    expect(priceScale([series("BTC", [100])])).toBeNull();
  });

  it("spans the plot width from the first to the last candle", () => {
    const s = series("BTC", [100, 110, 90]);
    const pts = polyline(s, priceScale([s])!).split(" ").map((p) => p.split(",").map(Number));
    expect(pts[0][0]).toBe(CHART.left);
    expect(pts[2][0]).toBe(CHART.w - CHART.right);
    expect(pts[1][1]).toBeLessThan(pts[0][1]); // higher value, higher on screen
  });

  it("makes file names from titles", () => {
    expect(slug("Weekly research report: BTC, ETH and SOL")).toBe("weekly-research-report-btc-eth-and-sol");
  });
});
