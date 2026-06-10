from datetime import datetime
from sqlalchemy import Integer, Text, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    # Uniqueness is on (ticker, url), not url alone: a single news article can
    # mention several companies, so the same url is stored once per matched
    # ticker (one row each). See src/data/rss.py.
    __table_args__ = (UniqueConstraint("ticker", "url", name="uq_articles_ticker_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published: Mapped[datetime | None] = mapped_column(DateTime)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    sentiment: Mapped[list["Sentiment"]] = relationship(back_populates="article")


class Sentiment(Base):
    __tablename__ = "sentiment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(Integer, ForeignKey("articles.id"), nullable=False)
    # 3-point price-impact scale: score in {-1.0, 0.0, +1.0}, derived from `label`
    # ({negative: -1, neutral: 0, positive: +1}). Stored numeric for aggregation,
    # `label` kept for display. See src/nlp/prompt.py.
    score: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)  # positive | negative | neutral
    # Whether the article is materially about this ticker: direct | mentioned |
    # off_topic. `off_topic` is coerced to neutral/0 — it flags keyword false
    # matches (e.g. the Edvard Grieg oilfield vs Grieg Seafood) for GUI review.
    relevance: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)  # one-line model justification
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)  # which prompt template produced this
    scored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    article: Mapped["Article"] = relationship(back_populates="sentiment")
