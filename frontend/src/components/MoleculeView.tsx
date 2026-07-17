import { useEffect, useRef, useState } from "react";
import SmilesDrawer from "smiles-drawer";

interface Props {
  smiles: string;
  size?: number;
  height?: number;
}

// Renders a 2D structure from a SMILES string using smiles-drawer's SmiDrawer.
// We draw into an <svg> element (the SVGElement branch of drawMolecule) rather
// than a canvas, which smiles-drawer handles directly. Pure JS, no WASM.
// If a string fails to parse we fall back to the SMILES text so a demo never
// shows a broken box.
export default function MoleculeView({ smiles, size = 84, height = 56 }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
    const svg = svgRef.current;
    if (!svg) return;
    // clear any previous render
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    try {
      const SD: any = (SmilesDrawer as any).default ?? SmilesDrawer;
      const drawer = new SD.SmiDrawer({
        width: size,
        height,
        bondThickness: 1.0,
        padding: 8,
        compactDrawing: true,
        terminalCarbons: false,
        explicitHydrogens: false,
      });
      // SVGElement branch: draw(smiles, svgEl, theme, successCb, errorCb)
      drawer.draw(
        smiles,
        svg,
        "light",
        () => {},
        () => setFailed(true)
      );
    } catch {
      setFailed(true);
    }
  }, [smiles, size, height]);

  if (failed) {
    return <div className="mol-fallback">{smiles}</div>;
  }
  return <svg ref={svgRef} width={size} height={height} />;
}
