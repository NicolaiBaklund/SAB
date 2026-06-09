# Sentiment Scoring (Phase 2)

How SAB turns a stored news article into a per-company sentiment score. This is
the design doc for everything under `src/nlp/`.

## The question we score

For each `(article, ticker)` pair we ask **one** thing:

> Does this news item raise or lower the expected **share price** of *this*
> company over the next few trading days?

This is a **price-impact / investor** lens, not editorial tone. It is the lens
that makes the output useful as a trading signal:

- A sea-lice outbreak reads as neutral *tone* but is price-**negative**.
- A record salmon spot price is price-**positive**.
- An AGM notice is **neutral** (administrative, no price information).

## Output scale — 3-point, categorical

The model emits a **category**, and we derive the number:

| label      | score |
|------------|-------|
| `negative` | −1.0  |
| `neutral`  |  0.0  |
| `positive` | +1.0  |

We deliberately rejected a continuous `[-1, 1]` score. An LLM cannot reproducibly
distinguish 0.62 from 0.71 — that apparent precision is noise (a false sense of
regression). Three classes is also the dominant convention in financial sentiment
research (Financial PhraseBank, FinBERT). The model never sees or emits the float;
categories are what LLMs label reliably. `score` is stored for aggregation,
`label` for display — they can never disagree because one is derived from the
other.

> If intensity later proves useful (e.g. distinguishing a profit warning from a
> lukewarm update), the natural next step is a 5-point ordinal
> `{-1, -0.5, 0, +0.5, +1}` — it fits the same `score REAL` column with no
> migration. We start at 3-point and revisit only if the data shows a need.

## Relevance — the false-positive guard

The scrapers match on bare keywords, so "Grieg" matches the *Edvard Grieg* oil
field, not *Grieg Seafood*. The model therefore also returns a `relevance`:

| relevance   | meaning                                                  |
|-------------|----------------------------------------------------------|
| `direct`    | the item is materially about the target company          |
| `mentioned` | the company appears but is not the main subject          |
| `off_topic` | keyword matched the wrong subject / company is irrelevant |

`off_topic` is **coerced to `neutral` / 0.0** by the parser (an off-topic match
carries no signal for this ticker), but the flag is stored so the review GUI can
surface keyword false matches for `companies.json` cleanup.

## Output contract

The model returns a single minified JSON object and nothing else:

```json
{"label": "negative", "relevance": "direct", "rationale": "Cut 2026 harvest guidance from a sea-lice outbreak."}
```

`rationale` is a one-line English justification (≤ 25 words) naming the main price
driver. It is stored for human review and for debugging wrong scores. The parser
(`prompt.parse_response`) is tolerant of reasoning-model `<think>` blocks, code
fences, surrounding prose, and Norwegian/short label spellings; it takes the
**last** valid JSON object (reasoning models put their answer last). On anything
it cannot confidently interpret it raises `ParseError`, and the scorer re-asks
once before skipping the row.

## Per-ticker attribution falls out of the schema

`articles` uniqueness is `(ticker, url)`, so the scrapers already store a
multi-company article **once per ticker**. Scoring is therefore just "score each
unscored row", and each row's prompt judges sentiment toward exactly one company.
No fan-out at score time.

## Model choice — decided by a bake-off, not a spec sheet

Every IDUN model is free, so "free + Norwegian-aware" no longer selects the small
NorwAI model over the (also Norwegian-capable) frontier models. We decided
empirically with `src/nlp/eval.py` over a 40-item hand-labeled gold set
(`data/eval/goldset.jsonl`: 6 positive, 6 negative, 6 off_topic, 22 neutral).

### Guided JSON is required, not optional

Reasoning models (NorwAI-Magistral, Qwen-thinking) **ignore** a "return only JSON"
instruction — they answer in Norwegian prose or leak every token into reasoning
and return empty content, failing the contract on most items. So
`response_format=json_object` (vLLM guided decoding) is mandatory: it is **on by
default** in the client, which transparently self-heals (drops it and retries) if
a server ever rejects it.

### Results (40 items, temperature 0)

