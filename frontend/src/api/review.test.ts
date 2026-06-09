import { buildReviewQuery, defaultFilters } from "./review";

describe("buildReviewQuery", () => {
  it("includes pagination and omits empty filters", () => {
    const query = buildReviewQuery(defaultFilters, { limit: 20, offset: 40 });

    expect(query).toBe("limit=20&offset=40");
  });

  it("serializes active filters using backend parameter names", () => {
    const query = buildReviewQuery(
      {
        ...defaultFilters,
        ticker: "MOWI",
        source: "gnews",
        label: "positive",
        scoreState: "scored",
        model: "model-a",
        publishedFrom: "2026-06-01",
        publishedTo: "2026-06-09",
        q: "  earnings  ",
      },
      { limit: 20, offset: 0 },
    );

    expect(query).toContain("ticker=MOWI");
    expect(query).toContain("source=gnews");
    expect(query).toContain("label=positive");
    expect(query).toContain("score_state=scored");
    expect(query).toContain("model=model-a");
    expect(query).toContain("published_from=2026-06-01");
    expect(query).toContain("published_to=2026-06-09");
    expect(query).toContain("q=earnings");
  });
});

