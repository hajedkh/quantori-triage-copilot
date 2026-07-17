import { ChevronDown } from "lucide-react";
import type { FunnelState, Metric } from "../types";

interface Props {
  funnel: FunnelState;
  metric: Metric | null;
}

// Widths encode the narrowing visually: input is full, filtered is proportional,
// ranked is the sliver that survives. Numbers are the real counts.
export default function FunnelMeter({ funnel, metric }: Props) {
  const { input, filtered, ranked } = funnel;
  const wFiltered = filtered != null ? Math.max(38, (filtered / input) * 100) : 100;
  const wRanked =
    ranked != null && filtered != null
      ? Math.max(24, (ranked / input) * 100)
      : wFiltered;

  return (
    <div className="panel">
      <div className="panel-h">
        <h3>Triage funnel</h3>
      </div>
      <div className="funnel">
        <div className="funnel-row">
          <div className="funnel-bar fb-input" style={{ width: "100%" }}>
            <span className="big">{input.toLocaleString()}</span>
            <span className="lbl">Input</span>
          </div>
        </div>

        <div className="funnel-caret">
          <ChevronDown size={14} />
        </div>

        <div className="funnel-row">
          <div
            className={"funnel-bar fb-filtered" + (filtered == null ? " funnel-pending" : "")}
            style={{ width: `${wFiltered}%` }}
          >
            <span className="big">{filtered != null ? filtered : "—"}</span>
            <span className="lbl">Filtered</span>
          </div>
        </div>

        <div className="funnel-caret">
          <ChevronDown size={14} />
        </div>

        <div className="funnel-row">
          <div
            className={"funnel-bar fb-ranked" + (ranked == null ? " funnel-pending" : "")}
            style={{ width: `${wRanked}%` }}
          >
            <span className="big">{ranked != null ? ranked : "—"}</span>
            <span className="lbl">Ranked</span>
          </div>
        </div>
      </div>

      {metric && (
        <div className="metric-card fadeup">
          <div className="metric-num">
            {metric.recovered}
            <small> / {metric.total_actives}</small>
          </div>
          <div className="metric-lbl">
            known actives recovered in top {metric.top_n}
            <br />
            from {metric.screened.toLocaleString()} molecules · labels hidden
          </div>
        </div>
      )}
    </div>
  );
}
