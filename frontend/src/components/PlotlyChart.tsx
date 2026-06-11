import Plotly from "plotly.js-basic-dist-min";
import type { Data, Layout } from "plotly.js";
import { useEffect, useRef } from "react";

type PlotlyChartProps = {
  data: Data[];
  layout: Partial<Layout>;
};

// Thin wrapper around plotly.js-basic-dist-min (react-plotly.js is unmaintained
// and predates React 19). Plotly.react diffs in place, so re-renders are cheap.
export function PlotlyChart({ data, layout }: PlotlyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    void Plotly.react(container, data, layout, {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
    });
  }, [data, layout]);

  useEffect(() => {
    const container = containerRef.current;
    return () => {
      if (container) {
        Plotly.purge(container);
      }
    };
  }, []);

  return <div className="plotly-chart" ref={containerRef} />;
}
