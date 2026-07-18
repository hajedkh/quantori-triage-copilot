import { useEffect, useRef } from "react";
import { BookOpen, FlaskConical, ListChecks, Compass, Shuffle } from "lucide-react";
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

interface Props {
  agents: Record<AgentId, AgentStatus>;
  funnel: FunnelState;
  metric: Metric | null;
  diversity: DiversityStats | null;
  log: LogEvent[];
  toolTrace: Record<AgentId, ToolCallEvent[]>;
}

const AGENTS: { id: AgentId; name: string; role: string; icon: JSX.Element }[] = [
  { id: "supervisor", name: "Supervisor", role: "plan · route · gate", icon: <Compass size={16} /> },
  { id: "knowledge", name: "Knowledge", role: "cited dossier · actives", icon: <BookOpen size={16} /> },
  { id: "cheminformatics", name: "Cheminformatics", role: "RDKit filters · similarity", icon: <FlaskConical size={16} /> },
  { id: "critic", name: "Critic / Ranking", role: "score · confidence", icon: <ListChecks size={16} /> },
  { id: "diversifier", name: "Diversifier", role: "chemotype spread", icon: <Shuffle size={16} /> },
];

export default function PipelineRail({ agents, funnel, metric, diversity, log, toolTrace }: Props) {
  const latestDiversifierLog =
    [...log].reverse().find((e) => e.agent === "diversifier")?.msg ?? null;

  return (
    <aside className="rail">
      <div className="panel">
        <div className="panel-h">
          <h3>Agent pipeline</h3>
        </div>
        <div className="agents">
          {AGENTS.map((a) => {
            const st = agents[a.id];
            const trace = toolTrace[a.id] || [];
            return (
              <div key={a.id} className={"agent " + st}>
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
                </div>
                {a.id === "diversifier" && (
                  <DiversifierFeedback
                    status={st}
                    diversity={diversity}
                    latestLog={latestDiversifierLog}
                  />
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
    </aside>
  );
}

const MODE_LABEL: Record<DiversityStats["mode"], string> = {
  off: "Off",
  scaffold: "Scaffold",
  mmr: "MMR",
  cluster: "Cluster",
};

function DiversifierFeedback({
  status,
  diversity,
  latestLog,
}: {
  status: AgentStatus;
  diversity: DiversityStats | null;
  latestLog: string | null;
}) {
  if (!diversity && status === "idle") return null;

  return (
    <div className="div-feedback fadeup">
      <div className="div-feedback-head">Diversifier feedback</div>
      {diversity ? (
        <>
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
          {latestLog && <div className="div-feedback-note">{latestLog}</div>}
        </>
      ) : (
        <div className="div-feedback-note">
          {status === "running"
            ? "Computing chemotype spread and re-selecting the shortlist..."
            : "No diversity pass output yet."}
        </div>
      )}
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
    <div className="tool-trace" ref={ref}>
      {trace.map((t, i) => {
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
      })}
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