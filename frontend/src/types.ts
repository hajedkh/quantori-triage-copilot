// Types mirror the backend AppState (see HLD section 5).

export type AgentId = "supervisor" | "knowledge" | "cheminformatics" | "critic" | "diversifier";
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
  diversified_added?: number | null;
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

// Chemotype-diversity selection applied by the Diversifier agent.
export type DiversityMode = "off" | "scaffold" | "mmr" | "cluster";

export interface DiversityStats {
  mode: DiversityMode;
  lambda: number;
  n_selected: number;
  n_scaffolds: number | null;
  n_clusters: number | null;
  n_generated?: number;
  seed_count?: number;
}

export interface DiversifyRequest {
  mode: DiversityMode;
  lam?: number;
  cutoff?: number;
  maxGenerated: number;
}

// Citation-grounding report from the Knowledge agent's dossier build.
export interface Grounding {
  cited_pmids: string[];
  provided_pmids?: string[];
  ungrounded: { pmid: string; reason?: string }[];
  all_grounded?: boolean;
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
  grounding: Grounding | null;
  diversity: DiversityStats | null;
  log: LogEvent[];
  targetName: string;
  targetId: string;
  toolTrace: Record<AgentId, ToolCallEvent[]>;
  // Same shape as toolTrace but never capped — toolTrace keeps only the last
  // TRACE_CAP entries per agent for the live rail display; this is the full
  // history, kept around solely for the "Download traces" export.
  fullTrace: Record<AgentId, ToolCallEvent[]>;
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

// Chat copilot — not a new agent, reuses run_tool_loop with chat_tools.py.
export interface ChatToolCallDisplay {
  tool: string;
  args: Record<string, unknown>;
  result_summary: string;
  status: "ok" | "error" | "retry";
}

// A mutate tool (rerank/focus_scaffold) called without confirmed=true
// returns one of these instead of applying anything.
export interface ChatPreview {
  toolName: string;
  entering_top20?: string[];
  leaving_top20?: string[];
  message?: string;
  [key: string]: unknown;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ChatToolCallDisplay[];
  preview?: ChatPreview | null;
  streaming?: boolean;
}