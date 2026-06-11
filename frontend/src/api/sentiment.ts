import { getJson } from "./review";
import type { SentimentTimeseriesResponse } from "../types";

export async function fetchSentimentTimeseries(): Promise<SentimentTimeseriesResponse> {
  return getJson<SentimentTimeseriesResponse>("/api/sentiment/timeseries");
}