| model | acc | macro-F1 | off-topic recall | self-consistency | latency |
|-------|-----|----------|------------------|------------------|---------|
| **Mistral-Large-3-675B** (chosen) | 0.93 | 0.89 | **1.00** | 1.00 | **3.3s** |
| GLM-4.7-FP8 | **1.00** | **1.00** | 0.83 | 1.00 | 14.5s |
| Qwen3.5-122B | 0.85 | 0.75 | 0.17 | — | 12.6s |
| NorwAI-Magistral-24B | 0.82 | 0.79 | 0.83 | — | 3.3s |
| gpt-oss-120b | 0.70 | 0.27 | 0.00 | — | 7.4s |

### Decision: Mistral-Large-3-675B (`settings.IDUN_MODEL`)

GLM-4.7 had the best raw label accuracy, but that metric is measured against *our*
labels (hand-labeled by a non-expert, 11 of 40 flagged uncertain), so the 0.07 gap
is within labeling noise. The two metrics that are **not** label-source dependent
both favor Mistral: **off-topic recall** (objective — football fixtures and the
Edvard Grieg oil field are not salmon news; Mistral caught all 6, GLM 5) and
**latency** (4.5× faster, which matters for the 591-article backfill and prompt
re-scoring). Both finalists were perfectly self-consistent at temperature 0.

GLM-4.7 is the documented high-accuracy alternative if a domain expert ever
relabels the gold set. Qwen (off-topic recall 0.17) and gpt-oss (collapses to
all-neutral, F1 0.27) were eliminated.

**Caveat:** the gold labels are not necesarily the "true" labels, and the set is small, so we will revisit the decision if the first full scoring run surfaces systematic errors (e.g. off-topic false positives slipping through, or a pattern of mislabeling a certain type of news).

## Determinism & the audit constraint

- **Temperature 0**, fixed `PROMPT_VERSION`, deterministic template. The same
  article yields the same prompt every run.
- The prompt is built **only** from the stored `title` + `body` plus the
  per-ticker framing — no fetching or enrichment at score time. So the review GUI
  can reconstruct exactly what the model saw from the two stored columns and the
  versioned template. `prompt_version` is stored on every row; changing the
  template text or few-shot set means bumping it.

## Rate limits

IDUN allows **20 req/min and 300k tokens/min**. The client serialises requests
with a minimum interval (default 18 req/min for headroom). The body cap
(`prompt.DEFAULT_MAX_BODY_CHARS`, ~24k chars ≈ ~8k tokens) keeps each request well
under the token rate, so enforcing the request rate is enough — no tokeniser
needed. Run off-peak (18:00–06:00 / weekends).

## Stored row (`sentiment` table)

| column           | meaning                                             |
|------------------|-----------------------------------------------------|
| `score`          | −1.0 / 0.0 / +1.0 (derived from `label`)            |
| `label`          | positive / neutral / negative                       |
| `relevance`      | direct / mentioned / off_topic                      |
| `rationale`      | one-line model justification                        |
| `model`          | IDUN model id that produced the score               |
| `prompt_version` | template version (e.g. `price-impact-v1`)           |
| `scored_at`      | UTC timestamp                                        |

## Files

| File                 | Responsibility                                            |
|----------------------|-----------------------------------------------------------|
| `src/nlp/prompt.py`  | versioned template, `build_messages`, `parse_response`    |
| `src/nlp/client.py`  | rate-limited IDUN HTTP client + retries                   |
| `src/nlp/scorer.py`  | score unscored rows → write `sentiment`; CLI + `--dry-run`|
| `src/nlp/eval.py`    | model bake-off: gold-set sampler + metrics                |

## Commands

```bash
# Inspect the exact prompt for real articles (no API call)
python -m src.nlp.scorer --dry-run --limit 3

# Bake-off: sample, hand-label data/eval/goldset.jsonl, then compare models
python -m src.nlp.eval sample --n 30
python -m src.nlp.eval run --k 2

# Score all unscored articles with the chosen model (settings.IDUN_MODEL)
python -m src.nlp.scorer
```

## Known gaps / next steps

- **Cross-source dedup** (same event in Newsweb *and* Google News under different
  URLs) is still not handled — it would be scored once per source. Best addressed
  here at scoring/aggregation time via fuzzy match on ticker + title + date.
- **Aggregation** (rolling per-ticker sentiment over a window) is Phase 2.2 / 3.
- **Prompt language**: instructions are English, article content stays Norwegian.
  If the NorwAI model underperforms in the bake-off, A/B a Norwegian-instruction
  variant (bump `PROMPT_VERSION`).
