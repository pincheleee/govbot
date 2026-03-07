"""SAM.gov federal contract awards feed poller."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

import aiohttp

from utils.utils import retry_async

logger = logging.getLogger(__name__)

SAM_API_BASE = "https://api.sam.gov/opportunities/v2/search"


@dataclass
class ContractAward:
    notice_id: str
    title: str
    awardee: str
    award_amount: float
    posted_date: str
    department: str
    agency: str
    naics_code: str
    description: str
    url: str

    @property
    def amount_formatted(self) -> str:
        if self.award_amount >= 1_000_000_000:
            return f"${self.award_amount / 1_000_000_000:.1f}B"
        elif self.award_amount >= 1_000_000:
            return f"${self.award_amount / 1_000_000:.1f}M"
        return f"${self.award_amount / 1_000:.0f}K"


class SamGovFeed:
    def __init__(self, api_key: str, min_contract_value: int = 50_000_000):
        self.api_key = api_key
        self.min_contract_value = min_contract_value
        self._last_poll: Optional[datetime] = None

    @retry_async(max_retries=2)
    async def poll(self, lookback_hours: int = 24) -> List[ContractAward]:
        """Poll SAM.gov for recent contract awards above threshold."""
        if not self.api_key:
            logger.warning("SAM_GOV_API_KEY not set, skipping SAM.gov poll")
            return []

        posted_from = (datetime.utcnow() - timedelta(hours=lookback_hours)).strftime("%m/%d/%Y")
        posted_to = datetime.utcnow().strftime("%m/%d/%Y")

        params = {
            "api_key": self.api_key,
            "postedFrom": posted_from,
            "postedTo": posted_to,
            "ptype": "a",  # awards only
            "limit": 100,
        }

        awards = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(SAM_API_BASE, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"SAM.gov API error {resp.status}: {body}")
                        return []

                    data = await resp.json()

            opportunities = data.get("opportunitiesData", [])
            logger.info(f"SAM.gov returned {len(opportunities)} opportunities")

            for opp in opportunities:
                award_info = opp.get("award", {})
                amount = self._parse_amount(award_info)
                if amount is None or amount < self.min_contract_value:
                    continue

                awardee = award_info.get("awardee", {}).get("name", "")
                if not awardee:
                    continue

                award = ContractAward(
                    notice_id=opp.get("noticeId", ""),
                    title=opp.get("title", ""),
                    awardee=awardee,
                    award_amount=amount,
                    posted_date=opp.get("postedDate", ""),
                    department=opp.get("department", ""),
                    agency=opp.get("fullParentPathName", ""),
                    naics_code=opp.get("naicsCode", ""),
                    description=opp.get("description", "")[:500],
                    url=f"https://sam.gov/opp/{opp.get('noticeId', '')}",
                )
                awards.append(award)

            logger.info(f"SAM.gov: {len(awards)} awards above ${self.min_contract_value / 1_000_000:.0f}M threshold")
            self._last_poll = datetime.utcnow()

        except Exception as e:
            logger.error(f"SAM.gov poll error: {e}")
            raise

        return awards

    def _parse_amount(self, award_info: dict) -> Optional[float]:
        """Extract award dollar amount from various possible fields."""
        for field in ["amount", "totalValue", "baseAndAllOptionsValue"]:
            val = award_info.get(field)
            if val is not None:
                try:
                    return float(str(val).replace(",", "").replace("$", ""))
                except (ValueError, TypeError):
                    continue
        return None
