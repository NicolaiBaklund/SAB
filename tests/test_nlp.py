"""Tests for the Phase 2.1 sentiment scoring stack.

Pure logic (prompt building, JSON parsing/coercion, eval metrics) is tested
directly. The IDUN client is tested with ``httpx.MockTransport`` (no network).
The scorer is tested with a fake client and the shared in-memory ``session``
fixture from ``conftest.py``.
"""
from datetime import datetime

import httpx
import pytest
from sqlalchemy import select

from src.data.models import Article, Sentiment
from src.nlp.client import IdunClient, IdunError, RateLimiter
from src.nlp.eval import accuracy, cohen_kappa, macro_f1
from src.nlp.prompt import (
    PROMPT_VERSION,
    ParseError,
    ScoreResult,
    _json_objects,
    build_messages,
    parse_response,
)
from src.nlp import scorer


# --------------------------------------------------------------------------- #
# prompt: build_messages
# --------------------------------------------------------------------------- #
def test_build_messages_shape():
    msgs = build_messages("MOWI", "Mowi ASA", "Title", "Body text")
    assert msgs[0]["role"] == "system"
    # system + 4 few-shot pairs (8) + 1 user
    assert len(msgs) == 1 + 8 + 1
    assert msgs[-1]["role"] == "user"
    user = msgs[-1]["content"]
    assert "Mowi ASA (MOWI)" in user
    assert "Title" in user and "Body text" in user


def test_build_messages_is_deterministic():
    a = build_messages("SALM", "SalMar ASA", "t", "b")
    b = build_messages("SALM", "SalMar ASA", "t", "b")
    assert a == b


def test_build_messages_truncates_body():
    body = "word " * 10_000
    user = build_messages("GSF", "Grieg Seafood ASA", "t", body, max_body_chars=100)[-1]["content"]
    assert "...[truncated]" in user
    # Body portion is bounded near the cap (plus the marker), not the full 50k chars.
    assert len(user) < 400


def test_build_messages_handles_missing_title_body():
    user = build_messages("AUSS", "Austevoll Seafood ASA", None, None)[-1]["content"]
    assert "(no title)" in user and "(no body)" in user


# --------------------------------------------------------------------------- #
# prompt: parse_response
# --------------------------------------------------------------------------- #
def test_parse_plain_json():
    res = parse_response('{"label": "positive", "relevance": "direct", "rationale": "beat"}')
    assert res == ScoreResult("positive", 1.0, "direct", "beat")


def test_parse_derives_score_from_label():
    assert parse_response('{"label":"negative","relevance":"direct"}').score == -1.0
    assert parse_response('{"label":"neutral","relevance":"direct"}').score == 0.0


def test_parse_off_topic_coerced_to_neutral():
    res = parse_response('{"label": "positive", "relevance": "off_topic", "rationale": "namesake"}')
    assert res.label == "neutral" and res.score == 0.0 and res.relevance == "off_topic"


def test_parse_strips_code_fence_and_prose():
    raw = 'Here is my answer:\n```json\n{"label": "negative", "relevance": "direct"}\n```'
    assert parse_response(raw).label == "negative"


def test_parse_reasoning_think_block_and_last_object():
    # A reasoning model emits a brace inside <think>, then the real answer last.
    raw = (
        "<think>Maybe {positive}? The lice cut guidance, so it's bad.</think>"
        '{"label": "negative", "relevance": "direct", "rationale": "lice cut guidance"}'
    )
    assert parse_response(raw).label == "negative"


def test_parse_norwegian_aliases():
    assert parse_response('{"label": "positiv", "relevance": "direkte"}').label == "positive"
    assert parse_response('{"label": "nøytral", "relevance": "nevnt"}').relevance == "mentioned"


def test_parse_relevance_defaults_direct():
    assert parse_response('{"label": "positive"}').relevance == "direct"


def test_parse_invalid_label_raises():
    with pytest.raises(ParseError):
        parse_response('{"label": "bullish", "relevance": "direct"}')


def test_parse_no_json_raises():
    with pytest.raises(ParseError):
        parse_response("I think it is positive.")


def test_json_objects_balanced_scan():
    objs = _json_objects('a {"x": {"y": 1}} b {"z": 2} c')
    assert objs == ['{"x": {"y": 1}}', '{"z": 2}']


def test_json_objects_ignores_brace_in_string():
    assert _json_objects('{"k": "a } b"}') == ['{"k": "a } b"}']


# --------------------------------------------------------------------------- #
# client: IdunClient over MockTransport
# --------------------------------------------------------------------------- #
def _reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _client(handler, **kw) -> tuple[IdunClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://t")
    # Huge rpm => negligible limiter interval, so tests don't actually wait.
    client = IdunClient(
        http, model="m", api_key="k", base_url="http://t",
        limiter=RateLimiter(1_000_000), **kw,
    )
    return client, http


@pytest.mark.asyncio
async def test_client_returns_content():
    client, http = _client(lambda req: httpx.Response(200, json=_reply("hello")))
    try:
        assert await client.complete([{"role": "user", "content": "hi"}]) == "hello"
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_client_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("src.nlp.client.asyncio.sleep", _noop_sleep)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json=_reply("ok"))

    client, http = _client(handler)
    try:
        assert await client.complete([{"role": "user", "content": "hi"}]) == "ok"
        assert calls["n"] == 2
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_client_fatal_status_raises():
    client, http = _client(lambda req: httpx.Response(401, text="bad key"))
    try:
        with pytest.raises(IdunError):
            await client.complete([{"role": "user", "content": "hi"}])
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_client_json_object_flag_sets_response_format():
    seen = {}

    def handler(req):
        import json as _json
        seen.update(_json.loads(req.content))
        return httpx.Response(200, json=_reply("x"))

    client, http = _client(handler, json_object=True)
    try:
        await client.complete([{"role": "user", "content": "hi"}])
        assert seen["response_format"] == {"type": "json_object"}
        assert seen["temperature"] == 0.0
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_client_self_heals_when_server_rejects_response_format(monkeypatch):
    monkeypatch.setattr("src.nlp.client.asyncio.sleep", _noop_sleep)
    had_rf: list[bool] = []

    def handler(req):
        import json as _json
        body = _json.loads(req.content)
        had_rf.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, text="response_format is not supported by this model")
        return httpx.Response(200, json=_reply("ok"))

    client, http = _client(handler)  # json_object defaults on now
    try:
        assert await client.complete([{"role": "user", "content": "hi"}]) == "ok"
        # First attempt carried response_format (-> 400), retry dropped it (-> 200).
        assert had_rf == [True, False]
    finally:
        await http.aclose()


