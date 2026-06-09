"""Sentiment prompt + output contract — Phase 2.1.

This module is the **single source of truth** for what the sentiment model is
asked and how its answer is parsed. It is pure (no I/O, no DB, no network) so it
can be unit-tested and so the review GUI can reconstruct *exactly* what the model
saw from the stored ``title`` + ``body`` (the Phase 2 audit constraint: nothing
is fetched or enriched at score time).

## What we ask the model

For each ``(article, ticker)`` row — the RSS/Newsweb scrapers already fan a
multi-company article into one row per ticker — we ask **one** question:

    Does this news item raise or lower the expected share price of THIS company
    over the next few trading days?

This is a **price-impact / investor** lens, not editorial tone. A sea-lice
outbreak reads "neutral tone" but is price-negative; a record salmon spot price
is price-positive. The lens is what makes the score useful as a trading signal.

## Output scale — 3-point, categorical

We deliberately do **not** ask for a continuous [-1, 1] number. An LLM cannot
reproducibly separate 0.62 from 0.71 — that precision is noise (a false sense of
regression). Instead the model emits a **categorical label** and we derive the
numeric score from it:

    negative -> -1.0     neutral -> 0.0     positive -> +1.0

3 classes is the dominant financial-sentiment convention (Financial PhraseBank,
FinBERT). The model never sees or emits the float; categories are what LLMs label
reliably.

## Relevance (false-positive guard)

The scrapers match on bare keywords, so "Grieg" matches the *Edvard Grieg* oil
field, not *Grieg Seafood*. The model therefore also returns a ``relevance``:

    direct      the item is materially about this company
    mentioned   the company appears but is not the subject (peer/sector context)
    off_topic   keyword matched the wrong subject, or the company is irrelevant

``off_topic`` is **coerced to neutral / 0.0** in :func:`parse_response` — an
off-topic match carries no signal for this ticker — but the ``off_topic`` flag is
stored so the GUI can surface keyword false matches for cleanup.

## Versioning

``PROMPT_VERSION`` is stored on every sentiment row. Changing the template text,
the few-shot set, or the contract means bumping this string, so scores are always
attributable to the exact prompt that produced them (needed for the model
bake-off and for re-scoring decisions).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Bump whenever SYSTEM_PROMPT, FEW_SHOT, or the user template below changes.
PROMPT_VERSION = "price-impact-v1"

# Label -> numeric score on the 3-point scale. The model emits the label; we
# derive the score so the two can never disagree.
LABEL_SCORES: dict[str, float] = {"negative": -1.0, "neutral": 0.0, "positive": 1.0}

VALID_LABELS = frozenset(LABEL_SCORES)
VALID_RELEVANCE = frozenset({"direct", "mentioned", "off_topic"})

# Lenient normalisation for what models actually return (Norwegian spellings,
# stray casing/whitespace). Anything not mapped and not already valid is an error
# the caller can retry on.
_LABEL_ALIASES = {
    "positive": "positive", "positiv": "positive", "pos": "positive",
    "negative": "negative", "negativ": "negative", "neg": "negative",
    "neutral": "neutral", "nøytral": "neutral", "noytral": "neutral", "neu": "neutral",
}
_RELEVANCE_ALIASES = {
    "direct": "direct", "direkte": "direct",
    "mentioned": "mentioned", "nevnt": "mentioned", "mention": "mentioned",
    "off_topic": "off_topic", "off-topic": "off_topic", "offtopic": "off_topic",
    "irrelevant": "off_topic", "irrelevante": "off_topic",
}

# Body longer than this (characters) is truncated before the model sees it.
# Newsweb bodies fold in PDF attachments converted to Markdown and can be very
# long; the smallest candidate model (NorwAI-Magistral-24B) has a 40k-token
# context. ~24k chars ≈ ~8k tokens leaves ample room for the system prompt,
# few-shot block, and the answer. Override per model from the scorer/eval.
DEFAULT_MAX_BODY_CHARS = 24_000

# Companies in scope, named in the system prompt so the model knows the universe
# and can recognise peers vs the target. Kept short on purpose.
_UNIVERSE = "Mowi, SalMar, Lerøy, Grieg Seafood, Bakkafrost and Austevoll"

SYSTEM_PROMPT = f"""\
You are a sell-side equity analyst covering Norwegian salmon-farming and seafood \
companies listed on Oslo Børs ({_UNIVERSE}). You are given a single news item \
(Norwegian or English) and must judge its likely effect on the share price of \
ONE specific company over the next few trading days.

