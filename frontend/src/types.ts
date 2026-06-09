export type SentimentLabel = "positive" | "neutral" | "negative";

export type Sentiment = {
  score: number;
  label: SentimentLabel;
  model: string;
  scored_at: string;
};

export type ReviewCompany = {
  article_id: number;
  ticker: string;
  sentiment: Sentiment | null;
};

export type ReviewArticle = {
  url: string;
  source: string;
  title: string | null;
  body: string | null;
  published: string | null;
  fetched_at: string;
  companies: ReviewCompany[];
};

export type ReviewArticlesResponse = {
  items: ReviewArticle[];
  total: number;
  limit: number;
  offset: number;
};

export type FilterOptions = {
  tickers: string[];
  sources: string[];
  labels: string[];
  models: string[];
};

export type ScoreState = "" | "scored" | "unscored";

export type ReviewFilters = {
  ticker: string;
  source: string;
  label: string;
  scoreState: ScoreState;
  model: string;
  publishedFrom: string;
  publishedTo: string;
  q: string;
};

export type Pagination = {
  limit: number;
  offset: number;
};

