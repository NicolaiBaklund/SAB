import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  defaultFilters,
  fetchFilterOptions,
  fetchReviewArticles,
} from "../api/review";
import { SentimentBubble } from "../components/SentimentBubble";
import type {
  FilterOptions,
  ReviewArticle,
  ReviewArticlesResponse,
  ReviewFilters,
  ScoreState,
} from "../types";

const PAGE_SIZE = 20;
const SENTIMENT_LABELS = ["positive", "neutral", "negative"];
const SCORE_STATES: Array<{ value: ScoreState; label: string }> = [
  { value: "", label: "Any score" },
  { value: "scored", label: "Scored" },
  { value: "unscored", label: "Unscored" },
];

function formatDate(value: string | null): string {
  if (!value) {
    return "No publish time";
  }
  return new Date(value).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function titleFor(article: ReviewArticle): string {
  return article.title || "Untitled article";
}

function uniqueSorted(values: string[]) {
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));
}

export function ReviewPage() {
  const [filters, setFilters] = useState<ReviewFilters>(defaultFilters);
  const [options, setOptions] = useState<FilterOptions>({
    tickers: [],
    sources: [],
    labels: [],
    models: [],
  });
  const [data, setData] = useState<ReviewArticlesResponse>({
    items: [],
    total: 0,
    limit: PAGE_SIZE,
    offset: 0,
  });
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const labelOptions = useMemo(
    () => uniqueSorted([...SENTIMENT_LABELS, ...options.labels]),
    [options.labels],
  );

  useEffect(() => {
    fetchFilterOptions()
      .then(setOptions)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load filters");
      });
  }, []);

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError(null);
    fetchReviewArticles(filters, { limit: PAGE_SIZE, offset })
      .then((payload) => {
        if (!ignore) {
          setData(payload);
        }
      })
      .catch((err: unknown) => {
        if (!ignore) {
          setError(err instanceof Error ? err.message : "Failed to load articles");
        }
      })
      .finally(() => {
        if (!ignore) {
          setLoading(false);
        }
      });

    return () => {
      ignore = true;
    };
  }, [filters, offset]);

  function updateFilter<K extends keyof ReviewFilters>(key: K, value: ReviewFilters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
    setOffset(0);
  }

  function resetFilters() {
    setFilters(defaultFilters);
    setOffset(0);
  }

  const pageStart = data.total === 0 ? 0 : data.offset + 1;
  const pageEnd = Math.min(data.offset + data.limit, data.total);
  const canGoBack = data.offset > 0;
  const canGoNext = data.offset + data.limit < data.total;
  const pagination = (
    <PaginationControls
      loading={loading}
      pageStart={pageStart}
      pageEnd={pageEnd}
      total={data.total}
      canGoBack={canGoBack}
      canGoNext={canGoNext}
      onPrevious={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
      onNext={() => setOffset(offset + PAGE_SIZE)}
    />
  );

  return (
    <section className="review-page">
      <form className="filter-bar" onSubmit={(event) => event.preventDefault()}>
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <input
            value={filters.q}
            onChange={(event) => updateFilter("q", event.target.value)}
            placeholder="Search titles"
          />
        </label>

        <label>
          Company
          <select
            value={filters.ticker}
            onChange={(event) => updateFilter("ticker", event.target.value)}
          >
            <option value="">All</option>
            {options.tickers.map((ticker) => (
              <option value={ticker} key={ticker}>
                {ticker}
              </option>
            ))}
          </select>
        </label>

        <label>
          Sentiment
          <select
            value={filters.label}
            onChange={(event) => updateFilter("label", event.target.value)}
          >
            <option value="">All</option>
            {labelOptions.map((label) => (
              <option value={label} key={label}>
                {label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Source
          <select
            value={filters.source}
            onChange={(event) => updateFilter("source", event.target.value)}
          >
            <option value="">All</option>
            {options.sources.map((source) => (
              <option value={source} key={source}>
                {source}
              </option>
            ))}
          </select>
        </label>

        <label>
          Score
          <select
            value={filters.scoreState}
            onChange={(event) => updateFilter("scoreState", event.target.value as ScoreState)}
          >
            {SCORE_STATES.map((state) => (
              <option value={state.value} key={state.label}>
                {state.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Model
          <select
            value={filters.model}
            onChange={(event) => updateFilter("model", event.target.value)}
          >
            <option value="">All models</option>
            {options.models.map((model) => (
              <option value={model} key={model}>
                {model}
              </option>
            ))}
          </select>
        </label>

        <label>
          From
          <input
            type="date"
            value={filters.publishedFrom}
            onChange={(event) => updateFilter("publishedFrom", event.target.value)}
          />
        </label>

        <label>
          To
          <input
            type="date"
            value={filters.publishedTo}
            onChange={(event) => updateFilter("publishedTo", event.target.value)}
          />
        </label>

        <button type="button" className="secondary-button" onClick={resetFilters}>
          <RefreshCw size={15} aria-hidden="true" />
          Reset
        </button>
      </form>

      {pagination}

      {error ? <div className="status status--error">{error}</div> : null}

      <div className="article-list">
        {data.items.map((article) => (
          <ArticleCard article={article} key={article.url} />
        ))}
        {!loading && data.items.length === 0 && !error ? (
          <div className="status">No articles match the active filters.</div>
        ) : null}
      </div>

      {data.items.length > 0 ? pagination : null}
    </section>
  );
}

type PaginationControlsProps = {
  loading: boolean;
  pageStart: number;
  pageEnd: number;
  total: number;
  canGoBack: boolean;
  canGoNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
};

function PaginationControls({
  loading,
  pageStart,
  pageEnd,
  total,
  canGoBack,
  canGoNext,
  onPrevious,
  onNext,
}: PaginationControlsProps) {
  return (
    <div className="list-toolbar">
      <span>{loading ? "Loading" : `${pageStart}-${pageEnd} of ${total}`}</span>
      <div className="pager">
        <button
          type="button"
          className="icon-button"
          onClick={onPrevious}
          disabled={!canGoBack || loading}
          aria-label="Previous page"
        >
          <ChevronLeft size={18} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="icon-button"
          onClick={onNext}
          disabled={!canGoNext || loading}
          aria-label="Next page"
        >
          <ChevronRight size={18} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function ArticleCard({ article }: { article: ReviewArticle }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <article className="article-card">
      <div className="article-header">
        <span className="source-badge">{article.source}</span>
        <h2>{titleFor(article)}</h2>
      </div>
      <div className="article-meta">
        <time dateTime={article.published || undefined}>{formatDate(article.published)}</time>
        <span aria-hidden="true">/</span>
        <a href={article.url} target="_blank" rel="noreferrer">
          Open original
          <ExternalLink size={14} aria-hidden="true" />
        </a>
      </div>

      <div className="bubble-row">
        {article.companies.map((company) => (
          <SentimentBubble company={company} key={company.article_id} />
        ))}
      </div>

      <button
        type="button"
        className="model-input-toggle"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
      >
        <ChevronDown
          size={16}
          aria-hidden="true"
          className={expanded ? "chevron chevron--open" : "chevron"}
        />
        Model input
      </button>

      {expanded ? (
        <div className="model-input-panel">
          <div className="target-lines">
            {article.companies.map((company) => (
              <code key={company.article_id}>Target ticker: {company.ticker}</code>
            ))}
          </div>
          <pre>{`${titleFor(article)}\n\n${article.body || "No body text stored."}`}</pre>
        </div>
      ) : null}
    </article>
  );
}
