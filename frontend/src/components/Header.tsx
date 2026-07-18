import { useEffect, useRef, useState } from "react";
import type { RunStatus, LLMProvider, LLMOptions, LLMHealth } from "../types";
import { getLLMConfig, setLLMConfig, getLLMHealth } from "../api";
import { QUANTORI_LOGO } from "../brand";

interface Props {
  mode: "mock" | "live";
  onMode: (m: "mock" | "live") => void;
  status: RunStatus;
  onHealthChange: (health: LLMHealth) => void;
  onHome: () => void;
}

const STATUS_TEXT: Record<RunStatus, string> = {
  idle: "Ready",
  running: "Running",
  awaiting_approval: "Awaiting approval",
  exported: "Exported",
  error: "Error",
};

const PROVIDER_LABEL: Record<LLMProvider, string> = {
  ollama: "Ollama (local)",
  gateway: "Quantori Litellm",
};

// Native <option> elements can only render plain text — no <img> inside a
// dropdown list item — so the real logo is shown as a small badge in the
// control bar instead, swapped based on the selected provider. (Logo URL
// now lives in ../brand — also reused by BrandSpinner for loading states.)

const EMPTY_OPTIONS: LLMOptions = { ollama: [], gateway: [] };
const EMPTY_HEALTH: LLMHealth = { status: "checking", ok: false, latency_ms: 0, error: null };

