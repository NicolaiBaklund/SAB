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

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String(item.msg);
        }
        return JSON.stringify(item);
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return "";
}

export function formatRequestError(status: number, body: unknown): string {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? formatErrorDetail(body.detail)
      : "";
  return detail ? `Request failed: ${status}: ${detail}` : `Request failed: ${status}`;
}

export async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw new Error(formatRequestError(response.status, body));
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
