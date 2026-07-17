import { ShieldCheck, Download, Check } from "lucide-react";

interface Props {
  exported: boolean;
  onApprove: () => void;
  onDownload: (kind: "csv" | "sdf" | "report") => void;
}

export default function ApproveBar({ exported, onApprove, onDownload }: Props) {
  return (
    <div className={"approve fadeup" + (exported ? " exported" : "")}>
      <ShieldCheck size={22} color={exported ? "var(--teal)" : "var(--hit)"} />
      <div className="approve-txt">
        <strong>{exported ? "Shortlist approved" : "Human sign-off required"}</strong>
        <p>
          {exported
            ? "Exported with full provenance. Download below."
            : "Review the ranked shortlist, then approve to export. Nothing leaves without your sign-off."}
        </p>
      </div>

      <div className="approve-actions">
        {!exported ? (
          <button className="btn primary" onClick={onApprove}>
            <Check size={15} strokeWidth={2.5} /> Approve &amp; export
          </button>
        ) : (
          <>
            <button className="btn" onClick={() => onDownload("csv")}>
              <Download size={14} /> CSV
            </button>
            <button className="btn" onClick={() => onDownload("sdf")}>
              <Download size={14} /> SDF
            </button>
            <button className="btn" onClick={() => onDownload("report")}>
              <Download size={14} /> Report
            </button>
          </>
        )}
      </div>
    </div>
  );
}
