import { useState } from "react";
import { Star, ChevronDown, Shuffle, Eye, EyeOff } from "lucide-react";
import type { RankedMol, DiversityStats } from "../types";
import MoleculeView from "./MoleculeView";
import ConfidenceBadge from "./ConfidenceBadge";

interface Props {
  ranked: RankedMol[];
  diversity: DiversityStats | null;
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

export default function ResultsTable({ ranked, diversity }: Props) {
  const [open, setOpen] = useState<number | null>(null);
  // Ground-truth labels stay hidden by default so the shortlist reads as a
  // real (unlabelled) triage; the operator can reveal the held-out actives.
  const [reveal, setReveal] = useState(false);

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
                </div>

                <div style={{ minWidth: 0 }}>
                  <div className="smiles-txt">{m.smiles}</div>
                  <div className="smiles-sub">
                    ~{m.max_similarity.toFixed(2)} Tanimoto · nearest {m.nearest_active}
                  </div>
                </div>

                <div className="col-score score-bar-wrap">
                  <span className="score-val" style={{ color: scoreColor(m.confidence) }}>
                    {m.score.toFixed(2)}
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
                        score <b>{m.score.toFixed(2)}</b>
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
    </div>
  );
}