async def _noop_sleep(_seconds):
    return None


@pytest.mark.asyncio
async def test_rate_limiter_spaces_calls(monkeypatch):
    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr("src.nlp.client.asyncio.sleep", record)
    limiter = RateLimiter(60)  # 1s interval
    await limiter.acquire()  # first: no wait
    await limiter.acquire()  # second: ~1s wait
    assert slept and 0.8 <= slept[-1] <= 1.0


# --------------------------------------------------------------------------- #
# scorer: with a fake client + in-memory DB
# --------------------------------------------------------------------------- #
class FakeClient:
    """Stand-in for IdunClient: returns queued replies, records call count."""

    def __init__(self, model: str, replies):
        self.model = model
        self._replies = list(replies)
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.asyncio
async def test_score_article_parses(monkeypatch):
    monkeypatch.setattr(scorer, "load_companies", lambda: [])
    art = Article(ticker="GSF", source="gnews", url="u", title="t", body="b",
                  fetched_at=datetime(2026, 6, 9))
    client = FakeClient("m", ['{"label":"negative","relevance":"direct","rationale":"lice"}'])
    res = await scorer.score_article(client, art, "Grieg Seafood ASA")
    assert res.label == "negative" and res.score == -1.0


@pytest.mark.asyncio
async def test_score_article_reasks_once_on_bad_json():
    art = Article(ticker="GSF", source="gnews", url="u", title="t", body="b",
                  fetched_at=datetime(2026, 6, 9))
    client = FakeClient("m", ["not json", '{"label":"neutral","relevance":"direct"}'])
    res = await scorer.score_article(client, art, "Grieg Seafood ASA")
    assert res.label == "neutral" and client.calls == 2


@pytest.mark.asyncio
async def test_score_writes_rows_and_is_model_scoped(session, monkeypatch):
    monkeypatch.setattr(scorer, "load_companies",
                        lambda: [{"ticker": "MOWI", "name": "Mowi ASA"}])
    session.add_all([
        Article(ticker="MOWI", source="gnews", url="u1", title="t1", body="b1",
                fetched_at=datetime(2026, 6, 9)),
        Article(ticker="MOWI", source="gnews", url="u2", title="t2", body="b2",
                fetched_at=datetime(2026, 6, 9)),
    ])
    await session.flush()

    client = FakeClient("model-A", [
        '{"label":"positive","relevance":"direct","rationale":"beat"}',
        '{"label":"neutral","relevance":"mentioned","rationale":"peer"}',
    ])
    written = await scorer.score(client, session, now=datetime(2026, 6, 9))
    assert written == 2

    rows = list((await session.execute(select(Sentiment))).scalars())
    assert {r.label for r in rows} == {"positive", "neutral"}
    assert all(r.model == "model-A" and r.prompt_version == PROMPT_VERSION for r in rows)
    assert all(r.relevance in {"direct", "mentioned"} for r in rows)

    # Re-running the same model finds nothing new; a different model re-scores all.
    assert await scorer.score(FakeClient("model-A", []), session) == 0
    client_b = FakeClient("model-B", [
        '{"label":"negative","relevance":"direct"}',
        '{"label":"negative","relevance":"direct"}',
    ])
    assert await scorer.score(client_b, session) == 2


@pytest.mark.asyncio
async def test_score_skips_failed_rows(session, monkeypatch):
    monkeypatch.setattr(scorer, "load_companies",
                        lambda: [{"ticker": "SALM", "name": "SalMar ASA"}])
    session.add(Article(ticker="SALM", source="gnews", url="u", title="t", body="b",
                        fetched_at=datetime(2026, 6, 9)))
    await session.flush()
    # Both the initial call and the re-ask return junk -> row skipped, not written.
    client = FakeClient("m", ["junk", "still junk"])
    assert await scorer.score(client, session) == 0
    assert list((await session.execute(select(Sentiment))).scalars()) == []


# --------------------------------------------------------------------------- #
# eval: metrics
# --------------------------------------------------------------------------- #
def test_accuracy():
    assert accuracy(["a", "b", "c"], ["a", "x", "c"]) == pytest.approx(2 / 3)


def test_macro_f1_perfect_and_degenerate():
    gold = ["positive", "neutral", "negative"]
    assert macro_f1(gold, gold) == pytest.approx(1.0)
    # Always "neutral" => only the neutral class scores, macro-F1 is low.
    assert macro_f1(gold, ["neutral"] * 3) < 0.4


def test_cohen_kappa_perfect_and_chance():
    assert cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1.0)
    # No agreement beyond chance on a balanced set => kappa around 0 or below.
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) <= 0.0
