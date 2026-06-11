import { vi } from "vitest";

// PlotlyChart pulls in the real plotly.js bundle, which jsdom can't run.
vi.mock("plotly.js-basic-dist-min", () => ({
  default: { react: vi.fn(), purge: vi.fn() },
}));

import { buildSeriesTraces, traceColor } from "./SentimentPage";
import type { SentimentSeries } from "../types";

const series: SentimentSeries = {
  ticker: "MOWI",
  points: [
    { date: "2026-06-01", mean: 0.5, rolling: 0.5, count: 2 },
    { date: "2026-06-03", mean: -1.0, rolling: 0.0, count: 1 },
  ],
};

describe("buildSeriesTraces", () => {
  it("produces a daily-mean marker trace and a rolling-mean line trace", () => {
    const [markers, line] = buildSeriesTraces(series, "#4fc3f7", 7);

    expect(markers).toMatchObject({
      mode: "markers",
      x: ["2026-06-01", "2026-06-03"],
      y: [0.5, -1.0],
      customdata: [2, 1],
    });
    expect(line).toMatchObject({
      mode: "lines",
      name: "MOWI",
      x: ["2026-06-01", "2026-06-03"],
      y: [0.5, 0.0],
    });
    expect((line as { line: { color: string } }).line.color).toBe("#4fc3f7");
  });

  it("mentions the rolling window length in the line hover", () => {
    const [, line] = buildSeriesTraces(series, "#4fc3f7", 7);

    expect((line as { hovertemplate: string }).hovertemplate).toContain("7d rolling");
  });
});

describe("traceColor", () => {
  it("cycles the palette for more companies than colors", () => {
    expect(traceColor(0)).toBe(traceColor(8));
    expect(traceColor(0)).not.toBe(traceColor(1));
  });
});
