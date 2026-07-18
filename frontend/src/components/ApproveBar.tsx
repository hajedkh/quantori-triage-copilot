import { useState } from "react";
import { ShieldCheck, Download, Check, ListTree } from "lucide-react";
import type { DiversityMode, DiversifyRequest, ExportProgress, RankingProfile } from "../types";
import BrandSpinner from "./BrandSpinner";

const APPROVE_STAGES = [
  "Preparing CSV summary for selected compounds",
  "Checking ChEMBL and PubChem for selected structures",
  "Preparing 3D structures for .sdf",
  "Assembling report with rationale and citations",
  "Finalizing downloads",
];

const STAGE_TO_INDEX: Record<string, number> = {
  start: 0,
  csv_prepare: 0,
  crossref_scope: 1,
  crossref_lookup: 1,
  csv_done: 1,
  sdf_prepare: 2,
  report_prepare: 3,
  finalize: 4,
  done: 4,
};

interface Props {
  exported: boolean;
  onApprove: (rankingProfile: RankingProfile) => Promise<void> | void;
  onDiversify: (req: DiversifyRequest) => Promise<void> | void;
  onRankingProfileChange: (rankingProfile: RankingProfile) => Promise<void> | void;
  canDiversify: boolean;
  onDownload: (kind: "csv" | "sdf" | "report" | "traces") => void;
  exportProgress: ExportProgress | null;
}

