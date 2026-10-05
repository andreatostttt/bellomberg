"""AI summary of one news article: synthetic DB, fake HTTP and fake model only."""
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from bellomberg.core import llm_client as lc
from bellomberg.market_data import article_summary as asum
from bellomberg.core import llm_usage

PARAGRAPH = ("Synthetic Corp reported quarterly revenue of 4.2 billion dollars, up 12 percent "
             "year on year, and raised its full-year guidance on stronger demand. ")
ARTICLE_HTML = (
    "<html><head><title>Synthetic story</title><script>var tracking = 'SCRIPT_NOISE';</script></head>"
    "<body><nav><p>NAV_NOISE menu link one, menu link two, menu link three</p></nav>"
    "<header><p>HEADER_NOISE site banner with long promotional words</p></header>"
    "<article><h1>Synthetic Corp beats</h1>"
    + "".join(f"<p>{PARAGRAPH} Paragraph {i}.</p>" for i in range(8))
    + "</article><aside><p>ASIDE_NOISE related stories you might like to read</p></aside>"
    "<footer><p>FOOTER_NOISE copyright and legal small print text</p></footer></body></html>")
PAYWALL_HTML = ("<html><body><article><p>Synthetic Corp reported results that investors watched closely.</p>"
                "<div class='paywall-overlay'><p>Subscribe to continue reading this article today.</p></div>"
                "</article></body></html>")
SHORT_HTML = "<html><body><article><p>Only a short teaser of the synthetic story is here.</p></article></body></html>"


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "news.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE news_feed (id INTEGER PRIMARY KEY, title, snippet, source, url UNIQUE, "
                     "published_at, pulled_at, ticker_mentioned, theme, provider, sentiment, sentiment_score, "
                     "relevance, notified, headline_it, why_matters)")
        rows = [
            (1, "Synthetic Corp beats", "snippet", "fake", "https://news.example.com/story", "SYNTH", "Peso 4% del book"),
            (2, "No url item", "snippet", "fake", "nourl:abc123", "", ""),
            (3, "Local item", "snippet", "fake", "http://localhost/admin", "", ""),
            (4, "Paywalled", "snippet", "fake", "https://paper.example.com/locked", "", ""),
            (5, "Teaser", "snippet", "fake", "https://news.example.com/teaser", "", ""),
        ]
        for nid, title, snippet, source, url, ticker, why in rows:
            conn.execute("INSERT INTO news_feed (id, title, snippet, source, url, published_at, pulled_at, "
                         "ticker_mentioned, theme, provider, sentiment, sentiment_score, relevance, notified, "
                         "headline_it, why_matters) VALUES (?,?,?,?,?,'',?,?,'','fake','neutral',0,7,0,'',?)",
                         (nid, title, snippet, source, url, datetime.now().isoformat(), ticker, why))
    usage_rows = []
    monkeypatch.setattr(asum, "MemoryDB", lambda: NS(db_path=str(path),
                                                     save_llm_usage=lambda memo_id, log: usage_rows.extend(log)))
    pages = {
        "https://news.example.com/story": ARTICLE_HTML,
        "https://paper.example.com/locked": PAYWALL_HTML,
        "https://news.example.com/teaser": SHORT_HTML,
    }
    fetched = []

    def fake_get(url, headers, timeout):
        fetched.append((url, headers))
        return FakeResponse(pages[url])

    monkeypatch.setattr(asum, "_http_get", fake_get)
    monkeypatch.setattr(asum, "_check_public_host", lambda host, port: None
                        if host != "localhost" else (_ for _ in ()).throw(ValueError("host locale non consentito")))
    monkeypatch.setattr(llm_usage, "MemoryDB", lambda: NS(save_llm_usage=lambda memo_id, log: usage_rows.extend(log)))
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "cost_eur", lambda model, usage, ttl=None: {
        "cost": 0.0009, "status": "ok", "fx_rate": 0.9, "fx_source": "live",
        "breakdown": {"cost_usd": usage.get("cost_usd")}})
    monkeypatch.setattr(lc, "modello", lambda funzione, *a: "fake/summary-model")
    prompts = []

    def create(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        en = "Answer in English" in prompt
        body = {"summary": "English summary." if en else "Sintesi italiana.",
                "points": ["p1", "p2", "p3"], "key_numbers": ["Revenue $4.2bn", "+12% y/y"],
                "portfolio": "Positive for SYNTH." if en else "Positivo per SYNTH."}
        return NS(content=[lc.TextBlock(json.dumps(body))],
                  usage=NS(input_tokens=900, output_tokens=120, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.001))

    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: NS(messages=NS(create=create)))
    return NS(path=path, prompts=prompts, fetched=fetched, usage=usage_rows, pages=pages)


def test_extraction_picks_article_body_and_drops_chrome():
    out = asum.extract_article_text(ARTICLE_HTML)
    assert "Paragraph 0." in out["text"] and "Paragraph 7." in out["text"]
    for noise in ("SCRIPT_NOISE", "NAV_NOISE", "HEADER_NOISE", "ASIDE_NOISE", "FOOTER_NOISE"):
        assert noise not in out["text"]
    assert not out["paywall_marker"]


