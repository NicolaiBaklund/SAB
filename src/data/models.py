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
    score: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)  # positive | negative | neutral
    model: Mapped[str] = mapped_column(Text, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    article: Mapped["Article"] = relationship(back_populates="sentiment")
