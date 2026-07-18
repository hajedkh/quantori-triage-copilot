import { useEffect, useRef } from "react";
import { BookOpen, FlaskConical, ListChecks, Compass, Shuffle } from "lucide-react";
import type { AgentId, AgentStatus, FunnelState, LogEvent, Metric, ToolCallEvent } from "../types";
import FunnelMeter from "./FunnelMeter";
import BrandSpinner from "./BrandSpinner";

interface Props {
  agents: Record<AgentId, AgentStatus>;
  funnel: FunnelState;
  metric: Metric | null;
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

export default function PipelineRail({ agents, funnel, metric, log, toolTrace }: Props) {
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