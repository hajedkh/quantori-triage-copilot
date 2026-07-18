import { useEffect, useRef, useState } from "react";
import { BookOpen, FlaskConical, ListChecks, Compass, Shuffle, Maximize2 } from "lucide-react";
import type {
  AgentId,
  AgentStatus,
  DiversityStats,
  FunnelState,
  LogEvent,
  Metric,
  ToolCallEvent,
} from "../types";
import FunnelMeter from "./FunnelMeter";
import BrandSpinner from "./BrandSpinner";
import AgentTraceModal from "./AgentTraceModal";

interface Props {
  agents: Record<AgentId, AgentStatus>;
  funnel: FunnelState;
  metric: Metric | null;
  diversity: DiversityStats | null;
  log: LogEvent[];
  toolTrace: Record<AgentId, ToolCallEvent[]>;
  // Same data as toolTrace but never capped — only used when a card is
  // expanded, so the modal can show the complete trace, not just the last 8.
  fullTrace: Record<AgentId, ToolCallEvent[]>;
  // True for the whole diversify-rerun request (fired -> re-screen ->
  // re-rank -> back at the gate), not just the Diversifier's own
  // near-instant re-selection step — see App.tsx for why.
  diversifying: boolean;
}

const AGENTS: { id: AgentId; name: string; role: string; icon: JSX.Element }[] = [
  { id: "supervisor", name: "Supervisor", role: "plan · route · gate", icon: <Compass size={16} /> },
  { id: "knowledge", name: "Knowledge", role: "cited dossier · actives", icon: <BookOpen size={16} /> },
  { id: "cheminformatics", name: "Cheminformatics", role: "RDKit filters · similarity", icon: <FlaskConical size={16} /> },
  { id: "critic", name: "Critic / Ranking", role: "score · confidence", icon: <ListChecks size={16} /> },
  { id: "diversifier", name: "Diversifier", role: "chemotype spread", icon: <Shuffle size={16} /> },
];

export default function PipelineRail({
  agents,
  funnel,
  metric,
  diversity,
  log,
  toolTrace,
  fullTrace,
  diversifying,
}: Props) {
  const diversifierLog = log.filter((e) => e.agent === "diversifier");
  const [expanded, setExpanded] = useState<AgentId | null>(null);
  const expandedMeta = AGENTS.find((a) => a.id === expanded);

  return (
    <aside className="rail">
      <div className="panel">
        <div className="panel-h">
          <h3>Agent pipeline</h3>
        </div>
        <div className="agents">
          {AGENTS.map((a) => {
            // The Diversifier's real status is stretched to cover the whole
            // rerun request (see App.tsx's `diversifying` flag) instead of
            // just its own sub-millisecond re-selection step, which used to
            // come and go between two SSE events before a frame ever painted.
            const st: AgentStatus =
              a.id === "diversifier" && diversifying ? "running" : agents[a.id];
            const trace = toolTrace[a.id] || [];
            return (
              <div
                key={a.id}
                className={"agent " + st}
                role="button"
                tabIndex={0}
                aria-label={`Expand ${a.name} trace`}
                onClick={() => setExpanded(a.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setExpanded(a.id);
                  }
                }}
              >
                <div className="agent-top">
                  <div className="agent-ico">
                    {st === "running" ? <BrandSpinner size={17} label={`${a.name} working`} /> : a.icon}
                  </div>
                  <div className="agent-body">
                    <div className="agent-name">{a.name}</div>
                    <div className="agent-role">{a.role}</div>
                  </div>
                  <div className="agent-state">
                    {st === "running" ? "active" : st === "done" ? "done" : "idle"}
                  </div>
                  <Maximize2 size={13} className="agent-expand-hint" />
                </div>
                {a.id === "diversifier" && (
                  <div onClick={(e) => e.stopPropagation()}>
                    <DiversifierFeedback status={st} diversity={diversity} log={diversifierLog} />
                  </div>
                )}
                {trace.length > 0 && <ToolTrace trace={trace} />}
              </div>
            );
          })}
        </div>
      </div>

      <FunnelMeter funnel={funnel} metric={metric} />

      <div className="panel">
        <div className="panel-h">
          <h3>Trace</h3>
        </div>
        <TraceLog log={log} />
      </div>

      {expandedMeta &&
        (() => {
          const expandedStatus: AgentStatus =
            expandedMeta.id === "diversifier" && diversifying ? "running" : agents[expandedMeta.id];
          return (
            <AgentTraceModal
              name={expandedMeta.name}
              role={expandedMeta.role}
              icon={expandedMeta.icon}
              status={expandedStatus}
              trace={fullTrace[expandedMeta.id] || []}
              logLines={log.filter((e) => e.agent === expandedMeta.id).map((e) => e.msg)}
              extra={
                expandedMeta.id === "diversifier" ? (
                  <DiversifierStats status={expandedStatus} diversity={diversity} />
                ) : null
              }
              onClose={() => setExpanded(null)}
            />
          );
        })()}
    </aside>
  );
}

const MODE_LABEL: Record<DiversityStats["mode"], string> = {
  off: "Off",
  scaffold: "Scaffold",
  mmr: "MMR",
  cluster: "Cluster",
};

