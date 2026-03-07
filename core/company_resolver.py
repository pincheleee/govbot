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
        """Fetch basic financial info for a ticker via Alpaca or Yahoo Finance."""
        try:
            # Use Alpaca assets endpoint for basic info
            async with aiohttp.ClientSession(headers=self.alpaca_headers) as session:
                async with session.get(
                    f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot"
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            return CompanyInfo(
                ticker=ticker,
                name=ticker,  # Alpaca doesn't return full name in snapshot
                sector="",
                market_cap=0,  # Will need a separate data source for this
                revenue=0,
                match_confidence=100,
            )
        except Exception as e:
            logger.error(f"Failed to fetch financials for {ticker}: {e}")
            return None