Judge PRICE IMPACT, not writing tone. The question is always: does this news \
raise or lower the expected value of the target company's stock?

Judge ONLY the target company named in the prompt. The item may mention \
competitors, peers, or the sector — ignore the impact on anyone else. If the \
target company is named only in passing, only as an index/peer comparison, or the \
item is actually about a different subject with the same name (e.g. an unrelated \
namesake), set relevance to "off_topic".

Use only the information in the item. Do not use outside knowledge or hindsight \
about how things later turned out. Do not speculate beyond what the text supports.

Salmon-sector guide (Norwegian terms -> typical price direction):
- POSITIVE: earnings/EBIT beat, raised guidance, higher salmon spot price \
(spotpris/lakspris), new contracts or markets, harvest-volume (slaktevolum) \
growth, capacity/licence expansion, cost cuts, share buyback, insider buying \
(innsidekjøp), dividend increase, strong biology / low mortality.
- NEGATIVE: profit warning (resultatvarsel), cut guidance, sea-lice (lakselus) \
outbreak, disease (ILA/PD), mass mortality (dødelighet), algae bloom, escapes \
(rømming), biomass/inventory loss, harvest/volume cut, higher feed or other cost, \
higher resource-rent tax (grunnrenteskatt), fines/sanctions, licence loss, \
insider selling, share issue/dilution (emisjon), dividend cut.
- NEUTRAL: routine or administrative disclosures with no clear price implication \
— AGM notices (innkalling til generalforsamling), large-shareholder flagging \
(flaggemelding), routine primary-insider/PDMR notifications, share-count updates, \
financial-calendar dates, prospectus boilerplate, already-known information.

Return your answer as a single minified JSON object and NOTHING else:
{{"label": "<positive|neutral|negative>", "relevance": "<direct|mentioned|off_topic>", "rationale": "<reason>"}}

Rules for the fields:
- label: "positive" if the news is net likely to push the target stock up, \
"negative" if net likely to push it down, "neutral" if there is no clear \
directional price impact (use "neutral" for purely administrative items).
- relevance: "direct" if the item is materially about the target company; \
"mentioned" if the company appears but is not the main subject; "off_topic" if \
the keyword matched the wrong subject or the company is irrelevant to the item.
- rationale: one short English clause (<= 25 words, no line breaks) naming the \
single main price driver behind your label.
Do not output markdown, code fences, comments, or any text outside the JSON."""


def _format_user(name: str, ticker: str, title: str | None, body: str | None,
                 *, max_body_chars: int = DEFAULT_MAX_BODY_CHARS) -> str:
    """Render the user turn: the target company plus the stored article text.

    The title is always kept; the body is truncated at a word boundary so a long
    Newsweb announcement cannot overflow the smallest model's context.
    """
    title = (title or "").strip() or "(no title)"
    body = (body or "").strip()
    if len(body) > max_body_chars:
        body = body[:max_body_chars].rsplit(" ", 1)[0].rstrip() + " ...[truncated]"
    body = body or "(no body)"
    return f"Target company: {name} ({ticker})\nTITLE: {title}\nBODY:\n{body}"


# Calibrated few-shot examples — one per outcome the model must get right:
# a direct profit-negative item, a routine admin item (neutral), a namesake
# false match (off_topic), and a clear earnings beat (positive). They double as
# the format spec: the model copies their exact JSON shape. Keep them short.
_FEW_SHOT_RAW: list[tuple[str, str, str | None, str | None, str]] = [
    (
        "SalMar ASA", "SALM",
        "SalMar nedjusterer slaktevolum for 2026 etter økt lusepåslag",
        "SalMar varsler lavere slaktevolum enn tidligere guidet som følge av økt "
        "lakselus i Midt-Norge, og venter høyere kostnad per kilo i andre halvår.",
        '{"label": "negative", "relevance": "direct", "rationale": "Cut 2026 harvest '
        'guidance and higher costs from a sea-lice outbreak."}',
    ),
    (
        "Mowi ASA", "MOWI",
        "Innkalling til ordinær generalforsamling i Mowi ASA",
        "Styret innkaller til ordinær generalforsamling 5. juni 2026. Saksliste og "
        "fullmaktsskjema er vedlagt.",
        '{"label": "neutral", "relevance": "direct", "rationale": "Routine AGM notice '
        'with no price-relevant information."}',
    ),
    (
        "Grieg Seafood ASA", "GSF",
        "Edvard Grieg-feltet øker oljeproduksjonen i Nordsjøen",
        "Aker BP melder økt produksjon på Edvard Grieg-feltet. Feltet er oppkalt "
        "etter komponisten og har ingen tilknytning til oppdrettsnæringen.",
        '{"label": "neutral", "relevance": "off_topic", "rationale": "About the Edvard '
        'Grieg oil field, not Grieg Seafood."}',
    ),
    (
        "Bakkafrost P/F", "BAKKA",
        "Bakkafrost slår forventningene med rekordhøy EBIT i Q1",
        "Bakkafrost leverte operasjonell EBIT godt over konsensus, drevet av høye "
        "laksepriser og lavere kostnader på Færøyene.",
        '{"label": "positive", "relevance": "direct", "rationale": "Q1 operating EBIT '
        'beat consensus on high salmon prices and lower costs."}',
    ),
]


def _few_shot_messages() -> list[dict[str, str]]:
    """The few-shot examples as alternating user/assistant messages."""
    messages: list[dict[str, str]] = []
    for name, ticker, title, body, answer in _FEW_SHOT_RAW:
        messages.append({"role": "user", "content": _format_user(name, ticker, title, body)})
        messages.append({"role": "assistant", "content": answer})
    return messages


def build_messages(
    ticker: str,
    name: str,
    title: str | None,
    body: str | None,
    *,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
) -> list[dict[str, str]]:
    """Build the OpenAI-style ``messages`` for one ``(article, ticker)`` pair.

    Deterministic and side-effect-free: the same inputs (and ``PROMPT_VERSION``)
    always yield the same messages, so the GUI can reconstruct the model input
    from the stored ``title`` + ``body`` without us persisting the prompt text.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_few_shot_messages(),
        {"role": "user", "content": _format_user(name, ticker, title, body,
                                                  max_body_chars=max_body_chars)},
    ]


