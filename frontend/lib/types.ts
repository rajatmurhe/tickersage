// Mirrors backend/app/schemas.py (ChatResponse, Trace) and the eval runner's latest.json.

export type Status = "ok" | "refused" | "out_of_scope" | "clarify" | "safe_fallback" | "error";

export interface StepTrace {
  name: string;
  latency_ms: number;
  model: string | null;
  provider: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  fell_back: boolean;
  detail: Record<string, unknown>;
}

export interface ToolTrace {
  id: number;
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  error: string | null;
  error_message: string | null;
  latency_ms: number;
  cached: boolean;
  source: string | null;
}

export interface Trace {
  request_id: string;
  intent: string | null;
  tools: ToolTrace[];
  steps: StepTrace[];
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  models: string[];
  fell_back: boolean;
  status: Status;
  refusal_reason: string | null;
  faithfulness: number | null;
  unsupported_numbers: string[];
}

export interface Citation {
  id: number;
  kind: "data" | "doc" | "news";
  title: string;
  url: string | null;
}

export interface ChatResponse {
  request_id: string;
  question: string;
  answer: string;
  status: Status;
  intent: string | null;
  citations: Citation[];
  data_as_of: string | null;
  trace: Trace;
}

/** `step` SSE event: one graph node has finished. */
export interface StepEvent {
  node: string;
  label: string;
  detail: Partial<StepTrace>;
}

export interface Health {
  status: string;
  llm: string;
  tool_mode: string;
  routing_mode: string;
  planner_mode: string;
  verifier: boolean;
}

export interface EvalSummary {
  pass_rate: number;
  tool_selection_accuracy: number;
  intent_accuracy: number;
  number_faithfulness: number;
  numbers_checked: number;
  key_number_accuracy: number;
  refusal_accuracy: number;
  refusal_recall: number;
  false_refusal_rate: number;
  injection_resistance: number;
  citation_rate: number | null;
  latency_p50_ms: number;
  latency_p95_ms: number;
  avg_cost_usd: number | null;
  judge_score: number | null;
}

export interface EvalCase {
  id: string;
  category: string;
  question: string;
  passed: boolean;
  failures: string[];
  status: Status;
  answer: string;
}

export interface EvalRun {
  run_id: string;
  created_at: string;
  cases_run: number;
  config: { llm: string; routing_mode: string; verifier: boolean; tool_mode: string };
  summary: EvalSummary;
  by_category: Record<string, { n: number; passed: number; pass_rate: number }>;
  failure_taxonomy: Record<string, number>;
  cases: EvalCase[];
}
