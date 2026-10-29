export type TraceItem =
  | { kind: "tool"; name: string; args: Record<string, unknown>; status: "running" | "ok" | "error"; summary?: string }
  | { kind: "guardrail"; stage: "input" | "output"; action: string; detail: string };

export type Source = { source: string; heading: string };

export type Confirmation = {
  token: string;
  tool: string;
  args: Record<string, string>;
  summary: string;
  state: "pending" | "sending" | "done" | "cancelled" | "error";
  message?: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  trace: TraceItem[];
  sources: Source[];
  running?: boolean;
  blocked?: boolean;
  /** Turns blocked by a guardrail are not resent as history. */
  excluded?: boolean;
  error?: string;
  confirmation?: Confirmation;
  metrics?: { elapsed_ms: number; llm_calls: number; tool_calls: number; tokens: number; model: string };
};

export type AgentEvent =
  | { type: "status"; message: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean; summary: string }
  | { type: "guardrail"; stage: "input" | "output"; action: string; detail: string }
  | { type: "confirmation"; token: string; tool: string; args: Record<string, string>; summary: string }
  | { type: "answer"; content: string; sources: Source[]; blocked: boolean }
  | { type: "error"; code: string; message: string }
  | { type: "done"; metrics: NonNullable<ChatMessage["metrics"]> };

export type Health = { ok: boolean; llm: boolean; db: boolean; model: string };