def test_extraction_without_article_uses_densest_block():
    html = ("<html><body><div class='menu'><p>Short menu entry here ok</p></div><div class='story'>"
            + "".join(f"<p>{PARAGRAPH} Block {i}.</p>" for i in range(3)) + "</div></body></html>")
    out = asum.extract_article_text(html)
    assert out["text"].count("Synthetic Corp") == 3 and "menu entry" not in out["text"]


def test_not_configured_makes_no_fetch_and_no_model_call(env, monkeypatch):
    def missing(funzione, *a):
        raise lc.ConfigurazioneLLMMancante("NEWS_SUMMARY_MODEL")
    monkeypatch.setattr(lc, "modello", missing)
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: pytest.fail("no client without a model"))
    result = asum.summarize_news_article(1, language="en")
    assert result == {"status": "not_configured", "variable": "NEWS_SUMMARY_MODEL"}
    assert not env.fetched and not env.prompts and not env.usage


def test_real_blank_variable_reports_not_configured(tmp_path, monkeypatch):
    """The real resolver (no fake modello): a blank NEWS_SUMMARY_MODEL is «not configured»."""
    path = tmp_path / "news.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE news_feed (id INTEGER PRIMARY KEY, title, url, ticker_mentioned, why_matters)")
        conn.execute("INSERT INTO news_feed VALUES (1, 't', 'https://news.example.com/story', '', '')")
    monkeypatch.setenv("NEWS_SUMMARY_MODEL", "")
    monkeypatch.setattr(asum, "MemoryDB", lambda: NS(db_path=str(path)))
    monkeypatch.setattr(asum, "_http_get", lambda *a: pytest.fail("no download when not configured"))
    assert asum.summarize_news_article(1, language="it") == {
        "status": "not_configured", "variable": "NEWS_SUMMARY_MODEL"}


@pytest.mark.parametrize("news_id,reason", [(2, "no_url"), (3, "unsafe_url"), (4, "paywall"), (5, "too_short")])
def test_unreadable_articles_never_call_the_model(env, news_id, reason):
    result = asum.summarize_news_article(news_id, language="en")
    assert result["status"] == "unreadable" and result["reason"] == reason
    assert isinstance(result["detail"], str) and result["detail"]
    assert not env.prompts and not env.usage
    if reason in ("no_url", "unsafe_url"):
        assert not env.fetched


@pytest.mark.parametrize("response,reason", [
    (FakeResponse(b"", status=403), "blocked"),
    (FakeResponse(b"", status=429), "blocked"),
    (FakeResponse(b"", status=402), "paywall"),
    (FakeResponse(b"%PDF-", headers={"Content-Type": "application/pdf"}), "not_html"),
    (FakeResponse(b"", status=503), "fetch"),
])
def test_http_failures_are_classified(env, monkeypatch, response, reason):
    monkeypatch.setattr(asum, "_http_get", lambda url, headers, timeout: response)
    assert asum.fetch_article_text("https://news.example.com/x", language="en")["reason"] == reason


def test_oversized_page_is_not_read(env, monkeypatch):
    monkeypatch.setattr(asum, "MAX_BYTES", 1000)
    monkeypatch.setattr(asum, "_http_get", lambda url, headers, timeout: FakeResponse(b"x" * 5000))
    assert asum.fetch_article_text("https://news.example.com/x", language="en")["reason"] == "fetch"


def test_redirect_to_private_host_is_rejected(env, monkeypatch):
    monkeypatch.setattr(asum, "_http_get", lambda url, headers, timeout: FakeResponse(
        b"", status=302, headers={"Location": "http://localhost/internal"}))
    out = asum.fetch_article_text("https://news.example.com/redirect", language="en")
    assert out == {"ok": False, "reason": "unsafe_url", "detail": out["detail"]}


def test_done_saves_cache_and_second_call_is_free(env):
    first = asum.summarize_news_article(1, language="en")
    assert first["status"] == "done" and first["cached"] is False
    assert set(first) == {"status", "news_id", "summary", "points", "key_numbers", "portfolio", "model",
                          "cost_eur", "cost_usd", "cost_status", "words", "duration_s", "created_at",
                          "language", "cached"}
    assert first["summary"] == "English summary." and first["news_id"] == 1
    assert first["model"] == "fake/summary-model" and first["cost_eur"] == 0.0009 and first["cost_usd"] == 0.001
    assert first["cost_status"] == "ok" and first["words"] > 100 and first["language"] == "en"
    assert datetime.fromisoformat(first["created_at"]).tzinfo is not None
    assert len(env.prompts) == 1 and len(env.usage) == 1
    assert env.usage[0]["agent"] == "news_summary" and env.usage[0]["model"] == "fake/summary-model"
    # the article text entered the prompt as delimited data, with the ticker context
    assert "Paragraph 3." in env.prompts[0] and "SYNTH" in env.prompts[0] and "<article>" in env.prompts[0]
    assert "SCRIPT_NOISE" not in env.prompts[0]

    second = asum.summarize_news_article(1, language="en")
    assert second["cached"] is True and second["summary"] == first["summary"]
    assert len(env.prompts) == 1 and len(env.fetched) == 1
    assert asum.read_cached_article_summary(1, language="en") == second

    third = asum.summarize_news_article(1, regenerate=True, language="en")
    assert third["cached"] is False and len(env.prompts) == 2


