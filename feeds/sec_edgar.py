"""SEC EDGAR feed poller -- polls EDGAR EFTS for material filings."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

import aiohttp

from utils.utils import RateLimiter

logger = logging.getLogger(__name__)

# EDGAR full-text search API
EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
# EDGAR submissions endpoint for company filings
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
# EDGAR full-text search (public, no auth)
EFTS_SEARCH = "https://efts.sec.gov/LATEST/search-index"
# Simpler search endpoint
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"

# Filing types we care about
MATERIAL_FORMS = {
    "8-K",       # Current events (earnings, leadership changes, M&A)
    "8-K/A",     # Amended 8-K
    "SC 13D",    # Activist investor accumulation (>5% stake)
    "SC 13D/A",  # Amended SC 13D
    "4",         # Insider trades (officer/director buys/sells)
    "10-K/A",    # Amended annual report (restated financials)
    "10-Q/A",    # Amended quarterly report
}

# Rate limit: SEC asks for max 10 requests/second
_rate_limiter = RateLimiter(max_calls=8, period_seconds=1.0)


@dataclass
class EdgarFiling:
    form_type: str  # "8-K", "4", "SC 13D"
    company: str
    ticker: str
    filed_date: str
    description: str
    url: str


class SecEdgarFeed:
    """Polls EDGAR EFTS full-text search for material filings (8-K, Form 4, SC 13D)."""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }

    async def poll(self, lookback_hours: int = 24) -> List[EdgarFiling]:
        """Poll EDGAR for recent material filings."""
        if not self.user_agent or "example.com" in self.user_agent:
            logger.warning(
                "SEC_USER_AGENT not properly configured (must include real email). "
                "Skipping EDGAR poll."
            )
            return []

        filings = []

        # Poll each material form type via the EFTS search API
        for form_type in MATERIAL_FORMS:
            try:
                batch = await self._search_filings(form_type, lookback_hours)
                filings.extend(batch)
            except Exception as e:
                logger.error(f"EDGAR search failed for {form_type}: {e}")

        logger.info(f"EDGAR: {len(filings)} material filings in last {lookback_hours}h")
        return filings

    async def _search_filings(
        self, form_type: str, lookback_hours: int
    ) -> List[EdgarFiling]:
        """Search EDGAR EFTS for a specific form type within the lookback window."""
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(hours=lookback_hours)

        # EDGAR EFTS full-text search API
        params = {
            "q": "*",
            "dateRange": "custom",
            "startdt": start_date.strftime("%Y-%m-%d"),
            "enddt": end_date.strftime("%Y-%m-%d"),
            "forms": form_type,
            "from": 0,
            "size": 50,
        }

        await _rate_limiter.acquire()

        results = []
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                url = "https://efts.sec.gov/LATEST/search-index"
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        hits = data.get("hits", {}).get("hits", [])
                        for hit in hits:
                            filing = self._parse_efts_hit(hit, form_type)
                            if filing:
                                results.append(filing)
                    elif resp.status == 404 or resp.status == 400:
                        # Fall back to the submissions-based approach
                        pass
                    else:
                        body = await resp.text()
                        logger.warning(
                            f"EDGAR EFTS returned {resp.status} for {form_type}: "
                            f"{body[:200]}"
                        )
        except aiohttp.ClientError as e:
            logger.warning(f"EDGAR EFTS request failed for {form_type}: {e}")

        # If EFTS returned nothing, try the full-text search endpoint
        if not results:
            results = await self._search_fulltext(form_type, lookback_hours)

        return results

    async def _search_fulltext(
        self, form_type: str, lookback_hours: int
    ) -> List[EdgarFiling]:
        """Fallback: use EDGAR full-text search API at /LATEST/search-index."""
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(hours=lookback_hours)

        params = {
            "q": f'formType:"{form_type}"',
            "dateRange": "custom",
            "startdt": start_date.strftime("%Y-%m-%d"),
            "enddt": end_date.strftime("%Y-%m-%d"),
            "from": 0,
            "size": 40,
        }

        await _rate_limiter.acquire()

        results = []
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                url = "https://efts.sec.gov/LATEST/search-index"
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    hits = data.get("hits", {}).get("hits", [])
                    for hit in hits:
                        filing = self._parse_efts_hit(hit, form_type)
                        if filing:
                            results.append(filing)
        except aiohttp.ClientError as e:
            logger.warning(f"EDGAR fulltext search failed for {form_type}: {e}")

        return results

    def _parse_efts_hit(self, hit: dict, default_form: str) -> Optional[EdgarFiling]:
        """Parse an EFTS search hit into an EdgarFiling."""
        source = hit.get("_source", {})
        if not source:
            return None

        # Extract fields from EFTS response
        company = source.get("display_names", [""])[0] if source.get("display_names") else source.get("entity_name", "")
        if not company:
            company = source.get("display_name", "Unknown")

        form_type = source.get("form_type", default_form)
        filed_date = source.get("file_date", source.get("period_of_report", ""))
        file_num = source.get("file_num", "")
        accession = source.get("_id", source.get("accession_no", ""))

        # Build EDGAR filing URL
        if accession:
            accession_clean = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{accession_clean}"
        else:
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type={form_type}"

        # Build description based on form type
        description = self._build_description(form_type, source)

        # Extract ticker from EDGAR data (tickers field)
        ticker = ""
        tickers = source.get("tickers", "")
        if tickers:
            if isinstance(tickers, list):
                ticker = tickers[0] if tickers else ""
            elif isinstance(tickers, str):
                ticker = tickers.split(",")[0].strip()

        return EdgarFiling(
            form_type=form_type,
            company=company,
            ticker=ticker,
            filed_date=filed_date,
            description=description,
            url=url,
        )

    def _build_description(self, form_type: str, source: dict) -> str:
        """Build a human-readable description based on form type."""
        display = source.get("display_description", "")
        if display:
            return display[:500]

        if form_type in ("8-K", "8-K/A"):
            items = source.get("items", "")
            return f"Current report (8-K). Items: {items}" if items else "Current report (8-K)"
        elif form_type == "4":
            return "Insider transaction (Form 4)"
        elif form_type in ("SC 13D", "SC 13D/A"):
            return "Activist investor filing (>5% stake)"
        elif form_type in ("10-K/A",):
            return "Amended annual report"
        elif form_type in ("10-Q/A",):
            return "Amended quarterly report"
        return f"Filing: {form_type}"
