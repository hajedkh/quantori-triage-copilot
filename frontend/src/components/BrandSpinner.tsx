import { QUANTORI_LOGO } from "../brand";

interface Props {
  size?: number;
  label?: string;
}

// The branded "working on it" mark: the Quantori logo turning and
// breathing (zoom in/out) in place. Used anywhere the app is waiting on an
// agent or a model call, instead of a generic spinner.
export default function BrandSpinner({ size = 16, label }: Props) {
  return (
    <span className="brand-spinner-wrap" role="status" aria-label={label || "loading"}>
      <img className="brand-spinner" src={QUANTORI_LOGO} alt="" width={size} height={size} />
    </span>
  );
}
