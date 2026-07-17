"""Export writers — CSV, SDF, and a Markdown report with the dossier + citations."""

from __future__ import annotations
import csv
from pathlib import Path
from rdkit import Chem


def write_csv(path: Path, ranked: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "rank",
                "smiles",
                "score",
                "confidence",
                "nearest_active",
                "max_similarity",
                "reason",
            ]
        )
        for r in ranked:
            w.writerow(
                [
                    r["rank"],
                    r["smiles"],
                    r["score"],
                    r["confidence"],
                    r["nearest_active"],
                    r["max_similarity"],
                    r["reason"],
                ]
            )


def write_sdf(path: Path, ranked: list[dict]) -> None:
    w = Chem.SDWriter(str(path))
    for r in ranked:
        mol = Chem.MolFromSmiles(r["smiles"])
        if mol is None:
            continue
        mol.SetProp("rank", str(r["rank"]))
        mol.SetProp("score", f'{r["score"]:.3f}')
        mol.SetProp("confidence", r["confidence"])
        mol.SetProp("nearest_active", str(r["nearest_active"]))
        w.write(mol)
    w.close()


def write_report(
    path: Path,
    target: str,
    dossier: str,
    citations: list[dict],
    ranked: list[dict],
    metric: dict | None,
) -> None:
    lines = [f"# Target Triage Report — {target}", ""]
    if metric:
        lines += [
            f"**Validation:** recovered {metric['recovered']} / {metric['total_actives']} "
            f"known actives in the top {metric['top_n']} (from {metric['screened']} screened).",
            "",
        ]
    lines += [
        "## Target dossier",
        "",
        dossier.replace("[[PMID:", "[PMID:").replace("]]", "]"),
        "",
    ]
    if citations:
        lines += ["### Citations", ""]
        for c in citations:
            lines.append(
                f"- [PMID:{c['pmid']}](https://pubmed.ncbi.nlm.nih.gov/{c['pmid']}/) — {c['claim']}"
            )
        lines.append("")
    lines += [
        "## Ranked shortlist",
        "",
        "| Rank | Score | Confidence | SMILES | Rationale |",
        "|---|---|---|---|---|",
    ]
    for r in ranked:
        lines.append(
            f"| {r['rank']} | {r['score']:.2f} | {r['confidence']} | `{r['smiles']}` | {r['reason']} |"
        )
    lines += [
        "",
        "_Triage, not oracle — every candidate carries confidence and provenance; "
        "a human approved this shortlist before export._",
    ]
    path.write_text("\n".join(lines))


def export_all(
    run_dir: Path,
    target: str,
    dossier: str,
    citations: list[dict],
    ranked: list[dict],
    metric: dict | None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    write_csv(run_dir / "shortlist.csv", ranked)
    write_sdf(run_dir / "shortlist.sdf", ranked)
    write_report(run_dir / "report.md", target, dossier, citations, ranked, metric)
