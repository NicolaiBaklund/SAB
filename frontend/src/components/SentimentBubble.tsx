import { CircleDashed, Minus, TrendingDown, TrendingUp } from "lucide-react";

import type { ReviewCompany, SentimentLabel } from "../types";

type Props = {
  company: ReviewCompany;
};

function formatScore(score: number): string {
  return `${score >= 0 ? "+" : ""}${score.toFixed(2)}`;
}

function labelIcon(label: SentimentLabel) {
  if (label === "positive") {
    return <TrendingUp aria-hidden="true" size={14} />;
  }
  if (label === "negative") {
    return <TrendingDown aria-hidden="true" size={14} />;
  }
  return <Minus aria-hidden="true" size={14} />;
}

export function SentimentBubble({ company }: Props) {
  const { ticker, sentiment } = company;
  if (!sentiment) {
    return (
      <span className="sentiment-bubble sentiment-bubble--unscored">
        <CircleDashed aria-hidden="true" size={14} />
        <span className="ticker">{ticker}</span>
        <span>unscored</span>
      </span>
    );
  }

  return (
    <span
      className={`sentiment-bubble sentiment-bubble--${sentiment.label}`}
      title={`${sentiment.model} scored at ${new Date(sentiment.scored_at).toLocaleString()}`}
    >
      {labelIcon(sentiment.label)}
      <span className="ticker">{ticker}</span>
      <span>{formatScore(sentiment.score)}</span>
    </span>
  );
}

