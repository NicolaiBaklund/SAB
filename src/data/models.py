from datetime import date, datetime
from sqlalchemy import Integer, Text, Float, Date, DateTime, ForeignKey, UniqueConstraint
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


class Price(Base):
    __tablename__ = "prices"

    # One daily OHLCV bar per company per trading day. Rows are *upserted*, not
    # insert-only: a bar fetched intraday is partial (close = last trade so far)
    # and the source revises `adj_close` retroactively on dividends/splits, so
    # re-runs must overwrite. See src/data/prices.py.
    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_prices_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)  # company ticker (MOWI), not the source symbol (MOWI.OL)
    date: Mapped[date] = mapped_column(Date, nullable=False)  # trading day, exchange-local
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    # Dividend/split-adjusted close — use this for returns and technical
    # indicators across corporate actions; raw `close` is what traded that day.
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(Text)  # e.g. NOK (from source metadata)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # yahoo
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
