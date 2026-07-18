import { useState } from "react";
import { Star, ChevronDown, Shuffle, Eye, EyeOff, Box } from "lucide-react";
import type { RankedMol, DiversityStats } from "../types";
import MoleculeView from "./MoleculeView";
import ConfidenceBadge from "./ConfidenceBadge";
import Mol3DModal from "./Mol3DModal";

interface Props {
  ranked: RankedMol[];
  diversity: DiversityStats | null;
  // Only available in LIVE mode once a run exists — the 3D viewer needs a
  // real backend/run to generate a conformer from. null in DEMO mode.
  runId: string | null;
}

function scoreColor(conf: string) {
  return conf === "High" ? "var(--hit)" : conf === "Medium" ? "var(--med)" : "var(--low)";
}

const DIVERSITY_LABEL: Record<string, string> = {
  off: "score order",
  scaffold: "scaffold-diverse",
  mmr: "MMR",
  cluster: "clustered",
};

export default function ResultsTable({ ranked, diversity, runId }: Props) {
  const [open, setOpen] = useState<number | null>(null);
  // Ground-truth labels stay hidden by default so the shortlist reads as a
  // real (unlabelled) triage; the operator can reveal the held-out actives.
  const [reveal, setReveal] = useState(false);
  const [view3D, setView3D] = useState<number | null>(null);
  // Set once, the first time molstar's own import fails — after that every
  // row's 3D button disables itself instead of re-attempting a known-broken
  // import on every click.
  const [molstar3DBroken, setMolstar3DBroken] = useState(false);

  if (ranked.length === 0) {
    return (
      <div className="panel">
        <div className="empty">
          The ranked shortlist appears here after the Critic agent scores the survivors.
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      <div
        className="results-toolbar"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 12px",
          fontSize: 11.5,
          color: "var(--fg-dim, #9aa4b2)",
        }}
      >
        {diversity && (
          <span
            className="diversity-chip"
            title={`Diversifier: ${diversity.mode}${diversity.mode === "mmr" ? ` (λ=${diversity.lambda})` : ""}`}
            style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
          >
            <Shuffle size={12} />
            {diversity.n_scaffolds != null ? `${diversity.n_scaffolds} scaffolds` : "diversity"}
            {" · "}
            {DIVERSITY_LABEL[diversity.mode] ?? diversity.mode}
            {diversity.n_clusters != null ? ` · ${diversity.n_clusters} clusters` : ""}
          </span>
        )}
        <button
          className="reveal-toggle"
          onClick={() => setReveal((r) => !r)}
          title="Reveal or hide the held-out known actives (validation ground truth)"
          style={{
            marginLeft: "auto",
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            background: "transparent",
            border: "1px solid var(--line, #2a2f3a)",
            borderRadius: 6,
            padding: "3px 8px",
            color: "inherit",
            cursor: "pointer",
          }}
        >
          {reveal ? <EyeOff size={12} /> : <Eye size={12} />}
          {reveal ? "Hide validation" : "Reveal validation"}
        </button>
      </div>
      <div className="results">
        <div className="rrow head">
          <span>Rank</span>
          <span>Structure</span>
          <span>Molecule</span>
          <span className="col-score">Score</span>
          <span>Confidence</span>
        </div>

        {ranked.map((m) => {
          const isOpen = open === m.rank;
          return (
            <div key={m.rank}>
              <div
                className={"rrow data" + (isOpen ? " open" : "")}
                data-rank={m.rank}
                onClick={() => setOpen(isOpen ? null : m.rank)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setOpen(isOpen ? null : m.rank);
                }}
              >
                <div className="rank-badge">
                  {m.rank}
                  {reveal && m.is_known_active && (
                    <Star size={12} className="known-star" fill="currentColor" />
                  )}
                </div>

                <div className="mol-cell">
                  <MoleculeView smiles={m.smiles} />
                  {runId && (
                    <button
                      className="mol3d-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setView3D(m.rank);
                      }}
                      disabled={molstar3DBroken}
                      title={molstar3DBroken ? "3D viewer unavailable" : "View 3D structure"}
                      aria-label="View 3D structure"
                    >
                      <Box size={11} />
                    </button>
                  )}
                </div>

                <div style={{ minWidth: 0 }}>
                  <div className="smiles-txt">{m.smiles}</div>
                  <div className="smiles-sub">
                    ~{m.max_similarity.toFixed(2)} Tanimoto · nearest {m.nearest_active}
                  </div>
                </div>

                <div className="col-score score-bar-wrap">
                  <span className="score-val" style={{ color: scoreColor(m.confidence) }}>
                    {m.score.toFixed(3)}
                  </span>
                  <div className="score-track">
                    <div
                      className="score-fill"
                      style={{ width: `${m.score * 100}%`, background: scoreColor(m.confidence) }}
                    />
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <ConfidenceBadge level={m.confidence} />
                  <ChevronDown
                    size={14}
                    style={{
                      color: "var(--fg-faint)",
                      transform: isOpen ? "rotate(180deg)" : "none",
                      transition: "transform 0.2s",
                    }}
                  />
                </div>
              </div>

              {isOpen && (
                <div className="prov fadeup">
                  <div className="prov-mol">
                    <MoleculeView smiles={m.smiles} size={130} height={120} />
                    {runId && (
                      <button
                        className="mol3d-btn mol3d-btn-lg"
                        onClick={(e) => {
                          e.stopPropagation();
                          setView3D(m.rank);
                        }}
                        disabled={molstar3DBroken}
                        title={molstar3DBroken ? "3D viewer unavailable" : "View 3D structure"}
                        aria-label="View 3D structure"
                      >
                        <Box size={16} />
                      </button>
                    )}
                  </div>
                  <div className="prov-facts">
                    <div className="prov-reason">{m.reason}</div>
                    <div className="prov-kv">
                      <span className="kv">
                        similarity <b>{m.max_similarity.toFixed(2)}</b>
                      </span>
                      <span className="kv">
                        nearest active <b>{m.nearest_active}</b>
                      </span>
                      <span className="kv">
                        score <b>{m.score.toFixed(3)}</b>
                      </span>
                      <span className="kv">
                        confidence <b>{m.confidence}</b>
                      </span>
                      {reveal && m.is_known_active && (
                        <span className="kv" style={{ borderColor: "var(--hit)", color: "var(--hit)" }}>
                          ★ known active (ground truth)
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {view3D !== null &&
        runId &&
        (() => {
          const mol = ranked.find((r) => r.rank === view3D);
          if (!mol) return null;
          return (
            <Mol3DModal
              runId={runId}
              rank={view3D}
              smiles={mol.smiles}
              score={mol.score}
              confidence={mol.confidence}
              onClose={() => setView3D(null)}
              onMolstarUnavailable={() => setMolstar3DBroken(true)}
            />
          );
        })()}
    </div>
  );
}