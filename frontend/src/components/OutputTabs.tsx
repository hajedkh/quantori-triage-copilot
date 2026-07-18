import type { Citation, RankedMol, Grounding, DiversityStats } from "../types";
import DossierPanel from "./DossierPanel";
import ResultsTable from "./ResultsTable";

interface Props {
  tab: "dossier" | "shortlist";
  onTab: (t: "dossier" | "shortlist") => void;
  dossier: string;
  citations: Citation[];
  dossierStreaming: boolean;
  ranked: RankedMol[];
  grounding: Grounding | null;
  diversity: DiversityStats | null;
  runId: string | null;
}

export default function OutputTabs({
  tab,
  onTab,
  dossier,
  citations,
  dossierStreaming,
  ranked,
  grounding,
  diversity,
  runId,
}: Props) {
  return (
    <div className="outputs">
      <div className="tabs">
        <button className={tab === "dossier" ? "on" : ""} onClick={() => onTab("dossier")}>
          Dossier
          {citations.length > 0 && <span className="count">{citations.length}</span>}
        </button>
        <button className={tab === "shortlist" ? "on" : ""} onClick={() => onTab("shortlist")}>
          Shortlist
          {ranked.length > 0 && <span className="count">{ranked.length}</span>}
        </button>
      </div>

      {tab === "dossier" ? (
        <DossierPanel text={dossier} citations={citations} streaming={dossierStreaming} grounding={grounding} />
      ) : (
        <ResultsTable ranked={ranked} diversity={diversity} runId={runId} />
      )}
    </div>
  );
}