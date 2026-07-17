import { Sparkles } from "lucide-react";
import type { Citation } from "../types";

interface Props {
  text: string;
  citations: Citation[];
  streaming: boolean;
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

export default function DossierPanel({ text, citations, streaming }: Props) {
  if (!text && !streaming) {
    return (
      <div className="panel">
        <div className="empty">The target dossier appears here once the run starts.</div>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="dossier">
        <div className="writing">
          <Sparkles size={12} />
          {streaming ? "Knowledge agent writing…" : "Target dossier · grounded in retrieved literature"}
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
