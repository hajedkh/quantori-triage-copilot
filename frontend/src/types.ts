// Types mirror the backend AppState (see HLD section 5).

export type AgentId = "supervisor" | "knowledge" | "cheminformatics" | "critic";
export type AgentStatus = "idle" | "running" | "done";
export type Confidence = "High" | "Medium" | "Low";
export type RunStatus =
  | "idle"
  | "running"
  | "awaiting_approval"
  | "exported"
  | "error";

export interface Citation {
  claim: string;
  pmid: string;
}

export interface FunnelState {
  input: number;
  filtered: number | null;
  ranked: number | null;
}

export interface RankedMol {
  rank: number;
  smiles: string;
  score: number;
  confidence: Confidence;
  reason: string;
  nearest_active: string;
  max_similarity: number;
  is_known_active: boolean;
}

export interface LogEvent {
  ts: number;
  agent: AgentId;
  msg: string;
  detail?: string;
}

export interface Metric {
  recovered: number;
  total_actives: number;
  top_n: number;
  screened: number;
}

// A single step of an agentic tool-calling loop (cheminformatics/critic).
// iteration: -1 marks the loop-summary event (tool is null, result_summary
// carries the token count) rather than an actual tool execution.
export interface ToolCallEvent {
  iteration: number;
  thought: string;
  tool: string | null;
  args: Record<string, unknown>;
  result_summary: string;
  status: "ok" | "error" | "retry";
}

// The whole client-side run, assembled from streamed events.
export interface RunState {
  status: RunStatus;
  agents: Record<AgentId, AgentStatus>;
  activeAgent: AgentId | null;
  funnel: FunnelState;
  dossier: string;
  citations: Citation[];
  ranked: RankedMol[];
  metric: Metric | null;
  log: LogEvent[];
  targetName: string;
  targetId: string;
  toolTrace: Record<AgentId, ToolCallEvent[]>;
}

// LLM provider layer (both providers are OpenAI-compatible; see backend/app/llm.py).
export type LLMProvider = "ollama" | "gateway";

export interface LLMOptions {
  ollama: string[];
  gateway: string[];
}

export type LLMHealthStatus = "checking" | "ok" | "down";

export interface LLMHealth {
  status: LLMHealthStatus;
  ok: boolean;
  latency_ms: number;
  error: string | null;
}
