# SAB GUI — Dashboard shell + Article Review Page (build spec / prompt)

Status: **planned (Phase 4.x)**. Build spec for the **SAB** dashboard and its first
view, the article review / audit page. Written to double as a ready-to-use prompt
for an engineer or agent — see [The prompt](#the-prompt) at the bottom.

## Product context

The review page is **one view inside the SAB dashboard**, not a standalone page.
SAB ("Sentiment Analysis Bot") will grow more views as later phases land:

- **now (Phase 4.x):** article review / audit (this doc)
- **later:** per-ticker sentiment over time, **price overlay**, signal generation,
  and **projections / predictions** (Phase 3 outputs) — typically time-series and
  chart-heavy.

So build an **app shell** (persistent nav, shared theme, shared read API, routing)
that the review page plugs into and future views slot beside — do not hard-code a
single-page layout. See [App shell & future views](#app-shell--future-views).

## Purpose

A read-only audit trail over the scraped news. A reviewer can:

- browse every article that was ingested,
- see **which company/companies** each article was matched to,
- see the **sentiment** assigned per company (or that it is **not yet scored**),
- read **exactly the text the model received**, so a score can be trusted/verified.

This is an auditing surface, not an editing one. Scoring happens in the Phase 2
pipeline; this page only displays its results.

## What the data model gives you (ground truth)

Two tables (`src/data/models.py`):

**`articles`** — one row per `(ticker, url)`:

| column | meaning |
|--------|---------|
| `id` | PK |
| `ticker` | the matched company, e.g. `MOWI` |
| `source` | `newsweb` or `gnews` |
| `url` | canonical link (Newsweb) or Google News redirect (gnews) |
| `published` | publish time (nullable) |
| `title` | headline |
| `body` | full text (Newsweb) or cleaned summary/snippet (gnews); **nullable** |
| `fetched_at` | when scraped |

**`sentiment`** — zero-or-more rows per article:

| column | meaning |
|--------|---------|
| `article_id` | FK → `articles.id` |
| `score` | float, −1.0 … 1.0 |
| `label` | `positive` \| `negative` \| `neutral` |
| `model` | model id used |
| `scored_at` | when scored |

### The key structural fact: one article → many rows

Because uniqueness is `(ticker, url)`, **a single news article that mentions N
companies is stored as N rows** — same `url`, same `title`/`body`, different
`ticker`, each scored independently. Newsweb announcements are single-company, so
they are always one row.

The GUI must **collapse this back to one card per article**:

> **Group by `url`.** Each group = one article card. Within the group, each
> `ticker` becomes one company bubble. (A group is always single-source, since
> `url` differs by source.)

For each `(ticker, url)` row, its sentiment is the **latest** `sentiment` row for
that `article_id` (`max(scored_at)`); a row with no sentiment is **unscored**.

## The card

One card per article (per `url`). Layout:

```
┌────────────────────────────────────────────────────────────┐
│ [gnews]  ILA påvist på Mowi-lokalitet – Kyst.no             │  source badge + title
│ 2026-06-09 14:05 · open original ↗                          │  published + link to url
│ ( MOWI ▲ +0.62 )  ( SALM ◌ unscored )                       │  one bubble per company
│ ▸ Model input (title + body)                                │  collapsible, see below
└────────────────────────────────────────────────────────────┘
```

### Company–sentiment bubbles

One bubble per company matched to the article, placed under the title. The bubble
shows the ticker and its sentiment, and is colored by `label`:

| State | Color | Content example |
|-------|-------|-----------------|
| positive | green | `MOWI ▲ +0.62` |
| neutral | gray/amber | `MOWI ■ 0.04` |
| negative | red | `MOWI ▼ −0.55` |
| **unscored** (no sentiment row) | outline / muted, visually distinct | `SALM ◌ unscored` |

Rules:
- **Unscored ≠ neutral.** If there is no `sentiment` row for that ticker's
  article, render the explicit *unscored* style — never a 0.0 / neutral score.
  Neutral is a real model verdict; unscored is "the model has not run."
- Color is driven by `label`; the numeric `score` is shown in the bubble (or on
  hover). Optionally shade intensity by `|score|`.
- Bubbles are per company, so a mixed article (e.g. MOWI negative, SALM positive)
  reads at a glance.

### Model input ("what the LLM received")

Collapsible section showing the **reconstructed** scorer input — this is the audit
core. The input is **not stored**; it is rebuilt from what is in the DB:

> model input = `title` + `body` (+ the per-company framing for that ticker)

Because scoring is constrained to use only `title` + `body` (see Phase 2
constraint below), displaying those columns *is* showing what the model saw. Show
it per bubble/company if the framing differs per ticker.

When prompt-editing lands later (prompts stored in DB, `sentiment.prompt_version`
recorded), also show the prompt version used so the exact rendered prompt is
reproducible. Until then, the template is deterministic and versioned in code.

## List behavior (MVP)

- Default sort: newest `published` first (nulls last), then `fetched_at`.
- Filters: company (`ticker`), sentiment `label`, `source`, **scored / unscored**,
  date range, free-text on `title`.
- Pagination or infinite scroll.
- Each card links out to the original `url` (gnews opens via Google redirect).

Out of scope for v1: editing, re-scoring, comments, full-article fetch for gnews
(body stays snippet-only until/unless that feature is added).

## Visual design & theming

Look and feel: **sleek, modern, VS Code–inspired.**

- **Dark by default**, with a **light/dark toggle** (persist the choice). Dark theme
  is the primary, polished one; light is a clean secondary.
- **VS Code aesthetic:** deep neutral background (near-black charcoal, not pure
  black), subtle panel borders, comfortable density, monospace for ids/urls/scores.
- **Bright accent colors** against the dark base — used for sentiment, links, active
  nav, and (later) chart series. Keep accents few and consistent.
- Define theme as **tokens** (CSS variables / theme object), not hard-coded colors,
  so the toggle and future chart views share one palette. Suggested tokens:

  | token | dark | role |
  |-------|------|------|
  | `--bg` | `#1e1e1e` | app background |
  | `--panel` | `#252526` | cards, nav |
  | `--border` | `#333` | dividers |
  | `--text` | `#d4d4d4` | body text |
  | `--muted` | `#858585` | secondary text, unscored |
  | `--accent` | `#4fc3f7` | links, active nav |
  | `--pos` | `#4ec9b0` | positive sentiment |
  | `--neu` | `#d7ba7d` | neutral sentiment |
  | `--neg` | `#f48771` | negative sentiment |

  (Colors are a starting palette — tune to taste; keep the *roles* stable.)
- Sentiment bubbles use `--pos` / `--neu` / `--neg`; the unscored bubble uses
  `--muted` as an outline (no fill) so it reads as "absent," not a verdict.
- Sleek, not heavy: rounded corners, restrained shadows, smooth hover/expand
  transitions, no clutter.

## App shell & future views

Structure for growth, build only the review view now:

- **Persistent shell:** top bar (SAB wordmark, theme toggle, global filters/search)
  + side or top **nav** listing views. Review view is the first nav item.
- **Routing:** one route per view (`/review`, and later `/sentiment`, `/signals`,
  `/projections`). The shell, theme, and data client are shared.
- **Shared read API:** a thin read-only data layer over the SQLite DB
  (`src/data/db.py`, async). Future views (price overlay, projections) query the
  same layer — design it to return JSON the frontend can chart, not page-specific
  HTML only.
- **Charts later:** projection/prediction views are time-series heavy. Pick a stack
  (below) that can host an interactive chart lib cleanly; reserve a place in the nav
  and theme tokens (`--accent` + sentiment colors as series colors) now.

## Open decisions (confirm before building)

- **Stack — DECIDED: React + Vite + TypeScript SPA over a thin FastAPI read-only
  API** (read layer over the existing async SQLite, `src/data/db.py`). Chosen for the
  sleek VS Code–style themeable UI and future interactive projection/price charts
  (Recharts/visx). Not yet scaffolded — build deferred.
- **Sentiment selection** when an article has several sentiment rows (re-scores or
  multiple models): default shown here is latest by `scored_at`. Decide if you also
  want a per-model view.

## The prompt

> Build the **SAB dashboard shell** and its first view, a read-only **article
> review page**, over the existing SQLite schema (`articles`, `sentiment` in
> `src/data/models.py`). Sleek, modern, **VS Code–style dark theme by default with a
> light/dark toggle**, deep charcoal background + a few **bright accents**, theme as
> tokens. Build a persistent shell (SAB wordmark, theme toggle, nav, shared read API,
> routing) so future chart-heavy views (sentiment-over-time, price overlay,
> projections/predictions) slot beside the review view — but implement only the
> review view now. Requirements:
>
> 1. **One card per article**, obtained by grouping `articles` by `url` (an article
>    is stored once per matched company, so the same `url` repeats across tickers).
> 2. Under each card's title, render **one bubble per company** (`ticker`) in that
>    group. Color the bubble by the latest `sentiment.label` for that ticker's
>    article row: green=positive, red=negative, gray=neutral. Show the `score`.
> 3. If a ticker's article has **no `sentiment` row, show an explicit "unscored"
>    bubble** in a distinct muted/outline style — never a fake neutral/0.0.
> 4. A collapsible **"model input"** section per card showing `title` + `body`
>    (the reconstructed text the scorer received). Do not fetch anything extra.
> 5. List newest `published` first; filters for company, sentiment label, source,
>    scored/unscored, and date range; free-text search on title; link each card out
>    to its `url`.
> 6. Read-only. No editing, no re-scoring. Keep it minimal.
>
> Confirm the GUI stack with the owner before writing code (FastAPI+templates /
> HTMX / Streamlit / SPA). Match the project's existing style and async DB access
> (`src/data/db.py`).
