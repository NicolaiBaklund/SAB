"""Model bake-off harness — Phase 2.1.

The roadmap's standing model pick (NorwAI-Magistral-24B) rested on "free +
Norwegian-aware". But every IDUN model is free, and the frontier options
(Mistral-Large-3-675B, Qwen3.5-122B, …) are also Norwegian-capable — so the small
model is no longer the obvious choice. This module decides it **empirically**
instead of by spec sheet:

1. ``sample``  — pull N random article rows from the DB into a JSONL template you
   hand-label with the *correct* answer (the gold set).
2. ``run``     — score every gold item with each candidate model and report which
   model best matches your labels, how self-consistent it is, and how much the
   models agree with each other.

Metrics reported per model:
- **accuracy**         fraction of items whose predicted label matches the gold label.
- **macro-F1**         mean F1 across the three classes (guards against a model
                       that wins accuracy by always saying "neutral").
- **off-topic recall** of the items you marked ``off_topic``, the fraction the
                       model also flagged ``off_topic`` (the false-positive guard).
- **self-consistency** with ``--k > 1``: fraction of items where all K runs at
                       temperature 0 returned the same label (measures determinism).
- **mean latency**     seconds per item.

Plus pairwise **Cohen's κ** between models (how much they agree beyond chance).

## Usage

    python -m src.nlp.eval sample --n 30          # write data/eval/goldset.jsonl
    #   ... hand-label gold_label / gold_relevance in that file ...
    python -m src.nlp.eval run                    # default 3-model bake-off
    python -m src.nlp.eval run --models A B --k 2 # custom models, self-consistency

``run`` calls IDUN, so it is rate-limited and should run off-peak. With ~30 items
× 3 models × k=1 that is ~90 requests (~5 min at 18 req/min).
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import func, select

from src.data.db import get_db
from src.data.models import Article
from src.nlp.client import IdunClient, RateLimiter
from src.nlp.prompt import REASK, VALID_LABELS, build_messages, parse_response
from src.settings import get_settings

logger = logging.getLogger(__name__)

GOLDSET_PATH = Path("data/eval/goldset.jsonl")

# Candidates for the default bake-off: the Norwegian-native baseline vs two
# frontier instruct models. Edit freely with --models.
# Exact IDUN catalog ids (the quant suffixes -NVFP4 / -FP8 are part of the served
# model name — a wrong id 404s every call).
DEFAULT_BAKEOFF_MODELS = [
    "mistralai/Mistral-Large-3-675B-Instruct-2512-NVFP4",  # bake-off winner (primary)
    "zai-org/GLM-4.7-FP8",                                 # runner-up: top accuracy, slower
    "NorwAI/NorwAI-Magistral-24B-reasoning",               # Norwegian-native baseline
]


@dataclass
class GoldItem:
    article_id: int | None
    ticker: str
    name: str
    title: str | None
    body: str | None
    gold_label: str  # positive | neutral | negative
    gold_relevance: str | None  # direct | mentioned | off_topic (optional)


# --------------------------------------------------------------------------- #
# Gold set I/O
# --------------------------------------------------------------------------- #
async def sample(n: int, out_path: Path, *, force: bool = False) -> int:
    """Write ``n`` random article rows to ``out_path`` as a labeling template.

    Each line is a full JSON record with ``gold_label`` / ``gold_relevance`` left
    blank for you to fill in. Refuses to clobber an existing file unless ``force``
    (so you don't lose hand labels).
    """
    if out_path.exists() and not force:
        raise SystemExit(f"{out_path} exists; pass --force to overwrite")
    from src.config import load_companies

    names = {c["ticker"]: c["name"] for c in load_companies()}
    async with get_db() as session:
        stmt = select(Article).order_by(func.random()).limit(n)
        articles = list((await session.execute(stmt)).scalars())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for a in articles:
            record = {
                "article_id": a.id,
                "ticker": a.ticker,
                "name": names.get(a.ticker, a.ticker),
                "title": a.title,
                "body": a.body,
                "gold_label": "",  # <- fill: positive | neutral | negative
                "gold_relevance": "",  # <- optional: direct | mentioned | off_topic
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("wrote %d rows to %s — now hand-label gold_label", len(articles), out_path)
    return len(articles)


def load_goldset(path: Path) -> list[GoldItem]:
    """Load labelled gold items, skipping any line whose ``gold_label`` is blank."""
    items: list[GoldItem] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            label = (r.get("gold_label") or "").strip().lower()
            if label not in VALID_LABELS:
                continue  # unlabelled / invalid — skip
            items.append(
                GoldItem(
                    article_id=r.get("article_id"),
                    ticker=r["ticker"],
                    name=r.get("name", r["ticker"]),
                    title=r.get("title"),
                    body=r.get("body"),
                    gold_label=label,
                    gold_relevance=(r.get("gold_relevance") or "").strip().lower() or None,
                )
            )
    return items


# --------------------------------------------------------------------------- #
# Metrics (pure)
# --------------------------------------------------------------------------- #
def accuracy(gold: list[str], pred: list[str]) -> float:
    if not gold:
        return 0.0
    return sum(g == p for g, p in zip(gold, pred)) / len(gold)


def macro_f1(gold: list[str], pred: list[str], labels=tuple(sorted(VALID_LABELS))) -> float:
    """Unweighted mean per-class F1 over ``labels``."""
    f1s: list[float] = []
    for c in labels:
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's κ between two label sequences (agreement beyond chance)."""
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in set(a) | set(b))
    return (po - pe) / (1 - pe) if pe != 1 else 1.0


