import { useState } from "react";
import { Star, ChevronDown } from "lucide-react";
import type { RankedMol } from "../types";
import MoleculeView from "./MoleculeView";
import ConfidenceBadge from "./ConfidenceBadge";

interface Props {
  ranked: RankedMol[];
}

function scoreColor(conf: string) {
  return conf === "High" ? "var(--hit)" : conf === "Medium" ? "var(--med)" : "var(--low)";
}

export default function ResultsTable({ ranked }: Props) {
  const [open, setOpen] = useState<number | null>(null);

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
                  {m.is_known_active && (
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
                      {m.is_known_active && (
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