// Just the header + stat grid, no outer wrapper and no trace — shared by
// the inline card's DiversifierFeedback (which wraps this plus its own
// trace in one .div-feedback box, unchanged from before) and the expanded
// modal's `extra` (its own .div-feedback box; the modal renders the trace
// itself from `logLines`, so it isn't duplicated here).
function DiversifierStatsBody({ status, diversity }: { status: AgentStatus; diversity: DiversityStats | null }) {
  return (
    <>
      <div className="div-feedback-head">
        Diversifier feedback
        {status === "running" && <span className="div-feedback-live">live</span>}
      </div>
      {diversity ? (
        <div className="div-feedback-grid">
          <div className="df-kv">
            <span>mode</span>
            <b>{MODE_LABEL[diversity.mode] ?? diversity.mode}</b>
          </div>
          <div className="df-kv">
            <span>selected</span>
            <b>{diversity.n_selected}</b>
          </div>
          {diversity.n_scaffolds != null && (
            <div className="df-kv">
              <span>scaffolds</span>
              <b>{diversity.n_scaffolds}</b>
            </div>
          )}
          {diversity.n_clusters != null && (
            <div className="df-kv">
              <span>clusters</span>
              <b>{diversity.n_clusters}</b>
            </div>
          )}
          {diversity.mode === "mmr" && (
            <div className="df-kv">
              <span>lambda</span>
              <b>{diversity.lambda.toFixed(2)}</b>
            </div>
          )}
        </div>
      ) : (
        <div className="div-feedback-note">
          {status === "running"
            ? "Computing chemotype spread and re-selecting the shortlist…"
            : "No diversity pass output yet."}
        </div>
      )}
    </>
  );
}

// Used as AgentTraceModal's `extra` for the Diversifier card — its own
// .div-feedback box, same look as the inline one, no trace (the modal shows
// that separately).
function DiversifierStats({ status, diversity }: { status: AgentStatus; diversity: DiversityStats | null }) {
  if (!diversity && status === "idle") return null;
  return (
    <div className="div-feedback fadeup">
      <DiversifierStatsBody status={status} diversity={diversity} />
    </div>
  );
}

function DiversifierFeedback({
  status,
  diversity,
  log,
}: {
  status: AgentStatus;
  diversity: DiversityStats | null;
  log: LogEvent[];
}) {
  if (!diversity && status === "idle") return null;

  return (
    <div className="div-feedback fadeup">
      <DiversifierStatsBody status={status} diversity={diversity} />
      {log.length > 0 && <DiversifierTrace log={log} live={status === "running"} />}
    </div>
  );
}

// A descriptive trace built straight from the Diversifier's own log lines —
// it has no tool_call events to show (it's deterministic RDKit, not an LLM
// tool-calling loop), so this is its equivalent of the other agents' trace,
// styled the same way (.tool-trace/.tt-row) for a consistent look in the rail.
function DiversifierTrace({ log, live }: { log: LogEvent[]; live: boolean }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [log.length]);

  return (
    <div className="tool-trace" ref={ref}>
      {log.map((e, i) => (
        <div key={i} className="tt-row fadeup">
          <div className="tt-call">
            <span className="tt-icon">🧬</span>
            <span>{e.msg}</span>
          </div>
        </div>
      ))}
      {live && (
        <div className="tt-row tt-summary fadeup">
          <BrandSpinner size={11} label="still working" /> still going…
        </div>
      )}
    </div>
  );
}

// Shared row renderer — used by the inline capped ToolTrace and the full,
// uncapped list inside AgentTraceModal, so the two never drift apart.
export function renderTraceRow(t: ToolCallEvent, i: number) {
  if (t.iteration === -1) {
    return (
      <div key={i} className="tt-row tt-summary fadeup">
        {t.result_summary}
      </div>
    );
  }
  const argStr = Object.keys(t.args || {}).length ? JSON.stringify(t.args) : "";
  return (
    <div key={i} className={"tt-row fadeup " + t.status}>
      {t.thought && <div className="tt-thought">{t.thought}</div>}
      <div className="tt-call">
        <span className="tt-icon">{t.status === "error" ? "⚠" : "🔧"}</span>
        <span className="tt-name">{t.tool}</span>
        {argStr && <span className="tt-args">{argStr}</span>}
        {t.status !== "error" && (
          <span className="tt-result ok">
            → ✓ {t.result_summary}
            {t.status === "retry" && <span className="tt-retry-tag"> (retry)</span>}
          </span>
        )}
      </div>
      {t.status === "error" && <div className="tt-result err">{t.result_summary}</div>}
    </div>
  );
}

// Renders the live [thought] → 🔧 tool(args) → ✓ result_summary steps of an
// agentic tool-calling loop. iteration:-1 is the loop-summary line (token
// count), rendered as a quiet footer rather than a call row.
function ToolTrace({ trace }: { trace: ToolCallEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [trace.length]);

  return (
    <div className="tool-trace" ref={ref} onClick={(e) => e.stopPropagation()}>
      {trace.map(renderTraceRow)}
    </div>
  );
}

function TraceLog({ log }: { log: LogEvent[] }) {
  if (log.length === 0) {
    return <div className="empty" style={{ padding: "22px 16px" }}>Waiting for run…</div>;
  }
  return (
    <div style={{ padding: "10px 12px", maxHeight: 260, overflowY: "auto" }}>
      {log.slice(-14).map((e, i) => (
        <div
          key={i}
          className="fadeup"
          style={{
            display: "flex",
            gap: 8,
            padding: "5px 0",
            fontFamily: "var(--mono)",
            fontSize: 11.5,
            lineHeight: 1.5,
            color: "var(--fg-dim)",
          }}
        >
          <span style={{ color: "var(--fg-faint)", flex: "none" }}>
            {e.agent.slice(0, 4)}
          </span>
          <span>{e.msg}</span>
        </div>
      ))}
    </div>
  );
}