# --------------------------------------------------------------------------- #
# Running candidates
# --------------------------------------------------------------------------- #
@dataclass
class ModelRun:
    model: str
    preds: list[str]  # majority label per item (len == len(goldset))
    consistent: list[bool]  # whether all K runs agreed, per item
    off_topic_pred: list[bool]  # whether the model flagged off_topic, per item
    mean_latency: float


async def _predict_once(client: IdunClient, item: GoldItem) -> tuple[str, str]:
    """One scored run for a gold item → (label, relevance), with a single re-ask."""
    messages = build_messages(item.ticker, item.name, item.title, item.body)
    raw = await client.complete(messages)
    try:
        res = parse_response(raw)
    except Exception:  # noqa: BLE001 — re-ask once, mirroring the scorer
        retry = [*messages, {"role": "assistant", "content": raw}, {"role": "user", "content": REASK}]
        res = parse_response(await client.complete(retry))
    return res.label, res.relevance


async def run_model(client: IdunClient, goldset: list[GoldItem], k: int) -> ModelRun:
    """Score every gold item ``k`` times with one model; collapse to a ModelRun."""
    preds: list[str] = []
    consistent: list[bool] = []
    off_topic_pred: list[bool] = []
    latencies: list[float] = []
    for item in goldset:
        labels: list[str] = []
        relevances: list[str] = []
        start = time.perf_counter()
        for _ in range(k):
            try:
                label, relevance = await _predict_once(client, item)
            except Exception as exc:  # noqa: BLE001 — count as a miss, keep going
                logger.warning("%s failed on article %s: %s", client.model, item.article_id, exc)
                label, relevance = "neutral", "direct"
            labels.append(label)
            relevances.append(relevance)
        latencies.append((time.perf_counter() - start) / k)
        preds.append(Counter(labels).most_common(1)[0][0])
        consistent.append(len(set(labels)) == 1)
        off_topic_pred.append(Counter(relevances).most_common(1)[0][0] == "off_topic")
    return ModelRun(
        model=client.model,
        preds=preds,
        consistent=consistent,
        off_topic_pred=off_topic_pred,
        mean_latency=sum(latencies) / len(latencies) if latencies else 0.0,
    )


