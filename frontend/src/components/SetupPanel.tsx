import { useRef } from "react";
import { FileText, Play, X } from "lucide-react";
import type { RankingProfile } from "../types";

interface Props {
  target: string;
  onTarget: (v: string) => void;
  file: File | null;
  onFile: (f: File | null) => void;
  rankingProfile: RankingProfile;
  onRankingProfile: (p: RankingProfile) => void;
  onRun: () => void;
  disabled: boolean;
  disabledReason?: string;
  mode: "mock" | "live";
}

const EXAMPLES = ["EGFR", "BRAF", "ABL1"];

export default function SetupPanel({
  target,
  onTarget,
  file,
  onFile,
  rankingProfile,
  onRankingProfile,
  onRun,
  disabled,
  disabledReason,
  mode,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  // In demo mode the candidate pile is bundled, so a file isn't required.
  const canRun = target.trim().length > 0 && (mode === "mock" || !!file);

  return (
    <section className="setup fadeup">
      <div className="setup-eyebrow">In-silico triage loop</div>
      <h1>Screen a molecule library against a target</h1>
      <p className="lede">
        Name a target protein and hand over a candidate library. The agents build
        a cited dossier, filter the pile with RDKit, and return a ranked,
        explained shortlist — with a human sign-off before anything exports.
      </p>

      <div className="setup-grid">
        <div className="field">
          <label
            htmlFor="target"
            title="Gene symbol or common target name used to fetch actives and literature context"
          >
            Target protein
          </label>
          <input
            id="target"
            type="text"
            placeholder="e.g. EGFR"
            value={target}
            onChange={(e) => onTarget(e.target.value)}
            spellCheck={false}
            title="Gene symbol or common target name used to fetch actives and literature context"
          />
        </div>

        <div className="field">
          <label
            htmlFor="file"
            title={
              mode === "mock"
                ? "Demo mode uses a bundled candidate file"
                : "Upload a CSV/SMI/TXT containing candidate SMILES"
            }
          >
            Candidate library {mode === "mock" ? "(bundled in demo)" : "(CSV of SMILES)"}
          </label>
          <div
            className={"drop" + (file ? " has" : "")}
            title={
              mode === "mock"
                ? "Demo mode uses a bundled file; optional in this mode"
                : "Upload CSV/SMI/TXT with SMILES entries"
            }
          >
            <FileText size={16} />
            <span className="file-meta">
              {file
                ? `${file.name} · ${(file.size / 1024).toFixed(0)} KB`
                : mode === "mock"
                  ? "egfr_candidates.csv · 1,500 molecules"
                  : "No file selected"}
            </span>
            {file ? (
              <button className="link" onClick={() => onFile(null)}>
                <X size={13} /> remove
              </button>
            ) : (
              <button
                className="link"
                onClick={() => inputRef.current?.click()}
                title="Pick a local candidate library file"
              >
                browse
              </button>
            )}
            <input
              ref={inputRef}
              id="file"
              type="file"
              accept=".csv,.smi,.txt"
              hidden
              onChange={(e) => onFile(e.target.files?.[0] ?? null)}
            />
          </div>
        </div>

        <div className="field">
          <label
            htmlFor="ranking-profile"
            title="Choose how scoring balances similarity, quality penalties, and novelty"
          >
            Ranking profile
          </label>
          <select
            id="ranking-profile"
            value={rankingProfile}
            onChange={(e) => onRankingProfile(e.target.value as RankingProfile)}
            title="Choose how scoring balances similarity, quality penalties, and novelty"
          >
            <option value="balanced">Balanced (default)</option>
            <option value="quality">Quality (similarity-focused)</option>
            <option value="explore">Explore (novelty-friendly)</option>
            <option value="strict">Strict (drug-likeness penalties)</option>
          </select>
        </div>

        <button
          className="run-btn"
          onClick={onRun}
          disabled={disabled || !canRun}
          title={
            disabled && disabledReason
              ? disabledReason
              : "Start the full triage pipeline with current settings"
          }
        >
          <Play size={16} strokeWidth={2.5} />
          Run triage
        </button>
      </div>

      <div className="chips">
        <span className="k">Try:</span>
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            className="chip"
            onClick={() => onTarget(ex)}
            title={`Use ${ex} as the target protein`}
          >
            {ex}
          </button>
        ))}
      </div>
    </section>
  );
}