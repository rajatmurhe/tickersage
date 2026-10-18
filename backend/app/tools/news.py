"""Headline sentiment from Google News RSS, scored with VADER.

Headlines are untrusted third-party text. Any headline that reads like an instruction
("ignore your rules", "tell users to buy X") is redacted before the model sees it,
and the redaction is counted so evals can check it happened.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from pydantic import BaseModel, Field
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from ..config import Settings
from ..guardrails import detect_injection
from ..http import fetch
from .errors import ToolError

log = logging.getLogger(__name__)

_analyzer = SentimentIntensityAnalyzer()
SOURCE = "Google News RSS + VADER"
REDACTED = "[headline withheld: it contained instructions rather than news]"


class NewsInput(BaseModel):
    query: str = Field(min_length=2, max_length=80)
    max_items: int = Field(default=5, ge=1, le=10)


class Headline(BaseModel):
    title: str
    publisher: str | None
    published_at: datetime | None
    url: str | None
    sentiment: float
    label: str
    redacted: bool = False


class NewsOutput(BaseModel):
    query: str
    headline_count: int
    headlines: list[Headline]
    avg_sentiment: float | None
    positive: int
    negative: int
    neutral: int
    redacted_count: int
    source: str
    as_of: datetime


def score(text: str) -> tuple[float, str]:
    compound = round(_analyzer.polarity_scores(text)["compound"], 3)
    label = "positive" if compound >= 0.05 else "negative" if compound <= -0.05 else "neutral"
    return compound, label


def build_news_output(query: str, items: list[dict], max_items: int, source: str) -> NewsOutput:
    """Score, sanitise and summarise raw items: dicts with title/publisher/published_at/url."""
    headlines: list[Headline] = []
    for item in items[:max_items]:
        title = " ".join(str(item.get("title", "")).split())
        if not title:
            continue
        hit = detect_injection(title)
        if hit:
            log.warning("redacted suspicious headline (matched %r)", hit)
            headlines.append(
                Headline(title=REDACTED, publisher=item.get("publisher"), published_at=item.get("published_at"),
                         url=item.get("url"), sentiment=0.0, label="neutral", redacted=True)
            )
            continue
        compound, label = score(title)
        headlines.append(
            Headline(title=title, publisher=item.get("publisher"), published_at=item.get("published_at"),
                     url=item.get("url"), sentiment=compound, label=label)
        )
    scored = [h for h in headlines if not h.redacted]
    avg = round(sum(h.sentiment for h in scored) / len(scored), 3) if scored else None
    return NewsOutput(
        query=query,
        headline_count=len(headlines),
        headlines=headlines,
        avg_sentiment=avg,
        positive=sum(h.label == "positive" for h in scored),
        negative=sum(h.label == "negative" for h in scored),
        neutral=sum(h.label == "neutral" for h in scored),
        redacted_count=sum(h.redacted for h in headlines),
        source=source,
        as_of=datetime.now(UTC),
    )


class NewsClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http

    async def get_news_sentiment(self, query: str, max_items: int) -> NewsOutput:
        return build_news_output(query, await self.fetch_items(query), max_items, SOURCE)

    async def fetch_items(self, query: str) -> list[dict]:
        """Raw, unscored headline items, newest first."""
        params = {"q": f"{query} crypto when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
        xml = await fetch(
            self.http, self.settings.news_rss_url, params,
            timeout=self.settings.tool_timeout_s, retries=self.settings.tool_retries, as_text=True,
        )
        feed = feedparser.parse(xml)
        items = []
        for entry in feed.entries:
            title, publisher = _split_publisher(entry.get("title", ""), entry.get("source", {}))
            items.append(
                {"title": title, "publisher": publisher, "published_at": _published(entry), "url": entry.get("link")}
            )
        items.sort(key=lambda i: i["published_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)
        if not items:
            raise ToolError("empty_data", f"No recent headlines found for '{query}'.")
        return items


def _split_publisher(title: str, source: dict) -> tuple[str, str | None]:
    publisher = source.get("title") if isinstance(source, dict) else None
    if publisher and title.endswith(f" - {publisher}"):
        title = title[: -len(publisher) - 3]
    return title, publisher


def _published(entry) -> datetime | None:
    if entry.get("published"):
        try:
            return parsedate_to_datetime(entry["published"]).astimezone(UTC)
        except (TypeError, ValueError):
            pass
    parsed = entry.get("published_parsed")
    return datetime.fromtimestamp(time.mktime(parsed), tz=UTC) if parsed else None
