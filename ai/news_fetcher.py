"""Financial news fetcher for signal enrichment via free RSS feeds."""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

import aiohttp

from utils.utils import retry_async

logger = logging.getLogger(__name__)

# Free RSS feeds for financial/government contract news
RSS_FEEDS = {
    "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
    "reuters_business": "https://www.rss.reuters.com/news/businessNews",
    "google_news_business": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB",
    "defense_news": "https://www.defensenews.com/arc/outboundfeeds/rss/",
    "federal_news_network": "https://federalnewsnetwork.com/feed/",
}

# Keywords to filter for relevance
RELEVANCE_KEYWORDS = [
    "government contract", "defense contract", "pentagon", "military",
    "federal", "procurement", "defense spending", "appropriation",
    "regulation", "sec", "fda", "epa", "fcc",
    "executive order", "tariff", "sanction",
    "earnings", "quarterly results", "revenue",
    "merger", "acquisition", "takeover",
    "insider", "buyback", "stock repurchase",
]


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    url: str
    published: str
    relevance_score: float  # 0.0-1.0


class NewsFetcher:
    """Fetches recent financial news from free RSS feeds to enrich AI analysis."""

    def __init__(self):
        self._cache: dict[str, List[NewsItem]] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=10)

    @retry_async(max_retries=1)
    async def fetch(self, ticker: str, hours: int = 48) -> List[dict]:
        """Fetch recent news relevant to a ticker and government/defense topics."""
        # Check cache freshness
        if self._cache_time and datetime.utcnow() - self._cache_time < self._cache_ttl:
            cached = self._cache.get(ticker, [])
            if cached:
                return [self._item_to_dict(item) for item in cached]

        all_items: List[NewsItem] = []

        # Fetch from all RSS feeds in parallel
        for source_name, feed_url in RSS_FEEDS.items():
            try:
                items = await self._fetch_rss(source_name, feed_url)
                all_items.extend(items)
            except Exception as e:
                logger.debug(f"RSS feed {source_name} failed: {e}")

        # Also fetch Google News for the specific ticker
        try:
            ticker_items = await self._fetch_ticker_news(ticker)
            all_items.extend(ticker_items)
        except Exception as e:
            logger.debug(f"Ticker news fetch failed for {ticker}: {e}")

        # Score and filter by relevance to ticker and gov/defense topics
        scored = self._score_items(all_items, ticker)
        scored.sort(key=lambda x: x.relevance_score, reverse=True)

        # Keep top relevant items
        relevant = [item for item in scored if item.relevance_score > 0.1][:15]

        # Cache results
        self._cache[ticker] = relevant
        self._cache_time = datetime.utcnow()

        logger.info(f"News: {len(relevant)} relevant items for {ticker} from {len(all_items)} total")

        return [self._item_to_dict(item) for item in relevant]

    async def _fetch_rss(self, source_name: str, url: str) -> List[NewsItem]:
        """Fetch and parse an RSS feed."""
        items = []

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []

        # Handle both RSS 2.0 and Atom
        # RSS 2.0: channel/item
        for item in root.findall(".//item"):
            title = self._get_text(item, "title")
            summary = self._get_text(item, "description")
            link = self._get_text(item, "link")
            pub_date = self._get_text(item, "pubDate")

            if title:
                # Strip HTML from summary
                summary = re.sub(r"<[^>]+>", "", summary)[:500]

                items.append(NewsItem(
                    title=title,
                    summary=summary,
                    source=source_name,
                    url=link,
                    published=pub_date,
                    relevance_score=0.0,
                ))

        # Atom: entry
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = self._get_text(entry, "atom:title", ns)
            summary = self._get_text(entry, "atom:summary", ns)
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            pub_date = self._get_text(entry, "atom:published", ns) or self._get_text(entry, "atom:updated", ns)

            if title:
                summary = re.sub(r"<[^>]+>", "", summary)[:500]
                items.append(NewsItem(
                    title=title,
                    summary=summary,
                    source=source_name,
                    url=link,
                    published=pub_date,
                    relevance_score=0.0,
                ))

        return items

    async def _fetch_ticker_news(self, ticker: str) -> List[NewsItem]:
        """Fetch news for a specific ticker via Google News RSS."""
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"

        items = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return []
                    text = await resp.text()

            root = ET.fromstring(text)
            for item in root.findall(".//item"):
                title = self._get_text(item, "title")
                link = self._get_text(item, "link")
                pub_date = self._get_text(item, "pubDate")
                source = self._get_text(item, "source")

                if title:
                    items.append(NewsItem(
                        title=title,
                        summary="",
                        source=f"google_news_{source}" if source else "google_news",
                        url=link,
                        published=pub_date,
                        relevance_score=0.0,
                    ))
        except Exception as e:
            logger.debug(f"Google News ticker search failed for {ticker}: {e}")

        return items

    def _score_items(self, items: List[NewsItem], ticker: str) -> List[NewsItem]:
        """Score each news item for relevance to the ticker and gov/defense topics."""
        ticker_lower = ticker.lower()

        for item in items:
            text = (item.title + " " + item.summary).lower()
            score = 0.0

            # Direct ticker mention
            if ticker_lower in text or f"${ticker_lower}" in text:
                score += 0.5

            # Relevance keyword matches
            keyword_hits = sum(1 for kw in RELEVANCE_KEYWORDS if kw in text)
            score += min(keyword_hits * 0.1, 0.4)

            # Recency bonus (if parseable)
            # Just check if it has a date at all
            if item.published:
                score += 0.1

            item.relevance_score = min(score, 1.0)

        return items

    def _get_text(self, element, tag: str, ns: dict = None) -> str:
        """Safely get text content from an XML element."""
        if ns:
            el = element.find(tag, ns)
        else:
            el = element.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    def _item_to_dict(self, item: NewsItem) -> dict:
        """Convert a NewsItem to a dict for the AI analyzer."""
        return {
            "title": item.title,
            "summary": item.summary,
            "source": item.source,
            "url": item.url,
            "published": item.published,
            "relevance_score": item.relevance_score,
        }
