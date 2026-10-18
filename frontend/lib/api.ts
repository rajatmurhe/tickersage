import type { ChatResponse, StepEvent } from "./types";

/** FastAPI backend. Baked in at build time; see .env.development and the Dockerfile. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8077").replace(/\/+$/, "");

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, { signal, cache: "no-store" });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(`Could not reach the API at ${API_URL}.`);
  }
  if (!resp.ok) throw new ApiError(`${path} returned ${resp.status}.`, resp.status);
  return (await resp.json()) as T;
}

export interface StreamHandlers {
  onStep: (step: StepEvent) => void;
  onToken: (delta: string) => void;
  onDone: (response: ChatResponse) => void;
}

/**
 * POST /api/chat/stream and dispatch its SSE events. EventSource only does GET, so this reads the
 * body with fetch + ReadableStream. Tokens arrive only after the backend has verified every number.
 */
export async function streamChat(question: string, handlers: StreamHandlers, signal?: AbortSignal): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ question }),
      signal,
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(`Could not reach the API at ${API_URL}. Is the backend running?`);
  }
  if (!resp.ok || !resp.body) throw new ApiError(await failureMessage(resp), resp.status);

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let boundary: RegExpExecArray | null;
    while ((boundary = /\r\n\r\n|\n\n|\r\r/.exec(buf))) {
      const raw = buf.slice(0, boundary.index);
      buf = buf.slice(boundary.index + boundary[0].length);
      const event = parseEvent(raw);
      if (!event) continue;
      if (event.type === "step") handlers.onStep(event.data as StepEvent);
      else if (event.type === "token") handlers.onToken((event.data as { delta: string }).delta);
      else if (event.type === "done") {
        finished = true;
        handlers.onDone(event.data as ChatResponse);
      }
    }
  }
  if (!finished) throw new ApiError("The answer stream ended early. Please try again.");
}

/** FastAPI errors carry a readable `detail` string (e.g. the rate limit message on a 429); use it when present. */
async function failureMessage(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    // not JSON
  }
  return `Request failed (${resp.status}).`;
}

function parseEvent(raw: string): { type: string; data: unknown } | null {
  let type = "message";
  const data: string[] = [];
  for (const line of raw.split(/\r\n|\n|\r/)) {
    if (!line || line.startsWith(":")) continue; // comments are keep-alive pings
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") type = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  return { type, data: JSON.parse(data.join("\n")) };
}
