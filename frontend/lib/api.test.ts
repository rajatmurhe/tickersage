import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError, streamChat } from "./api";
import type { ChatResponse, StepEvent } from "./types";

const STEP: StepEvent = { node: "router", label: "Routing the question", detail: { latency_ms: 12 } };
const DONE = { request_id: "r1", answer: "BTC is $67,000 €.", status: "ok" } as unknown as ChatResponse;

/** The backend (sse-starlette) ends lines with \r\n and sends ": ping" comments as keep-alives. */
const BODY =
  ": ping - 2026-10-18 21:00:00\r\n\r\n" +
  `event: step\r\ndata: ${JSON.stringify(STEP)}\r\n\r\n` +
  `event: token\r\ndata: ${JSON.stringify({ delta: "BTC is " })}\r\n\r\n` +
  `event: token\r\ndata: ${JSON.stringify({ delta: "$67,000 €." })}\r\n\r\n` +
  `event: done\r\ndata: ${JSON.stringify(DONE)}\r\n\r\n`;

/** A fetch that answers with `chunks`, each delivered as its own read(). Strings are UTF-8 encoded. */
function mockFetch(chunks: (string | Uint8Array)[], init: ResponseInit = { status: 200 }) {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === "string" ? enc.encode(c) : c);
      controller.close();
    },
  });
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(body, init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Split `text` at the given character offsets. */
function splitAt(text: string, ...offsets: number[]): string[] {
  const cuts = [0, ...offsets, text.length];
  return cuts.slice(1).map((end, i) => text.slice(cuts[i], end));
}

async function run(question = "price of btc") {
  const steps: StepEvent[] = [];
  const tokens: string[] = [];
  const done: ChatResponse[] = [];
  await streamChat(question, {
    onStep: (s) => steps.push(s),
    onToken: (t) => tokens.push(t),
    onDone: (r) => done.push(r),
  });
  return { steps, tokens, done };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamChat", () => {
  it("posts the question and dispatches step, token and done events", async () => {
    const fetchMock = mockFetch([BODY]);
    const { steps, tokens, done } = await run("price of btc");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/api/chat/stream`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ question: "price of btc" });

    expect(steps).toEqual([STEP]);
    expect(tokens).toEqual(["BTC is ", "$67,000 €."]);
    expect(done).toEqual([DONE]);
  });

  it("reassembles events split mid-field, mid-CRLF and mid-JSON", async () => {
    const at = BODY.indexOf("event: token");
    const chunks = splitAt(BODY, at + 3, at + 13, at + 30);
    expect(chunks[1]).toBe("nt: token\r");
    mockFetch(chunks);
    expect(await run()).toEqual({ steps: [STEP], tokens: ["BTC is ", "$67,000 €."], done: [DONE] });
  });

  it('waits for the full blank line when a chunk ends in "\\r\\n\\r"', async () => {
    const at = BODY.indexOf("\r\n\r\n", BODY.indexOf("event: step"));
    const chunks = splitAt(BODY, at + 3);
    expect(chunks[0].endsWith("\r\n\r")).toBe(true);
    expect(chunks[1].startsWith("\nevent: token")).toBe(true);
    mockFetch(chunks);
    expect(await run()).toEqual({ steps: [STEP], tokens: ["BTC is ", "$67,000 €."], done: [DONE] });
  });

  it("handles every possible chunk boundary, including inside a multi-byte character", async () => {
    const bytes = new TextEncoder().encode(BODY);
    mockFetch(Array.from(bytes, (b) => Uint8Array.of(b)));
    expect(await run()).toEqual({ steps: [STEP], tokens: ["BTC is ", "$67,000 €."], done: [DONE] });
  });

  it("accepts \\n and \\r line endings and skips comment-only events", async () => {
    mockFetch([
      ": ping\n\n",
      `event: token\ndata: {"delta":"a"}\n\n`,
      ":keep-alive\r\r",
      `event: token\rdata: {"delta":"b"}\r\r`,
      `event: done\ndata: ${JSON.stringify(DONE)}\n\n`,
    ]);
    const { tokens, done } = await run();
    expect(tokens).toEqual(["a", "b"]);
    expect(done).toEqual([DONE]);
  });

  it("parses a payload spread over several data: lines, with a comment between them", async () => {
    mockFetch([
      'event: token\ndata: {"delta":\n: comment inside an event\ndata:"two\\nlines"\ndata: }\n\n',
      `event: done\ndata: ${JSON.stringify(DONE)}\n\n`,
    ]);
    const { tokens } = await run();
    expect(tokens).toEqual(["two\nlines"]);
  });

  it("ignores unknown event types", async () => {
    mockFetch([`event: heartbeat\ndata: {}\n\n`, `event: done\ndata: ${JSON.stringify(DONE)}\n\n`]);
    expect(await run()).toEqual({ steps: [], tokens: [], done: [DONE] });
  });

  it("throws ApiError when the stream ends without a done event", async () => {
    mockFetch([`event: step\ndata: ${JSON.stringify(STEP)}\n\n`, `event: token\ndata: {"delta":"BTC"}\n\n`]);
    const handlers = { onStep: vi.fn(), onToken: vi.fn(), onDone: vi.fn() };
    const result = streamChat("q", handlers);
    await expect(result).rejects.toBeInstanceOf(ApiError);
    await expect(result).rejects.toThrow("ended early");
    expect(handlers.onStep).toHaveBeenCalledOnce();
    expect(handlers.onToken).toHaveBeenCalledWith("BTC");
    expect(handlers.onDone).not.toHaveBeenCalled();
  });

  it("throws ApiError when the done event is cut off before its blank line", async () => {
    mockFetch([`event: done\ndata: ${JSON.stringify(DONE)}\n`]);
    await expect(run()).rejects.toBeInstanceOf(ApiError);
  });

  it("throws ApiError with the status for a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("rate limited", { status: 429 })));
    const err = await run().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).message).toBe("Request failed (429).");
  });

  it("uses the API's detail message when the error body has one", async () => {
    const detail = "Rate limit reached: this demo allows 5 questions per minute. Try again in 42 s.";
    const body = JSON.stringify({ detail });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 429, headers: { "Content-Type": "application/json" } })));
    const err = await run().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).message).toBe(detail);
  });

  it("throws ApiError when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    await expect(run()).rejects.toThrow(/Could not reach the API/);
  });

  it("rethrows the abort error instead of wrapping it when the caller aborts", async () => {
    const controller = new AbortController();
    controller.abort();
    const abort = new DOMException("aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(abort)));
    const noop = { onStep() {}, onToken() {}, onDone() {} };
    await expect(streamChat("q", noop, controller.signal)).rejects.toBe(abort);
  });
});
