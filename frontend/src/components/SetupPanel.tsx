import { useRef } from "react";
import { FileText, Play, X } from "lucide-react";

interface Props {
  target: string;
  onTarget: (v: string) => void;
  file: File | null;
  onFile: (f: File | null) => void;
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
          <label htmlFor="target">Target protein</label>
          <input
            id="target"
            type="text"
            placeholder="e.g. EGFR"
            value={target}
            onChange={(e) => onTarget(e.target.value)}
            spellCheck={false}
          />
        </div>

        <div className="field">
          <label htmlFor="file">
            Candidate library {mode === "mock" ? "(bundled in demo)" : "(CSV of SMILES)"}
          </label>
          <div className={"drop" + (file ? " has" : "")}>
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
              <button className="link" onClick={() => inputRef.current?.click()}>
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

        <button
          className="run-btn"
          onClick={onRun}
          disabled={disabled || !canRun}
          title={disabled && disabledReason ? disabledReason : undefined}
        >
          <Play size={16} strokeWidth={2.5} />
          Run triage
        </button>
      </div>

      <div className="chips">
        <span className="k">Try:</span>
        {EXAMPLES.map((ex) => (
          <button key={ex} className="chip" onClick={() => onTarget(ex)}>
            {ex}
          </button>
        ))}
      </div>
    </section>
  );
}
