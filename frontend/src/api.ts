// Live-backend client. Talks to the FastAPI monolith from the HLD (section 8)
// through the Vite proxy at /api. Emits the same StreamEvent shape as mock.ts,
// so App.tsx handles both modes with one reducer.

import type { StreamEvent } from "./mock";
import type {
  LLMOptions,
  LLMProvider,
  DiversifyRequest,
  RankingProfile,
  RankedMol,
} from "./types";

export interface StartResult {
  runId: string;
}

export interface LLMConfigResult {
  provider: LLMProvider;
  model: string;
  options: LLMOptions;
}

export interface LLMHealthResult {
  ok: boolean;
  latency_ms: number;
  model: string;
  error: string | null;
}

// GET /config/llm -> current active provider/model + selectable options.
// Never includes an api_key.
export async function getLLMConfig(): Promise<LLMConfigResult> {
  const res = await fetch("/api/config/llm");
  if (!res.ok) throw new Error(`get llm config failed: ${res.status}`);
  return res.json();
}

// POST /config/llm {provider, model} -> switches the process-wide active config.
export async function setLLMConfig(
  provider: LLMProvider,
  model: string
): Promise<LLMConfigResult> {
  const res = await fetch("/api/config/llm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || `set llm config failed: ${res.status}`);
  }
  return res.json();
}

// GET /config/llm/health?provider=... -> 1-token ping result for that provider.
export async function getLLMHealth(provider: LLMProvider): Promise<LLMHealthResult> {
  const res = await fetch(`/api/config/llm/health?provider=${encodeURIComponent(provider)}`);
  if (!res.ok) throw new Error(`get llm health failed: ${res.status}`);
  return res.json();
}

// POST /run  (multipart: target_name + candidates.csv) -> { run_id }
export async function startRun(
  targetName: string,
  file: File
): Promise<StartResult> {
  const fd = new FormData();
  fd.append("target_name", targetName);
  fd.append("candidates", file);
  const res = await fetch("/api/run", { method: "POST", body: fd });
  if (!res.ok) throw new Error(`start failed: ${res.status}`);
  const data = await res.json();
  return { runId: data.run_id };
}

// GET /stream/{runId}  (SSE) -> maps backend log events into StreamEvents.
export function subscribe(
  runId: string,
  emit: (e: StreamEvent) => void
): () => void {
  const es = new EventSource(`/api/stream/${runId}`);

  es.addEventListener("message", (ev) => {
    try {
      const raw = JSON.parse((ev as MessageEvent).data);
      // Backend is expected to send { type, agent?, payload? } already shaped
      // like StreamEvent. If your backend differs, adapt the mapping here.
      emit(raw as StreamEvent);
      if (raw.type === "awaiting_approval" || raw.type === "error") es.close();
    } catch {
      /* ignore keep-alives */
    }
  });

  es.onerror = () => es.close();
  return () => es.close();
}

// POST /approve/{runId} -> resumes the interrupted graph, triggers export.
export async function approveRun(
  runId: string,
  rankingProfile?: RankingProfile
): Promise<void> {
  const body = rankingProfile ? JSON.stringify({ rankingProfile }) : undefined;
  const res = await fetch(`/api/approve/${runId}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body,
  });
  if (!res.ok) throw new Error(`approve failed: ${res.status}`);
}

export async function rerankRun(
  runId: string,
  rankingProfile: RankingProfile
): Promise<{ ranked: RankedMol[]; rankingProfile: RankingProfile }> {
  const res = await fetch(`/api/rerank/${runId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rankingProfile }),
  });
  if (!res.ok) throw new Error(`rerank failed: ${res.status}`);
  const data = await res.json();
  return {
    ranked: data.ranked,
    rankingProfile: data.rankingProfile,
  };
}

// POST /diversify/{runId} -> run one more loop:
// diversifier -> cheminformatics -> critic -> approval gate.
export async function diversifyRun(runId: string, req: DiversifyRequest): Promise<void> {
  const res = await fetch(`/api/diversify/${runId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`diversify failed: ${res.status}`);
}

// Download URL for an exported artifact.
export function downloadUrl(runId: string, kind: "csv" | "sdf" | "report") {
  return `/api/download/${runId}/${kind}`;
}

// ---------------------------------------------------------------- chat copilot --

// POST /session -> {run_id}. Called once on app load so the chat has
// something to talk to before any target/library/pipeline exists.
export async function createSession(): Promise<StartResult> {
  const res = await fetch("/api/session", { method: "POST" });
  if (!res.ok) throw new Error(`create session failed: ${res.status}`);
  const data = await res.json();
  return { runId: data.run_id };
}

// POST /upload/{runId} (multipart) -> {count}. Attaches a library to a
// setup-phase run without starting it — distinct from the existing
// POST /run, which creates+starts a run in one step.
export async function uploadLibrary(runId: string, file: File): Promise<number> {
  const fd = new FormData();
  fd.append("candidates", file);
  const res = await fetch(`/api/upload/${runId}`, { method: "POST", body: fd });
  if (!res.ok) throw new Error(`upload failed: ${res.status}`);
  const data = await res.json();
  return data.count;
}

// POST /steer/{runId} {message} -> {queued, position}. Only valid while running.
export async function steer(runId: string, message: string): Promise<{ queued: boolean; position: number }> {
  const res = await fetch(`/api/steer/${runId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail || `steer failed: ${res.status}`);
  }
  return res.json();
}

export interface ChatEvent {
  type: "tool_call" | "chat_token" | "chat_done" | "steer" | string;
  agent?: string;
  payload?: any;
}

// POST /chat/{runId} {message} -> SSE, but POST bodies can't use the native
// EventSource API, so this reads the streamed response body directly and
// splits it on sse_starlette's "data: ...\n\n" framing (the same wire format
// /stream already uses under the hood — EventSource just parses it for us
// there; here we do it by hand).
export async function askChat(
  runId: string,
  message: string,
  onEvent: (e: ChatEvent) => void
): Promise<void> {
  const res = await fetch(`/api/chat/${runId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (; ;) {
    const { done, value } = await reader.read();
    if (done) break;
    // The actual wire format is CRLF ("\r\n\r\n" between frames) even though
    // curl's terminal output renders that visually identical to "\n\n" —
    // normalize before splitting or every frame silently fails to match.
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue; // keep-alive comment lines etc.
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}