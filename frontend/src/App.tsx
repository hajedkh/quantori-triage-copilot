import { useCallback, useRef, useState } from "react";
import Header from "./components/Header";
import SetupPanel from "./components/SetupPanel";
import PipelineRail from "./components/PipelineRail";
import OutputTabs from "./components/OutputTabs";
import ApproveBar from "./components/ApproveBar";
import { runMockStream, buildCsv, type StreamEvent } from "./mock";
import { startRun, subscribe, approveRun, downloadUrl } from "./api";
import type { AgentId, RunState, LLMHealth } from "./types";

const EMPTY: RunState = {
  status: "idle",
  agents: { supervisor: "idle", knowledge: "idle", cheminformatics: "idle", critic: "idle" },
  activeAgent: null,
  funnel: { input: 0, filtered: null, ranked: null },
  dossier: "",
  citations: [],
  ranked: [],
  metric: null,
  log: [],
  targetName: "",
  targetId: "",
  toolTrace: { supervisor: [], knowledge: [], cheminformatics: [], critic: [] },
};

const TRACE_CAP = 8;

export default function App() {
  const [mode, setMode] = useState<"mock" | "live">("live");
  const [target, setTarget] = useState("EGFR");
  const [file, setFile] = useState<File | null>(null);
  const [tab, setTab] = useState<"dossier" | "shortlist">("dossier");
  const [run, setRun] = useState<RunState>(EMPTY);
  const [llmHealth, setLlmHealth] = useState<LLMHealth>({
    status: "checking",
    ok: false,
    latency_ms: 0,
    error: null,
  });
  const runIdRef = useRef<string | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);

  // Single event handler for both mock and live streams.
  const apply = useCallback((e: StreamEvent) => {
    setRun((s) => {
      const next = { ...s };
      switch (e.type) {
        case "agent_start":
          next.agents = { ...s.agents, [e.agent as AgentId]: "running" };
          next.activeAgent = e.agent as AgentId;
          next.status = "running";
          break;
        case "agent_done":
          next.agents = { ...s.agents, [e.agent as AgentId]: "done" };
          break;
        case "target_resolved":
          next.targetId = e.payload.id;
          break;
        case "funnel":
          next.funnel = e.payload;
          break;
        case "dossier_token":
          next.dossier = s.dossier + e.payload;
          break;
        case "citations":
          next.citations = e.payload;
          break;
        case "ranked":
          next.ranked = e.payload;
          break;
        case "metric":
          next.metric = e.payload;
          break;
        case "log":
          next.log = [
            ...s.log,
            { ts: Date.now(), agent: e.agent as AgentId, msg: e.payload },
          ];
          break;
        case "awaiting_approval":
          next.status = "awaiting_approval";
          break;
        case "tool_call": {
          const agentId = e.agent as AgentId;
          const prevTrace = s.toolTrace[agentId] || [];
          next.toolTrace = {
            ...s.toolTrace,
            [agentId]: [...prevTrace, e.payload].slice(-TRACE_CAP),
          };
          break;
        }
      }
      return next;
    });

    // Auto-switch to shortlist tab when results land.
    if (e.type === "ranked") setTab("shortlist");
  }, []);

  const start = useCallback(async () => {
    // reset
    if (unsubRef.current) unsubRef.current();
    setRun({ ...EMPTY, status: "running", targetName: target, funnel: { input: 0, filtered: null, ranked: null } });
    setTab("dossier");

    if (mode === "mock") {
      runMockStream(apply);
    } else {
      try {
        if (!file) throw new Error("Select a candidate CSV for live mode.");
        const { runId } = await startRun(target, file);
        runIdRef.current = runId;
        unsubRef.current = subscribe(runId, apply);
      } catch (err) {
        setRun((s) => ({
          ...s,
          status: "error",
          log: [...s.log, { ts: Date.now(), agent: "supervisor", msg: String(err) }],
        }));
      }
    }
  }, [mode, target, file, apply]);

  const approve = useCallback(async () => {
    if (mode === "live" && runIdRef.current) {
      try {
        await approveRun(runIdRef.current);
      } catch {
        /* surfaced below via status */
      }
    }
    setRun((s) => ({ ...s, status: "exported" }));
  }, [mode]);

  const download = useCallback(
    (kind: "csv" | "sdf" | "report") => {
      if (mode === "live" && runIdRef.current) {
        window.open(downloadUrl(runIdRef.current, kind), "_blank");
        return;
      }
      // Demo mode: generate the CSV client-side so the button really works.
      if (kind === "csv") {
        const blob = new Blob([buildCsv(run.ranked)], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${run.targetName || "target"}_shortlist.csv`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        alert(`${kind.toUpperCase()} export is produced by the backend in live mode.`);
      }
    },
    [mode, run.ranked, run.targetName]
  );

  const started = run.status !== "idle";
  const dossierStreaming = run.agents.knowledge === "running";
  const llmBlocked = mode === "live" && !llmHealth.ok;
  const runDisabledReason =
    run.status === "running"
      ? undefined
      : llmBlocked
      ? `LLM provider is down${llmHealth.error ? " — " + llmHealth.error : ""}. Fix the provider or switch to DEMO mode.`
      : undefined;

  return (
    <>
      <Header mode={mode} onMode={setMode} status={run.status} onHealthChange={setLlmHealth} />
      <main className="shell">
        {!started && (
          <SetupPanel
            target={target}
            onTarget={setTarget}
            file={file}
            onFile={setFile}
            onRun={start}
            disabled={run.status === "running" || llmBlocked}
            disabledReason={runDisabledReason}
            mode={mode}
          />
        )}

        {started && (
          <>
            <div className="work">
              <PipelineRail
                agents={run.agents}
                funnel={run.funnel}
                metric={run.metric}
                log={run.log}
                toolTrace={run.toolTrace}
              />
              <OutputTabs
                tab={tab}
                onTab={setTab}
                dossier={run.dossier}
                citations={run.citations}
                dossierStreaming={dossierStreaming}
                ranked={run.ranked}
              />
            </div>

            {(run.status === "awaiting_approval" || run.status === "exported") && (
              <ApproveBar
                exported={run.status === "exported"}
                onApprove={approve}
                onDownload={download}
              />
            )}
          </>
        )}
      </main>
    </>
  );
}
