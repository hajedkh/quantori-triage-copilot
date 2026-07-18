import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, AlertTriangle } from "lucide-react";
import { getMol3D } from "../api";
import MoleculeView from "./MoleculeView";
import ConfidenceBadge from "./ConfidenceBadge";
import type { Confidence } from "../types";

interface Props {
  runId: string;
  rank: number;
  smiles: string;
  score: number;
  confidence: Confidence;
  onClose: () => void;
  // Fired once, the first time the molstar import itself fails (not a
  // per-molecule embed failure) — lets ResultsTable disable every 3D button
  // instead of letting each row re-attempt and re-fail the same broken import.
  onMolstarUnavailable: () => void;
}

// A full SMILES can run 60+ chars — too long for a one-line header. Trims
// to a readable snippet; the exact SMILES is already in the row/table above.
function shortSmiles(smiles: string, max = 26): string {
  return smiles.length > max ? smiles.slice(0, max) + "…" : smiles;
}

type Phase = "loading" | "ready" | "conformer-unavailable" | "molstar-unavailable";

// Mol*'s own Viewer instance has no exported TS type surface we need beyond
// what we call here — typing it precisely would mean importing molstar's
// types at the top of this file, which defeats the point of lazy-loading it.
type MolstarViewer = {
  plugin: {
    canvas3d?: { setProps: (props: unknown) => void } | null;
    managers: { structure: { hierarchy: { current: { structures: Array<{ cell: unknown }> } } } };
    builders: { structure: { representation: { applyPreset: (parent: unknown, preset: string) => Promise<unknown> | undefined } } };
  };
  loadStructureFromData: (data: string, format: string, options?: { dataLabel?: string }) => Promise<void>;
  dispose: () => void;
};

const SPIN_MS = 4000;

export default function Mol3DModal({ runId, rank, smiles, score, confidence, onClose, onMolstarUnavailable }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<MolstarViewer | null>(null);
  const spinTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      const result = await getMol3D(runId, rank);
      if (cancelled) return;
      if (!result.ok || !result.molblock) {
        setPhase("conformer-unavailable");
        return;
      }
      const molblock = result.molblock;

      // Mol* is a large WebGL library — imported here, on first click, and
      // nowhere else. Guarded: any failure (missing package, WebGL
      // unavailable, etc.) degrades to the 2D fallback, never a crash.
      try {
        await import("molstar/build/viewer/molstar.css");
        const [{ Viewer }, { Vec3 }] = await Promise.all([
          import("molstar/lib/apps/viewer/app"),
          import("molstar/lib/mol-math/linear-algebra"),
        ]);
        if (cancelled || !containerRef.current) return;

        const viewer = (await Viewer.create(containerRef.current, {
          layoutIsExpanded: false,
          layoutShowControls: false,
          layoutShowRemoteState: false,
          layoutShowSequence: false,
          layoutShowLog: false,
          layoutShowLeftPanel: false,
          viewportShowExpand: false,
          viewportShowSelectionMode: false,
          viewportShowSettings: false,
          viewportShowTrajectoryControls: false,
        })) as unknown as MolstarViewer;

        if (cancelled) {
          viewer.dispose();
          return;
        }
        viewerRef.current = viewer;

        await viewer.loadStructureFromData(molblock, "mol", { dataLabel: `rank-${rank}` });
        if (cancelled) return;

        // Best-effort explicit ball-and-stick. loadStructureFromData already
        // applied its own default (auto) representation, which for a
        // standalone small molecule is already atom/bond-explicit — this
        // just makes the intent explicit rather than relying on that. If
        // molstar's internal shape here ever changes, the view already
        // rendered fine above, so this failing is not fatal.
        try {
          const structures = viewer.plugin.managers.structure.hierarchy.current.structures;
          if (structures[0]) {
            await viewer.plugin.builders.structure.representation.applyPreset(
              structures[0].cell,
              "atomic-detail"
            );
          }
        } catch {
          /* keep whatever representation loadStructureFromData already applied */
        }

        // Auto-spin briefly on open, then hand full control to the user —
        // Mol*'s own trackball camera already supports drag-to-rotate and
        // scroll-to-zoom with no further setup.
        viewer.plugin.canvas3d?.setProps({
          trackball: { animate: { name: "spin", params: { speed: 0.6, axis: Vec3.create(0, 1, 0) } } },
        });
        spinTimerRef.current = window.setTimeout(() => {
          viewerRef.current?.plugin.canvas3d?.setProps({
            trackball: { animate: { name: "off", params: {} } },
          });
        }, SPIN_MS);

        if (!cancelled) setPhase("ready");
      } catch {
        onMolstarUnavailable();
        if (!cancelled) setPhase("molstar-unavailable");
      }
    };

    run();

    return () => {
      cancelled = true;
      if (spinTimerRef.current !== null) window.clearTimeout(spinTimerRef.current);
      if (viewerRef.current) {
        try {
          viewerRef.current.dispose();
        } catch {
          /* already gone */
        }
        viewerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, rank]);

  const fallback = phase === "conformer-unavailable" || phase === "molstar-unavailable";

  return createPortal(
    <div className="trace-modal-backdrop" onClick={onClose}>
      <div
        className="mol3d-modal fadeup"
        role="dialog"
        aria-modal="true"
        aria-label={`3D structure — rank ${rank}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="trace-modal-h">
          <div className="trace-modal-copy">
            <div className="trace-modal-name">3D structure · rank #{rank}</div>
            <div className="trace-modal-role mol3d-smiles" title={smiles}>
              {shortSmiles(smiles)}
            </div>
          </div>
          <div className="mol3d-meta">
            <span className="mol3d-score">{score.toFixed(3)}</span>
            <ConfidenceBadge level={confidence} />
          </div>
          <button className="trace-modal-close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div className="mol3d-body">
          {phase === "loading" && (
            <div className="mol3d-loading">
              <div className="mol3d-spinner" />
              Generating 3D conformer…
            </div>
          )}

          {/* Kept mounted (not conditionally rendered) once "ready" starts,
              so the ref exists before Viewer.create() runs. Hidden while
              loading/fallback so it never shows a blank canvas. */}
          <div
            ref={containerRef}
            className="mol3d-canvas"
            style={{ display: phase === "ready" ? "block" : "none" }}
          />

          {fallback && (
            <div className="mol3d-fallback">
              <div className="mol3d-fallback-2d">
                <MoleculeView smiles={smiles} size={160} height={150} />
              </div>
              <div className="mol3d-fallback-note">
                <AlertTriangle size={13} />
                {phase === "molstar-unavailable"
                  ? "3D viewer unavailable — showing 2D depiction instead."
                  : "3D unavailable for this structure — showing 2D depiction instead."}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