@dataclass(frozen=True)
class ScoreResult:
    """Parsed, validated, coercion-applied model output for one row."""

    label: str  # positive | neutral | negative (post-coercion)
    score: float  # -1.0 | 0.0 | 1.0, derived from label
    relevance: str  # direct | mentioned | off_topic
    rationale: str  # may be empty


class ParseError(ValueError):
    """The model output could not be parsed into a valid ScoreResult."""


# Reasoning models (NorwAI-Magistral-reasoning, gpt-oss, …) emit a chain of
# thought before the answer, often wrapped in <think>…</think>. Strip it so the
# extractor does not parse a brace from inside the reasoning.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _json_objects(text: str) -> list[str]:
    """Every balanced ``{...}`` substring in ``text``, in order of appearance.

    A simple brace scan (depth counter, string-aware) so nested objects don't
    confuse it. The answer is the last valid one — reasoning models put their
    final JSON at the very end.
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                objects.append(text[start : i + 1])
    return objects


def parse_response(raw: str) -> ScoreResult:
    """Parse a raw model reply into a :class:`ScoreResult`.

    Tolerant of reasoning ``<think>`` blocks, code fences and surrounding prose
    (scans for balanced JSON objects and takes the **last** valid one), and of
    Norwegian/short label spellings. Applies the business rule that an
    ``off_topic`` match scores neutral / 0.0. Raises :class:`ParseError` on
    anything it cannot confidently interpret, so the client can re-ask once.
    """
    cleaned = _THINK_RE.sub(" ", raw or "")
    data = None
    for candidate in reversed(_json_objects(cleaned)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "label" in parsed:
            data = parsed
            break
    if data is None:
        raise ParseError(f"no JSON object with a label in model output: {raw!r}")

    label = _LABEL_ALIASES.get(str(data.get("label", "")).strip().lower())
    if label not in VALID_LABELS:
        raise ParseError(f"missing/invalid label: {data.get('label')!r}")

    # Relevance defaults to "direct" if absent (the common case); only an
    # explicitly invalid value is an error.
    relevance_raw = str(data.get("relevance", "direct")).strip().lower()
    relevance = _RELEVANCE_ALIASES.get(relevance_raw)
    if relevance not in VALID_RELEVANCE:
        raise ParseError(f"invalid relevance: {data.get('relevance')!r}")

    rationale = str(data.get("rationale", "")).strip()

    # Business rule: an off-topic keyword match has no signal for this ticker.
    if relevance == "off_topic":
        label = "neutral"

    return ScoreResult(
        label=label,
        score=LABEL_SCORES[label],
        relevance=relevance,
        rationale=rationale,
    )
