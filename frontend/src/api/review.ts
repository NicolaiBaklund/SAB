import type {
  FilterOptions,
  Pagination,
  ReviewArticlesResponse,
  ReviewFilters,
} from "../types";

const API_ROOT = "/api/review";

export const defaultFilters: ReviewFilters = {
  ticker: "",
  source: "",
  label: "",
  scoreState: "",
  model: "",
  publishedFrom: "",
  publishedTo: "",
  q: "",
};

export function buildReviewQuery(
  filters: ReviewFilters,
  pagination: Pagination,
): string {
  const params = new URLSearchParams();
  params.set("limit", String(pagination.limit));
  params.set("offset", String(pagination.offset));

  const entries: Array<[string, string]> = [
    ["ticker", filters.ticker],
    ["source", filters.source],
    ["label", filters.label],
    ["score_state", filters.scoreState],
    ["model", filters.model],
    ["published_from", filters.publishedFrom],
    ["published_to", filters.publishedTo],
    ["q", filters.q.trim()],
  ];

  for (const [key, value] of entries) {
    if (value) {
      params.set(key, value);
    }
  }

  return params.toString();
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchReviewArticles(
  filters: ReviewFilters,
  pagination: Pagination,
): Promise<ReviewArticlesResponse> {
  const query = buildReviewQuery(filters, pagination);
  return getJson<ReviewArticlesResponse>(`${API_ROOT}/articles?${query}`);
}

export async function fetchFilterOptions(): Promise<FilterOptions> {
  return getJson<FilterOptions>(`${API_ROOT}/filter-options`);
}

