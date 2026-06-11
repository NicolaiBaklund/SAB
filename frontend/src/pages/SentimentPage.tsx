import type { Data, Layout } from "plotly.js";
import { useEffect, useMemo, useState } from "react";

import { fetchSentimentTimeseries } from "../api/sentiment";
import { PlotlyChart } from "../components/PlotlyChart";
import type { ThemeName } from "../theme";
import type { SentimentSeries, SentimentTimeseriesResponse } from "../types";

type ViewMode = "combined" | "per-company";

// Bright trace colors that read on both the dark and light palettes,
// assigned by series order (tickers come sorted from the API).
const TRACE_COLORS = [
  "#4fc3f7",
  "#ffa657",
  "#7ee787",
  "#ff7eb6",
  "#d2a8ff",
  "#f2cc60",
  "#79c0ff",
  "#ffab70",
];

export function traceColor(index: number): string {
  return TRACE_COLORS[index % TRACE_COLORS.length];
}

// Two traces per company: faint daily-mean markers (raw signal, hover shows
// article count) and a solid rolling-mean line (the trend).
export function buildSeriesTraces(
  series: SentimentSeries,
  color: string,
  windowDays: number,
): Data[] {
  const dates = series.points.map((point) => point.date);
  return [
    {
      type: "scatter",
      mode: "markers",
      name: `${series.ticker} daily`,
      x: dates,
      y: series.points.map((point) => point.mean),
      customdata: series.points.map((point) => point.count),
      hovertemplate:
        `${series.ticker} %{x}<br>daily mean %{y:.2f} (%{customdata} article(s))<extra></extra>`,
      marker: { color, size: 5, opacity: 0.45 },
    },
    {
      type: "scatter",
      mode: "lines",
      name: series.ticker,
      x: dates,
      y: series.points.map((point) => point.rolling),
      hovertemplate:
        `${series.ticker} %{x}<br>${windowDays}d rolling %{y:.2f}<extra></extra>`,
      line: { color, width: 2, shape: "spline" },
    },
  ];
}

// Mirrors the --text/--border/--border-strong values in styles.css. Reading
// them via getComputedStyle would race the data-theme effect in App.tsx and
// pick up the old theme's values during the toggle render.
const CHART_COLORS: Record<ThemeName, { text: string; border: string; borderStrong: string }> = {
  dark: { text: "#d7dbe3", border: "#2b2f3a", borderStrong: "#383d4a" },
  light: { text: "#283442", border: "#dce2e9", borderStrong: "#c6cfd9" },
};

function chartLayout(
  theme: ThemeName,
  title: string | undefined,
  height: number,
): Partial<Layout> {
  const { text, border, borderStrong } = CHART_COLORS[theme];
  return {
    title: title ? { text: title, font: { size: 13 } } : undefined,
    height,
    margin: { t: title ? 36 : 16, r: 16, b: 40, l: 40 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: text, family: "inherit", size: 12 },
    showlegend: false,
    hovermode: "closest",
    xaxis: { gridcolor: border, linecolor: border },
    yaxis: {
      range: [-1.15, 1.15],
      gridcolor: border,
      linecolor: border,
      zeroline: true,
      zerolinecolor: borderStrong,
    },
  };
}

export function SentimentPage({ theme }: { theme: ThemeName }) {
  const [data, setData] = useState<SentimentTimeseriesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("combined");
  const [hiddenTickers, setHiddenTickers] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchSentimentTimeseries()
      .then(setData)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load sentiment");
      });
  }, []);

  const series = data?.series ?? [];
  const windowDays = data?.window_days ?? 7;
  const hasAnyPoints = series.some((entry) => entry.points.length > 0);

  const combinedTraces = useMemo(
    () =>
      series.flatMap((entry, index) =>
        hiddenTickers.has(entry.ticker)
          ? []
          : buildSeriesTraces(entry, traceColor(index), windowDays),
      ),
    [series, hiddenTickers, windowDays],
  );

  const combinedLayout = useMemo(() => chartLayout(theme, undefined, 420), [theme]);

  // Stable data/layout objects per panel so PlotlyChart only re-renders when
  // the data, selection, or theme actually changes.
  const companyPanels = useMemo(
    () =>
      series.flatMap((entry, index) =>
        hiddenTickers.has(entry.ticker)
          ? []
          : [
              {
                ticker: entry.ticker,
                data: buildSeriesTraces(entry, traceColor(index), windowDays),
                layout: chartLayout(theme, entry.ticker, 260),
              },
            ],
      ),
    [series, hiddenTickers, windowDays, theme],
  );

  function toggleTicker(ticker: string) {
    setHiddenTickers((current) => {
      const next = new Set(current);
      if (next.has(ticker)) {
        next.delete(ticker);
      } else {
        next.add(ticker);
      }
      return next;
    });
  }

  if (error) {
    return (
      <section className="sentiment-page">
        <div className="status status--error">{error}</div>
      </section>
    );
  }

  return (
    <section className="sentiment-page">
      <div className="sentiment-toolbar">
        <div className="view-toggle" role="group" aria-label="Chart layout">
          <button
            type="button"
            className={view === "combined" ? "toggle-button toggle-button--active" : "toggle-button"}
            onClick={() => setView("combined")}
          >
            Combined
          </button>
          <button
            type="button"
            className={view === "per-company" ? "toggle-button toggle-button--active" : "toggle-button"}
            onClick={() => setView("per-company")}
          >
            Per company
          </button>
        </div>

        <div className="company-filter" role="group" aria-label="Companies">
          {series.map((entry, index) => (
            <label className="company-checkbox" key={entry.ticker}>
              <input
                type="checkbox"
                checked={!hiddenTickers.has(entry.ticker)}
                onChange={() => toggleTicker(entry.ticker)}
              />
              <span className="company-swatch" style={{ background: traceColor(index) }} />
              {entry.ticker}
            </label>
          ))}
        </div>
      </div>

      <p className="sentiment-hint">
        Daily mean score (dots) and {windowDays}-day rolling mean (line) of
        price-impact sentiment, −1 to +1. Off-topic matches are excluded.
      </p>

      {!data ? <div className="status">Loading</div> : null}

      {data && !hasAnyPoints ? (
        <div className="status">
          No scored sentiment yet. Run the scorer (python -m src.nlp.scorer) to
          populate the sentiment table.
        </div>
      ) : null}

      {data && hasAnyPoints && view === "combined" ? (
        <div className="chart-panel">
          <PlotlyChart data={combinedTraces} layout={combinedLayout} />
        </div>
      ) : null}

      {data && hasAnyPoints && view === "per-company" ? (
        <div className="chart-grid">
          {companyPanels.map((panel) => (
            <div className="chart-panel" key={panel.ticker}>
              <PlotlyChart data={panel.data} layout={panel.layout} />
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