export default function Header({ mode, onMode, status, onHealthChange, onHome }: Props) {
  const [provider, setProvider] = useState<LLMProvider>("ollama");
  const [model, setModel] = useState("");
  const [options, setOptions] = useState<LLMOptions>(EMPTY_OPTIONS);
  const [health, setHealth] = useState<LLMHealth>(EMPTY_HEALTH);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);

  // Is the FastAPI backend itself reachable at all (separate from whether an
  // LLM provider behind it is healthy). Colors the "Ready" status dot.
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const res = await fetch("/api/");
        if (!cancelled) setBackendUp(res.ok);
      } catch {
        if (!cancelled) setBackendUp(false);
      }
    };
    check();
    const id = window.setInterval(check, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const showToast = (msg: string) => {
    setToast(msg);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4500);
  };

  // Health-check whichever provider is currently selected in the dropdown.
  const refreshHealth = async (p: LLMProvider, modelOptions: LLMOptions) => {
    if (modelOptions[p].length === 0) {
      const h: LLMHealth = {
        status: "down",
        ok: false,
        latency_ms: 0,
        error: `no models available for ${p} — is it reachable?`,
      };
      setHealth(h);
      onHealthChange(h);
      return;
    }
    setHealth((s) => ({ ...s, status: "checking" }));
    try {
      const r = await getLLMHealth(p);
      const h: LLMHealth = { status: r.ok ? "ok" : "down", ok: r.ok, latency_ms: r.latency_ms, error: r.error };
      setHealth(h);
      onHealthChange(h);
    } catch (err) {
      const h: LLMHealth = { status: "down", ok: false, latency_ms: 0, error: String(err) };
      setHealth(h);
      onHealthChange(h);
    }
  };

  // Initial load.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cfg = await getLLMConfig();
        if (cancelled) return;
        setProvider(cfg.provider);
        setModel(cfg.model);
        setOptions(cfg.options);
        await refreshHealth(cfg.provider, cfg.options);
      } catch {
        if (!cancelled) {
          const h: LLMHealth = { status: "down", ok: false, latency_ms: 0, error: "backend unreachable" };
          setHealth(h);
          onHealthChange(h);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll every 10s, and immediately whenever the selected provider changes.
  useEffect(() => {
    refreshHealth(provider, options);
    const id = window.setInterval(() => refreshHealth(provider, options), 10_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, options]);

  const handleProviderChange = async (next: LLMProvider) => {
    const prevProvider = provider;
    const prevModel = model;
    const nextModels = options[next];

    setProvider(next);

    if (nextModels.length === 0) {
      // Nothing valid to switch to server-side — just reflect the selection
      // locally so the health check can show *why* it's down.
      setModel("");
      return;
    }

    const nextModel = nextModels[0];
    setModel(nextModel);
    try {
      const res = await setLLMConfig(next, nextModel);
      setProvider(res.provider);
      setModel(res.model);
      setOptions(res.options);
    } catch (err) {
      setProvider(prevProvider);
      setModel(prevModel);
      showToast(`Couldn't switch to ${PROVIDER_LABEL[next]}: ${String(err)}`);
    }
  };

  const handleModelChange = async (next: string) => {
    const prevModel = model;
    setModel(next);
    try {
      const res = await setLLMConfig(provider, next);
      setModel(res.model);
    } catch (err) {
      setModel(prevModel);
      showToast(`Couldn't switch model: ${String(err)}`);
    }
  };

  const dotClass = "llm-dot " + (health.status === "ok" ? "ok" : health.status === "down" ? "down" : "checking");
  const dotTitle =
    health.status === "checking"
      ? "Checking…"
      : health.ok
        ? `Healthy · ${health.latency_ms}ms`
        : `Down${health.error ? " · " + health.error : ""}`;

  const demoLocked = mode === "mock";
  const currentModels = options[provider];

  const dotClassStatus =
    status === "running"
      ? "dot live"
      : status === "awaiting_approval"
        ? "dot wait"
        : status === "exported"
          ? "dot done"
          : status === "idle"
            ? backendUp === true
              ? "dot up"
              : backendUp === false
                ? "dot down"
                : "dot"
            : "dot";

  return (
    <header className="hdr">
      <button className="hdr-brand" onClick={onHome} title="Back to setup — start a new triage">
        <div className="hdr-mark">
          <img className="hdr-mark-logo" src={QUANTORI_LOGO} alt="" width={20} height={20} />
        </div>
        <div>
          <div className="hdr-title">Quantori Triage Copilot</div>
          <div className="hdr-sub">in-silico screening · triage, not oracle</div>
        </div>
      </button>

      <div className="hdr-right">
        <div
          className="mode-toggle"
          role="group"
          aria-label="Run mode"
          style={{
            display: "inline-flex",
            border: "1px solid var(--line, #2a2f3a)",
            borderRadius: 8,
            overflow: "hidden",
            marginRight: 10,
          }}
        >
          {(["mock", "live"] as const).map((m) => {
            const active = mode === m;
            return (
              <button
                key={m}
                onClick={() => onMode(m)}
                title={m === "mock" ? "Demo mode — no backend needed" : "Live mode — talks to the backend"}
                style={{
                  padding: "5px 12px",
                  fontSize: 11.5,
                  fontWeight: 600,
                  letterSpacing: 0.4,
                  border: "none",
                  cursor: "pointer",
                  background: active ? "var(--teal, #1f9c8e)" : "transparent",
                  color: active ? "#fff" : "var(--fg-dim, #9aa4b2)",
                }}
              >
                {m === "mock" ? "DEMO" : "LIVE"}
              </button>
            );
          })}
        </div>

        <div className="llm-ctl" title={demoLocked ? "Provider switching applies to LIVE runs." : undefined}>
          <span className={dotClass} title={dotTitle} />
          {provider === "gateway" ? (
            <img className="llm-logo" src={QUANTORI_LOGO} alt="" title="Quantori Litellm Gateway" />
          ) : (
            <span className="llm-logo llm-logo-emoji" title="Ollama (local)">
              🖥
            </span>
          )}
          <select
            value={provider}
            disabled={demoLocked}
            onChange={(e) => handleProviderChange(e.target.value as LLMProvider)}
          >
            <option value="ollama">{PROVIDER_LABEL.ollama}</option>
            <option value="gateway">{PROVIDER_LABEL.gateway}</option>
          </select>
          <select
            value={model}
            disabled={demoLocked || currentModels.length === 0}
            onChange={(e) => handleModelChange(e.target.value)}
          >
            {currentModels.length === 0 ? (
              <option value="">no models available</option>
            ) : (
              currentModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))
            )}
          </select>
        </div>

        <span className="status-pill">
          <span className={dotClassStatus} />
          {STATUS_TEXT[status]}
        </span>
      </div>

      {toast && <div className="llm-toast fadeup">{toast}</div>}
    </header>
  );
}