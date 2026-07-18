import { Sparkles, ShieldCheck, ShieldAlert } from "lucide-react";
import type { Citation, Grounding } from "../types";
import BrandSpinner from "./BrandSpinner";

interface Props {
  text: string;
  citations: Citation[];
  streaming: boolean;
  grounding: Grounding | null;
}

// Renders dossier text, converting [[PMID:xxxx]] markers into clickable links.
function renderWithCitations(text: string) {
  const parts = text.split(/(\[\[PMID:\d+\]\])/g);
  return parts.map((p, i) => {
    const m = p.match(/\[\[PMID:(\d+)\]\]/);
    if (m) {
      const pmid = m[1];
      return (
        <a
          key={i}
          className="cite"
          href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
          target="_blank"
          rel="noreferrer"
        >
          PMID:{pmid}
        </a>
      );
    }
    return <span key={i}>{p}</span>;
  });
}

export default function DossierPanel({ text, citations, streaming, grounding }: Props) {
  if (!text && !streaming) {
    return (
      <div className="panel">
        <div className="empty">The target dossier appears here once the run starts.</div>
      </div>
    );
  }

  const nUngrounded = grounding?.ungrounded?.length ?? 0;
  const nCited = grounding?.cited_pmids?.length ?? 0;
  const groundingBadge =
    grounding && !streaming && nCited > 0 ? (
      nUngrounded === 0 ? (
        <span
          className="grounding-badge ok"
          title="Every cited PMID traces to a provided source abstract"
          style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, color: "var(--teal, #1f9c8e)" }}
        >
          <ShieldCheck size={13} /> {nCited} citation{nCited === 1 ? "" : "s"} grounded
        </span>
      ) : (
        <span
          className="grounding-badge warn"
          title={`Ungrounded PMIDs: ${grounding!.ungrounded.map((u) => u.pmid).join(", ")}`}
          style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, color: "var(--hit, #e0a03a)" }}
        >
          <ShieldAlert size={13} /> {nUngrounded} of {nCited} citation{nCited === 1 ? "" : "s"} ungrounded
        </span>
      )
    ) : null;

  return (
    <div className="panel">
      <div className="dossier">
        <div className="writing">
          {streaming ? <BrandSpinner size={13} label="writing" /> : <Sparkles size={12} />}
          {streaming ? "Knowledge agent writing…" : "Target dossier · grounded in retrieved literature"}
          {groundingBadge && <span style={{ marginLeft: "auto" }}>{groundingBadge}</span>}
        </div>
        <div className="dossier-body">
          {renderWithCitations(text)}
          {streaming && <span className="cursor" />}
        </div>

        {citations.length > 0 && !streaming && (
          <div className="cite-list fadeup">
            <h4>Citations</h4>
            {citations.map((c, i) => (
              <div key={i} className="cite-item">
                <a
                  href={`https://pubmed.ncbi.nlm.nih.gov/${c.pmid}/`}
                  target="_blank"
                  rel="noreferrer"
                >
                  PMID:{c.pmid}
                </a>
                <span>{c.claim}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}