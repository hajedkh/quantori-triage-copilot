// Live-backend client. Talks to the FastAPI monolith from the HLD (section 8)
// through the Vite proxy at /api. Emits the same StreamEvent shape as mock.ts,
// so App.tsx handles both modes with one reducer.

import type { StreamEvent } from "./mock";
import type { LLMOptions, LLMProvider } from "./types";

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
export async function approveRun(runId: string): Promise<void> {
  const res = await fetch(`/api/approve/${runId}`, { method: "POST" });
  if (!res.ok) throw new Error(`approve failed: ${res.status}`);
}

// Download URL for an exported artifact.
export function downloadUrl(runId: string, kind: "csv" | "sdf" | "report") {
  return `/api/download/${runId}/${kind}`;
}
