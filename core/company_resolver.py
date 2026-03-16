"""Maps company names to tickers and fetches financial context."""

import json
import logging
import os
from typing import Optional
from dataclasses import dataclass

import aiohttp
from thefuzz import fuzz

logger = logging.getLogger(__name__)


@dataclass
class CompanyInfo:
    ticker: str
    name: str
    sector: str
    market_cap: float
    revenue: float
    match_confidence: int  # 0-100, how confident the name match is


class CompanyResolver:
    def __init__(self, data_dir: str, alpaca_headers: dict):
        self.data_dir = data_dir
        self.alpaca_headers = alpaca_headers
        self.map_path = os.path.join(data_dir, "company_map.json")
        self._map: dict[str, str] = {}
        self._financials_cache: dict[str, Optional[CompanyInfo]] = {}
        self._load_map()

    def _load_map(self):
        if os.path.exists(self.map_path):
            with open(self.map_path, "r") as f:
                self._map = json.load(f)
            logger.info(f"Loaded {len(self._map)} company->ticker mappings")
        else:
            # Seed with known major government contractors
            self._map = {
                "LOCKHEED MARTIN": "LMT",
                "LOCKHEED MARTIN CORPORATION": "LMT",
                "RAYTHEON": "RTX",
                "RAYTHEON TECHNOLOGIES": "RTX",
                "RTX CORPORATION": "RTX",
                "NORTHROP GRUMMAN": "NOC",
                "NORTHROP GRUMMAN CORPORATION": "NOC",
                "GENERAL DYNAMICS": "GD",
                "GENERAL DYNAMICS CORPORATION": "GD",
                "BOEING": "BA",
                "THE BOEING COMPANY": "BA",
                "BOOZ ALLEN HAMILTON": "BAH",
                "BOOZ ALLEN HAMILTON INC": "BAH",
                "LEIDOS": "LDOS",
                "LEIDOS INC": "LDOS",
                "LEIDOS HOLDINGS": "LDOS",
                "PALANTIR": "PLTR",
                "PALANTIR TECHNOLOGIES": "PLTR",
                "L3HARRIS": "LHX",
                "L3HARRIS TECHNOLOGIES": "LHX",
                "GENERAL ELECTRIC": "GE",
                "HUNTINGTON INGALLS": "HII",
                "HUNTINGTON INGALLS INDUSTRIES": "HII",
                "SAIC": "SAIC",
                "SCIENCE APPLICATIONS INTERNATIONAL": "SAIC",
                "CACI INTERNATIONAL": "CACI",
                "TEXTRON": "TXT",
                "BAE SYSTEMS": "BAESY",
                "MAXIMUS": "MMS",
                "MAXIMUS INC": "MMS",
                "PERATON": None,  # private, skip
                "DELOITTE": None,  # private
                "ACCENTURE": "ACN",
                "ACCENTURE FEDERAL SERVICES": "ACN",
                "IBM": "IBM",
                "INTERNATIONAL BUSINESS MACHINES": "IBM",
                "MICROSOFT": "MSFT",
                "MICROSOFT CORPORATION": "MSFT",
                "AMAZON": "AMZN",
                "AMAZON WEB SERVICES": "AMZN",
                "GOOGLE": "GOOGL",
                "ALPHABET": "GOOGL",
                "ORACLE": "ORCL",
                "ORACLE CORPORATION": "ORCL",
                "PFIZER": "PFE",
                "MODERNA": "MRNA",
                "JOHNSON & JOHNSON": "JNJ",
                "UNITEDHEALTH": "UNH",
                "UNITEDHEALTH GROUP": "UNH",
                "HUMANA": "HUM",
                "CENTENE": "CNC",
                "FLUOR": "FLR",
                "FLUOR CORPORATION": "FLR",
                "JACOBS": "J",
                "JACOBS ENGINEERING": "J",
                "KBR": "KBR",
                "KBR INC": "KBR",
                "AECOM": "ACM",
            }
            self._save_map()

    def _save_map(self):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.map_path, "w") as f:
            json.dump(self._map, f, indent=2)

    def resolve(self, company_name: str) -> Optional[str]:
        """Resolve a company name to a ticker symbol using fuzzy matching."""
        name_upper = company_name.upper().strip()

        # Exact match
        if name_upper in self._map:
            return self._map[name_upper]

        # Fuzzy match against known names
        best_score = 0
        best_ticker = None
        for known_name, ticker in self._map.items():
            if ticker is None:
                continue
            score = fuzz.ratio(name_upper, known_name)
            if score > best_score:
                best_score = score
                best_ticker = ticker

        if best_score >= 80:
            # Cache the new name variant
            self._map[name_upper] = best_ticker
            self._save_map()
            logger.info(f"Fuzzy matched '{company_name}' -> {best_ticker} (score: {best_score})")
            return best_ticker

        logger.warning(f"Could not resolve company: '{company_name}' (best score: {best_score})")
        return None

    def add_mapping(self, company_name: str, ticker: str):
        """Manually add or update a company->ticker mapping."""
        self._map[company_name.upper().strip()] = ticker
        self._save_map()

    async def get_financials(self, ticker: str) -> Optional[CompanyInfo]:
        """Fetch basic financial info for a ticker using Yahoo Finance v8 API."""
        # Check cache first
        if ticker in self._financials_cache:
            return self._financials_cache[ticker]

        info = await self._fetch_yahoo_financials(ticker)

        # Fall back to Alpaca if Yahoo fails
        if info is None:
            info = await self._fetch_alpaca_financials(ticker)

        # Cache result (even None to avoid repeated failures)
        self._financials_cache[ticker] = info
        return info

    async def _fetch_yahoo_financials(self, ticker: str) -> Optional[CompanyInfo]:
        """Fetch market cap, sector, and name from Yahoo Finance v8 quote API."""
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + ticker
        params = {
            "interval": "1d",
            "range": "1d",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        }

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.debug(f"Yahoo Finance chart API returned {resp.status} for {ticker}")
                        return await self._fetch_yahoo_quoteSummary(ticker)
                    data = await resp.json()

            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            market_cap = meta.get("marketCap", 0)
            name = meta.get("longName", meta.get("shortName", ticker))

            if market_cap:
                return CompanyInfo(
                    ticker=ticker,
                    name=name,
                    sector="",
                    market_cap=float(market_cap),
                    revenue=0,
                    match_confidence=100,
                )

            # If chart API doesn't have marketCap, try quoteSummary
            return await self._fetch_yahoo_quoteSummary(ticker)

        except Exception as e:
            logger.debug(f"Yahoo Finance chart API failed for {ticker}: {e}")
            return await self._fetch_yahoo_quoteSummary(ticker)

    async def _fetch_yahoo_quoteSummary(self, ticker: str) -> Optional[CompanyInfo]:
        """Fallback: fetch from Yahoo Finance quoteSummary endpoint."""
        url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
        params = {
            "modules": "summaryProfile,financialData,price",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        }

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            result = data.get("quoteSummary", {}).get("result", [])
            if not result:
                return None

            modules = result[0]

            # Extract from price module
            price_data = modules.get("price", {})
            market_cap_raw = price_data.get("marketCap", {}).get("raw", 0)
            name = price_data.get("longName", price_data.get("shortName", ticker))

            # Extract from summaryProfile
            profile = modules.get("summaryProfile", {})
            sector = profile.get("sector", "")

            # Extract revenue from financialData
            fin_data = modules.get("financialData", {})
            revenue = fin_data.get("totalRevenue", {}).get("raw", 0)

            return CompanyInfo(
                ticker=ticker,
                name=name,
                sector=sector,
                market_cap=float(market_cap_raw),
                revenue=float(revenue),
                match_confidence=100,
            )
        except Exception as e:
            logger.debug(f"Yahoo Finance quoteSummary failed for {ticker}: {e}")
            return None

    async def _fetch_alpaca_financials(self, ticker: str) -> Optional[CompanyInfo]:
        """Fallback: use Alpaca snapshot for basic info (no market cap)."""
        try:
            async with aiohttp.ClientSession(headers=self.alpaca_headers) as session:
                async with session.get(
                    f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot"
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            # Alpaca doesn't provide market cap in snapshots, so estimate from price * shares
            # We can't get shares outstanding from Alpaca, so market_cap stays 0
            return CompanyInfo(
                ticker=ticker,
                name=ticker,
                sector="",
                market_cap=0,
                revenue=0,
                match_confidence=100,
            )
        except Exception as e:
            logger.error(f"Failed to fetch financials for {ticker}: {e}")
            return None

    async def passes_market_cap_filter(self, ticker: str, min_cap: int, max_cap: int = 0) -> bool:
        """Check if a ticker passes the market cap filter.

        Args:
            ticker: Stock ticker symbol
            min_cap: Minimum market cap in dollars. 0 = no minimum.
            max_cap: Maximum market cap in dollars. 0 = no maximum.

        Returns:
            True if the ticker passes the filter. False if data is unavailable (fail closed).
        """
        if min_cap == 0 and max_cap == 0:
            return True

        info = await self.get_financials(ticker)
        if info is None or info.market_cap == 0:
            logger.warning(f"No market cap data for {ticker}, rejecting (fail closed)")
            return False

        if min_cap > 0 and info.market_cap < min_cap:
            logger.info(
                f"{ticker} market cap ${info.market_cap / 1e9:.1f}B below "
                f"minimum ${min_cap / 1e9:.1f}B"
            )
            return False

        if max_cap > 0 and info.market_cap > max_cap:
            logger.info(
                f"{ticker} market cap ${info.market_cap / 1e9:.1f}B above "
                f"maximum ${max_cap / 1e9:.1f}B"
            )
            return False

        return True
