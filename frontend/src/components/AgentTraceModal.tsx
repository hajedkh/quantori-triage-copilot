import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import type { AgentStatus, ToolCallEvent } from "../types";
import { renderTraceRow } from "./PipelineRail";

interface Props {
  name: string;
  role: string;
  icon: JSX.Element;
  status: AgentStatus;
  trace: ToolCallEvent[];
  // This agent's own log lines (e.g. Supervisor/Knowledge/Diversifier have
  // no tool_call events at all — they're not LLM tool-calling loops — so the
  // modal falls back to their log messages instead of an empty trace.
  logLines: string[];
  // Agent-specific content shown above the trace/log — e.g. the Diversifier's
  // stat grid (mode/selected/scaffolds/clusters/lambda). Optional: most
  // agents don't have anything beyond their trace.
  extra?: JSX.Element | null;
  onClose: () => void;
}

// Full, uncapped trace for one agent — opened by clicking its card in the
// rail. Reuses the same row rendering as the inline (capped) trace so the
// two views never show different formatting for the same event.
export default function AgentTraceModal({ name, role, icon, status, trace, logLines, extra, onClose }: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Jump to the latest entry on open, then keep following the bottom while
  // the agent is still running — once it's done, let the operator scroll freely.
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [trace.length, logLines.length, status]);

  const hasTrace = trace.length > 0;
  const hasLog = logLines.length > 0;

  // Portal straight onto <body> — .rail (this modal's natural DOM ancestor)
  // is `position: sticky`, which (unlike relative/absolute) creates its own
  // stacking context unconditionally. Rendered as a normal child, this modal's
  // fixed position + z-index would still get trapped inside that stacking
  // context, so a later DOM sibling with no z-index at all (OutputTabs, i.e.
  // the dossier/citations) would still paint over it. Escaping via a portal
  // sidesteps the ancestor entirely.
  return createPortal(
    <div className="trace-modal-backdrop" onClick={onClose}>
      <div
        className="trace-modal fadeup"
        role="dialog"
        aria-modal="true"
        aria-label={`${name} trace`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="trace-modal-h">
          <div className="trace-modal-ico">{icon}</div>
          <div className="trace-modal-copy">
            <div className="trace-modal-name">{name}</div>
            <div className="trace-modal-role">{role}</div>
          </div>
          <span className={"status-pill trace-modal-status " + status}>
            <span className={"dot " + (status === "running" ? "live" : status === "done" ? "done" : "")} />
            {status === "running" ? "active" : status === "done" ? "done" : "idle"}
          </span>
          <button className="trace-modal-close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div className="trace-modal-body" ref={bodyRef}>
          {extra}
          {!hasTrace && !hasLog && !extra ? (
            <div className="empty" style={{ padding: "40px 22px" }}>
              Nothing recorded yet.
            </div>
          ) : (
            <>
              {hasTrace && trace.map(renderTraceRow)}
              {hasLog &&
                logLines.map((msg, i) => (
                  <div key={"log-" + i} className="tt-row fadeup">
                    <div className="tt-call">
                      <span className="tt-icon">🧬</span>
                      <span>{msg}</span>
                    </div>
                  </div>
                ))}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
