import type { Confidence } from "../types";

export default function ConfidenceBadge({ level }: { level: Confidence }) {
  return <span className={`conf ${level}`}>{level}</span>;
}
