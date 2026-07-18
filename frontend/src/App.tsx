import { useCallback, useEffect, useRef, useState } from "react";
import Header from "./components/Header";
import SetupPanel from "./components/SetupPanel";
import PipelineRail from "./components/PipelineRail";
import OutputTabs from "./components/OutputTabs";
import ApproveBar from "./components/ApproveBar";
import ChatPanel from "./components/ChatPanel";
import { runMockStream, buildCsv, type StreamEvent } from "./mock";
import { startRun, subscribe, approveRun, downloadUrl, createSession } from "./api";
import type { AgentId, RunState, LLMHealth, DiversityMode } from "./types";

const EMPTY: RunState = {
  status: "idle",
  agents: { supervisor: "idle", knowledge: "idle", cheminformatics: "idle", critic: "idle", diversifier: "idle" },
  activeAgent: null,
  funnel: { input: 0, filtered: null, ranked: null },
  dossier: "",
  citations: [],
  ranked: [],
  metric: null,
  grounding: null,
  diversity: null,
  log: [],
  targetName: "",
  targetId: "",
  toolTrace: { supervisor: [], knowledge: [], cheminformatics: [], critic: [], diversifier: [] },
  fullTrace: { supervisor: [], knowledge: [], cheminformatics: [], critic: [], diversifier: [] },
};

const TRACE_CAP = 8;

export default function App() {
  const [mode, setMode] = useState<"mock" | "live">("live");
  const [target, setTarget] = useState("EGFR");
  const [file, setFile] = useState<File | null>(null);
  const [diversify, setDiversify] = useState<DiversityMode>("scaffold");
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
  const [chatRunId, setChatRunId] = useState<string | null>(null);
  const [lastSteerAck, setLastSteerAck] = useState<{ message: string; ts: number } | null>(null);

  // The chat is present from the very first screen, so it needs a run_id
  // before any target/library/pipeline exists.
  useEffect(() => {
    let cancelled = false;
    createSession()
      .then(({ runId }) => {
        if (!cancelled) setChatRunId(runId);
      })
      .catch(() => {
        /* chat just stays disabled if this fails — rest of the app is unaffected */
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        case "grounding":
          next.grounding = e.payload;
          break;
        case "diversity":
          next.diversity = e.payload;
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
          const prevFull = s.fullTrace[agentId] || [];
          next.fullTrace = { ...s.fullTrace, [agentId]: [...prevFull, e.payload] };
          break;
        }
      }
      return next;
    });

    // Auto-switch to shortlist tab when results land.
    if (e.type === "ranked") setTab("shortlist");
    // The chat's steer messages are only "confirmed" once this event lands
    // on the pipeline's own SSE stream — see ChatPanel's pendingSteer.
    if (e.type === "steer") setLastSteerAck({ message: e.payload, ts: Date.now() });
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
        const { runId } = await startRun(target, file, diversify);
        runIdRef.current = runId;
        unsubRef.current = subscribe(runId, apply);
        // Point the chat at whichever run is actually live, regardless of
        // which path (this form, or the chat's own start_run) started it.
        setChatRunId(runId);
      } catch (err) {
        setRun((s) => ({
          ...s,
          status: "error",
          log: [...s.log, { ts: Date.now(), agent: "supervisor", msg: String(err) }],
        }));
      }
    }
  }, [mode, target, file, diversify, apply]);

  const onCiteRank = useCallback((rank: number) => {
    setTab("shortlist");
    window.setTimeout(() => {
      const el = document.querySelector(`[data-rank="${rank}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("cite-flash");
        window.setTimeout(() => el.classList.remove("cite-flash"), 1500);
      }
    }, 50);
  }, []);

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
    (kind: "csv" | "sdf" | "report" | "traces") => {
      // Traces are assembled from state already on the client (received live
      // over SSE in both modes) — no backend round-trip either way, unlike
      // csv/sdf/report which are files the backend writes on approval.
      if (kind === "traces") {
        const payload = {
          target: run.targetName,
          generated_at: new Date().toISOString(),
          agents: run.fullTrace,
        };
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${run.targetName || "target"}_traces.json`;
        a.click();
        URL.revokeObjectURL(url);
        return;
      }
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
    [mode, run.ranked, run.targetName, run.fullTrace]
  );

  // Logo/title in the header — abandons the client-side view of the current
  // run (the backend run itself is left alone; there's no cancel endpoint)
  // and drops back to the setup screen so a new triage can be started.
  const goHome = useCallback(() => {
    if (unsubRef.current) unsubRef.current();
    unsubRef.current = null;
    runIdRef.current = null;
    setRun(EMPTY);
    setTab("dossier");
  }, []);

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
      <Header mode={mode} onMode={setMode} status={run.status} onHealthChange={setLlmHealth} onHome={goHome} />
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
            diversify={diversify}
            onDiversify={setDiversify}
          />
        )}

        {started && (
          <>
            <div className="work">
              <PipelineRail
                agents={run.agents}
                funnel={run.funnel}
                metric={run.metric}
                diversity={run.diversity}
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
                grounding={run.grounding}
                diversity={run.diversity}
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

        {/* Same mounted instance throughout — docked below the form pre-run,
            floating (fixed-position) once the pipeline starts. */}
        <ChatPanel
          runId={chatRunId}
          status={run.status}
          lastSteerAck={lastSteerAck}
          onCiteRank={onCiteRank}
          docked={!started}
        />
      </main>
    </>
  );
}