def report(goldset: list[GoldItem], runs: list[ModelRun], k: int) -> str:
    """Render the comparison table + pairwise agreement as text."""
    gold = [g.gold_label for g in goldset]
    gold_off = [g.gold_relevance == "off_topic" for g in goldset]
    n_off = sum(gold_off)

    lines = [f"Gold set: {len(goldset)} items ({n_off} off_topic), k={k}", ""]
    header = f"{'model':<46} {'acc':>6} {'macroF1':>8} {'offRec':>7} {'consist':>8} {'lat(s)':>7}"
    lines += [header, "-" * len(header)]
    for r in runs:
        acc = accuracy(gold, r.preds)
        f1 = macro_f1(gold, r.preds)
        off_rec = (
            sum(o and p for o, p in zip(gold_off, r.off_topic_pred)) / n_off if n_off else float("nan")
        )
        consist = sum(r.consistent) / len(r.consistent) if k > 1 else float("nan")
        lines.append(
            f"{r.model:<46} {acc:>6.2f} {f1:>8.2f} {off_rec:>7.2f} {consist:>8.2f} {r.mean_latency:>7.1f}"
        )

    if len(runs) > 1:
        lines += ["", "Pairwise Cohen's κ (label agreement):"]
        for a, b in itertools.combinations(runs, 2):
            lines.append(f"  {a.model.split('/')[-1]:<34} vs {b.model.split('/')[-1]:<34} {cohen_kappa(a.preds, b.preds):>6.2f}")
    return "\n".join(lines)


async def evaluate(
    models: list[str], goldset: list[GoldItem], *, k: int, rpm: int | None,
    json_object: bool = True,
) -> str:
    """Run all candidate models over the gold set and return the rendered report.

    Guided JSON decoding (``response_format``) is on by default — without it,
    reasoning models (NorwAI-Magistral, Qwen-thinking) answer in prose or leak
    all tokens into reasoning and emit empty content, making the bake-off unfair.
    The client self-heals if the server rejects it.
    """
    settings = get_settings()
    api_key = settings.IDUN_KEY.get_secret_value()
    if api_key in ("", "Not Set"):
        raise SystemExit("IDUN_KEY is not set — add it to .env.local")
    # One shared limiter so the whole bake-off respects the global rate cap.
    limiter = RateLimiter(rpm) if rpm else RateLimiter()
    runs: list[ModelRun] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as http:
        for model in models:
            logger.info("running %s over %d items (k=%d)", model, len(goldset), k)
            client = IdunClient(
                http, model=model, api_key=api_key, base_url=settings.IDUN_BASE_URL,
                limiter=limiter, json_object=json_object,
            )
            runs.append(await run_model(client, goldset, k))
    return report(goldset, runs, k)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sentiment model bake-off (Phase 2.1)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sample = sub.add_parser("sample", help="write a gold-set labeling template from the DB")
    p_sample.add_argument("--n", type=int, default=30, help="number of articles to sample")
    p_sample.add_argument("--out", type=Path, default=GOLDSET_PATH)
    p_sample.add_argument("--force", action="store_true", help="overwrite an existing gold set")

    p_run = sub.add_parser("run", help="score the gold set with each model and report")
    p_run.add_argument("--models", nargs="+", default=DEFAULT_BAKEOFF_MODELS)
    p_run.add_argument("--goldset", type=Path, default=GOLDSET_PATH)
    p_run.add_argument("--k", type=int, default=1, help="runs per item (>=2 measures self-consistency)")
    p_run.add_argument("--rpm", type=int, default=None, help="requests/min cap (default 18)")
    p_run.add_argument("--limit", type=int, default=None, help="score only the first N gold items (smoke test)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "sample":
        asyncio.run(sample(args.n, args.out, force=args.force))
        return

    goldset = load_goldset(args.goldset)
    if not goldset:
        raise SystemExit(f"no labelled items in {args.goldset} — fill in gold_label first")
    if args.limit:
        goldset = goldset[: args.limit]
    print(asyncio.run(evaluate(args.models, goldset, k=args.k, rpm=args.rpm)))


if __name__ == "__main__":
    main()