export default function ApproveBar({
  exported,
  onApprove,
  onDiversify,
  onRankingProfileChange,
  canDiversify,
  onDownload,
  exportProgress,
}: Props) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<DiversityMode>("scaffold");
  const [lam, setLam] = useState(0.7);
  const [cutoff, setCutoff] = useState(0.35);
  const [maxGenerated, setMaxGenerated] = useState(200);
  const [rankingProfile, setRankingProfile] = useState<RankingProfile>("balanced");
  const [busy, setBusy] = useState(false);
  const [approving, setApproving] = useState(false);

  const submitDiversify = async () => {
    setBusy(true);
    try {
      const req: DiversifyRequest = {
        mode,
        maxGenerated,
        rankingProfile,
      };
      if (mode === "mmr") req.lam = lam;
      if (mode === "cluster") req.cutoff = cutoff;
      await onDiversify(req);
      setOpen(false);
    } catch (err) {
      alert(`Couldn't start diversification rerun: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const changeRankingProfile = async (next: RankingProfile) => {
    const prev = rankingProfile;
    setRankingProfile(next);
    if (exported) return;
    setBusy(true);
    try {
      await onRankingProfileChange(next);
    } catch (err) {
      setRankingProfile(prev);
      alert(`Couldn't rerank shortlist: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const submitApprove = async () => {
    if (approving) return;
    setApproving(true);
    try {
      await onApprove(rankingProfile);
    } finally {
      setApproving(false);
    }
  };

  const currentStageMessage = exportProgress?.message || APPROVE_STAGES[0];
  const stageIndex = STAGE_TO_INDEX[exportProgress?.stage || "start"] ?? 0;

  return (
    <>
      <div className={"approve fadeup" + (exported ? " exported" : "") + (approving ? " approving" : "")}>
        {approving ? (
          <BrandSpinner size={22} label="creating downloads" />
        ) : (
          <ShieldCheck size={22} color={exported ? "var(--teal)" : "var(--hit)"} />
        )}
        <div className="approve-txt">
          <strong>
            {approving
              ? "Creating downloads"
              : exported
                ? "Shortlist approved"
                : "Human sign-off required"}
          </strong>
          <p>
            {approving
              ? currentStageMessage
              : exported
                ? "Exported with full provenance. Download below."
                : "Review the ranked shortlist, then either approve to export or run one more diversification pass before re-filtering and re-ranking."}
          </p>
          {approving && (
            <div className="approve-task-list" aria-live="polite">
              {APPROVE_STAGES.map((task, idx) => (
                <div key={task} className={"approve-task " + (idx < stageIndex ? "done" : idx === stageIndex ? "active" : "pending")}>
                  {idx < stageIndex ? "✓" : idx === stageIndex ? "•" : "○"} {task}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="approve-actions">
          {!exported ? (
            <>
              {!approving && (
                <button
                  className="btn primary"
                  onClick={submitApprove}
                  disabled={busy || approving}
                  title="Finalize shortlist and export CSV, SDF, and report artifacts"
                >
                  <Check size={15} strokeWidth={2.5} /> Approve &amp; export
                </button>
              )}
            </>
          ) : (
            <>
              <button className="btn" onClick={() => onDownload("csv")} title="Download shortlist table as CSV">
                <Download size={14} /> CSV
              </button>
              <button className="btn" onClick={() => onDownload("sdf")} title="Download structures as SDF with 3D conformers">
                <Download size={14} /> SDF
              </button>
              <button className="btn" onClick={() => onDownload("report")} title="Download markdown report with rationale and citations">
                <Download size={14} /> Report
              </button>
              <button className="btn" onClick={() => onDownload("traces")} title="Full tool-call trace, every agent, uncapped">
                <ListTree size={14} /> Audit Trail
              </button>
            </>
          )}
        </div>

        {!exported && !approving && (
          <div className="approve-ranking-row">
            <label title="Select profile used to rescore the final shortlist before export">
              Ranking profile for final shortlist
              <select
                value={rankingProfile}
                onChange={(e) => void changeRankingProfile(e.target.value as RankingProfile)}
                disabled={busy}
                title="Re-score shortlist at the gate with a different ranking policy"
              >
                <option value="balanced">Balanced (default)</option>
                <option value="quality">Quality (similarity-focused)</option>
                <option value="explore">Explore (novelty-friendly)</option>
                <option value="strict">Strict (drug-likeness penalties)</option>
              </select>
            </label>
            <button
              className="btn"
              onClick={() => setOpen(true)}
              disabled={!canDiversify || busy || approving}
              title={
                canDiversify
                  ? "Generate new compounds and re-run filtering/ranking"
                  : "Diversification rerun is available in live mode"
              }
            >
              Diversify &amp; rerun
            </button>
          </div>
        )}
      </div>

      {open && (
        <div className="div-modal-wrap" role="dialog" aria-modal="true" aria-label="Diversify and rerun settings">
          <div className="div-modal">
            <h4>Diversify &amp; rerun settings</h4>

            <label title="Profile used after diversification rerun">
              Ranking profile
              <select
                value={rankingProfile}
                onChange={(e) => setRankingProfile(e.target.value as RankingProfile)}
                title="Profile used after diversification rerun"
              >
                <option value="balanced">Balanced (default)</option>
                <option value="quality">Quality (similarity-focused)</option>
                <option value="explore">Explore (novelty-friendly)</option>
                <option value="strict">Strict (drug-likeness penalties)</option>
              </select>
            </label>

            <label title="How shortlist diversity is selected before retriage">
              Method
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as DiversityMode)}
                title="How shortlist diversity is selected before retriage"
              >
                <option value="scaffold">Scaffold (Bemis-Murcko)</option>
                <option value="mmr">MMR</option>
                <option value="cluster">Cluster (Butina)</option>
                <option value="off">Off (generate from top-scored seeds)</option>
              </select>
            </label>

            {mode === "mmr" && (
              <label title="MMR tradeoff: higher favors quality, lower favors novelty">
                MMR lambda (0-1)
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={lam}
                  onChange={(e) => setLam(Number(e.target.value))}
                  title="MMR tradeoff: higher favors quality, lower favors novelty"
                />
              </label>
            )}

            {mode === "cluster" && (
              <label title="Lower values create more clusters and increase diversity spread">
                Cluster cutoff (distance)
                <input
                  type="number"
                  min={0.1}
                  max={0.9}
                  step={0.05}
                  value={cutoff}
                  onChange={(e) => setCutoff(Number(e.target.value))}
                  title="Lower values create more clusters (more diversity)"
                />
              </label>
            )}

            <label title="Upper bound for generated candidates before retriage">
              Max new compounds
              <input
                type="number"
                min={10}
                max={5000}
                step={10}
                value={maxGenerated}
                onChange={(e) => setMaxGenerated(Math.max(1, Number(e.target.value) || 1))}
                title="Upper bound for generated candidates before retriage"
              />
            </label>

            <div className="div-modal-actions">
              <button className="btn" onClick={() => setOpen(false)} disabled={busy} title="Close without starting a rerun">Cancel</button>
              <button className="btn primary" onClick={submitDiversify} disabled={busy} title="Start diversification then rerun screening and ranking">
                {busy ? "Starting..." : "Run diversification"}
              </button>
            </div>
          </div>
        </div>
      )}

    </>
  );
}