def test_languages_have_separate_caches_and_prompts(env):
    en = asum.summarize_news_article(1, language="en")
    assert asum.read_cached_article_summary(1, language="it") == {"status": "none"}
    it = asum.summarize_news_article(1, language="it")
    assert en["summary"] == "English summary." and it["summary"] == "Sintesi italiana."
    assert "Answer in English" in env.prompts[0] and "Rispondi in italiano" in env.prompts[1]
    assert env.fetched[0][1]["Accept-Language"].startswith("en") and env.fetched[1][1]["Accept-Language"].startswith("it")
    assert asum.read_cached_article_summary(1, language="it")["summary"] == "Sintesi italiana."
    assert len(list((env.path.parent / (env.path.name + ".news_article_summaries_v1")).rglob("*.json"))) == 2


def test_model_failure_and_bad_json_are_errors_and_not_cached(env, monkeypatch):
    class Boom(Exception):
        status_code = 400
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: NS(messages=NS(create=lambda **k: (_ for _ in ()).throw(Boom("bad request")))))
    assert asum.summarize_news_article(1, language="en")["status"] == "error"
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: NS(messages=NS(create=lambda **k: NS(
        content=[lc.TextBlock("not json at all")], usage=NS(input_tokens=1, output_tokens=1, cost_usd=None)))))
    assert asum.summarize_news_article(1, language="en")["status"] == "error"
    assert asum.read_cached_article_summary(1, language="en") == {"status": "none"}


def test_429_dopo_i_retry_del_client_e_un_errore_senza_secondo_livello(env, monkeypatch):
    """REV_G3 R3: i retry li fa il client (max_retries); qui una sola chiamata, timeout dichiarato."""
    calls, costruttori = [], []

    def create(**kwargs):
        calls.append(1)
        raise lc.APIStatusError(429, "rate limited")
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: costruttori.append(k) or NS(messages=NS(create=create)))
    assert asum.summarize_news_article(1, language="en")["status"] == "error" and len(calls) == 1
    assert costruttori == [{"timeout": llm_usage.TIMEOUT_CLICK_S}]


def test_missing_row(env):
    assert asum.summarize_news_article(999, language="en") == {"status": "missing"}
    assert asum.read_cached_article_summary(999, language="en") == {"status": "missing"}


# ---------------------------------------------------------------- endpoint
@pytest.fixture
def client(env, monkeypatch):
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api
    monkeypatch.setitem(api._SESSIONS, "synthetic-token", 9_999_999_999)
    monkeypatch.setattr(api, "news_refresh_manager", NS(start=lambda immediate=True: None, stop=lambda: None))
    api._LAST_CALL.clear()
    with TestClient(api.app, base_url="http://127.0.0.1:8765") as c:
        yield c, api
    api._LAST_CALL.clear()


def _headers(language="en", token=True):
    h = {"X-BB-Language": language}
    if token:
        h["X-BB-Token"] = "synthetic-token"
    return h


def test_endpoint_contract(client, env):
    c, api = client
    assert c.get("/news/1/article-summary", headers=_headers()).json() == {"status": "none"}
    assert c.get("/news/999/article-summary", headers=_headers()).status_code == 404
    assert c.post("/news/1/article-summary", headers=_headers(token=False)).status_code == 401
    assert not env.prompts

    done = c.post("/news/1/article-summary", headers=_headers())
    assert done.status_code == 200 and done.json()["status"] == "done" and done.json()["cached"] is False
    # throttle: an immediate second paid request is refused
    assert c.post("/news/1/article-summary?regenerate=true", headers=_headers()).status_code == 429
    api._LAST_CALL.clear()
    cached = c.post("/news/1/article-summary", headers=_headers())
    assert cached.json()["cached"] is True and len(env.prompts) == 1
    # a cached answer spent nothing, so it gives the throttle back
    assert "/news/{news_id}/article-summary" not in api._LAST_CALL
    got = c.get("/news/1/article-summary", headers=_headers())
    assert got.json()["summary"] == "English summary." and got.json()["cached"] is True
    assert c.get("/news/1/article-summary", headers=_headers("it")).json() == {"status": "none"}
    assert c.post("/news/999/article-summary", headers=_headers()).status_code == 404


def test_endpoint_not_configured(client, monkeypatch):
    c, _api = client
    def missing(funzione, *a):
        raise lc.ConfigurazioneLLMMancante("NEWS_SUMMARY_MODEL")
    monkeypatch.setattr(lc, "modello", missing)
    r = c.post("/news/1/article-summary", headers=_headers())
    assert r.status_code == 200 and r.json() == {"status": "not_configured", "variable": "NEWS_SUMMARY_MODEL"}
