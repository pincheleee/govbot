"""Signal scanner: polls all data feeds and produces unified signals."""

import logging
from dataclasses import dataclass
from typing import List, Optional

from feeds.sam_gov import SamGovFeed, ContractAward
from feeds.sec_edgar import SecEdgarFeed
from feeds.federal_register import FederalRegisterFeed
from feeds.congress import CongressFeed

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A unified signal from any data feed."""
    source: str  # "SAM_CONTRACT" | "SEC_8K" | "SEC_FORM4" | "FED_REGISTER" | "CONGRESS"
    company_name: str
    ticker: Optional[str]  # None if not yet resolved
    title: str
    details: str
    dollar_value: Optional[float]
    url: str
    raw_data: dict  # Full original data for AI analysis


class SignalScanner:
    def __init__(self, config):
        self.config = config
        self.sam_feed = SamGovFeed(
            api_key=config.sam_gov_api_key,
            min_contract_value=config.min_contract_value,
        )
        self.edgar_feed = SecEdgarFeed(user_agent=config.sec_user_agent)
        self.fed_register_feed = FederalRegisterFeed()
        self.congress_feed = CongressFeed(api_key=config.congress_api_key)

    async def scan_feeds(self) -> List[Signal]:
        """Poll all active feeds and return unified signals."""
        signals = []

        # Phase 1: SAM.gov only
        sam_signals = await self._scan_sam_gov()
        signals.extend(sam_signals)

        # Phase 2 stubs
        edgar_signals = await self._scan_edgar()
        signals.extend(edgar_signals)

        # Phase 3 stubs
        fed_signals = await self._scan_fed_register()
        signals.extend(fed_signals)

        congress_signals = await self._scan_congress()
        signals.extend(congress_signals)

        logger.info(f"Total signals from all feeds: {len(signals)}")
        return signals

    async def _scan_sam_gov(self) -> List[Signal]:
        try:
            awards = await self.sam_feed.poll()
        except Exception as e:
            logger.error(f"SAM.gov scan failed: {e}")
            return []

        signals = []
        for award in awards:
            signal = Signal(
                source="SAM_CONTRACT",
                company_name=award.awardee,
                ticker=None,  # Will be resolved by company_resolver
                title=f"Contract Award: {award.title}",
                details=(
                    f"Awardee: {award.awardee}\n"
                    f"Amount: {award.amount_formatted}\n"
                    f"Agency: {award.department}\n"
                    f"NAICS: {award.naics_code}\n"
                    f"Description: {award.description[:300]}"
                ),
                dollar_value=award.award_amount,
                url=award.url,
                raw_data={
                    "notice_id": award.notice_id,
                    "title": award.title,
                    "awardee": award.awardee,
                    "award_amount": award.award_amount,
                    "department": award.department,
                    "naics_code": award.naics_code,
                    "posted_date": award.posted_date,
                },
            )
            signals.append(signal)

        return signals

    async def _scan_edgar(self) -> List[Signal]:
        filings = await self.edgar_feed.poll()
        return []  # Phase 2

    async def _scan_fed_register(self) -> List[Signal]:
        docs = await self.fed_register_feed.poll()
        return []  # Phase 3

    async def _scan_congress(self) -> List[Signal]:
        bills = await self.congress_feed.poll()
        return []  # Phase 3
