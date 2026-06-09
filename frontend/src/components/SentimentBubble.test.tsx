import { render, screen } from "@testing-library/react";

import { SentimentBubble } from "./SentimentBubble";
import type { ReviewCompany, SentimentLabel } from "../types";

function company(label: SentimentLabel | null, score = 0.62): ReviewCompany {
  return {
    article_id: Math.floor(Math.random() * 10000),
    ticker: "MOWI",
    sentiment: label
      ? {
          label,
          score,
          model: "model-a",
          scored_at: "2026-06-09T12:00:00",
        }
      : null,
  };
}

describe("SentimentBubble", () => {
  it("renders positive sentiment with a signed score", () => {
    const { container } = render(<SentimentBubble company={company("positive", 0.62)} />);

    expect(screen.getByText("MOWI")).toBeInTheDocument();
    expect(screen.getByText("+0.62")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("sentiment-bubble--positive");
  });

  it("renders neutral sentiment as a scored verdict", () => {
    const { container } = render(<SentimentBubble company={company("neutral", 0.04)} />);

    expect(screen.getByText("+0.04")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("sentiment-bubble--neutral");
  });

  it("renders negative sentiment", () => {
    const { container } = render(<SentimentBubble company={company("negative", -0.55)} />);

    expect(screen.getByText("-0.55")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("sentiment-bubble--negative");
  });

  it("renders unscored without a numeric score", () => {
    const { container } = render(<SentimentBubble company={company(null)} />);

    expect(screen.getByText("unscored")).toBeInTheDocument();
    expect(screen.queryByText("+0.62")).not.toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("sentiment-bubble--unscored");
  });
});

