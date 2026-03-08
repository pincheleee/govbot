"""Signal scanner: polls all data feeds and produces unified signals."""

import logging
from dataclasses import dataclass
from typing import List, Optional

from feeds.sam_gov import SamGovFeed, ContractAward
from feeds.sec_edgar import SecEdgarFeed, EdgarFiling
from feeds.federal_register import FederalRegisterFeed, FedRegDocument
from feeds.congress import CongressFeed, CongressBill

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A unified signal from any data feed."""
    source: str  # "SAM_CONTRACT" | "SEC_8K" | "SEC_FORM4" | "SEC_13D" | "FED_REGISTER" | "CONGRESS"
    company_name: str
    ticker: Optional[str]  # None if not yet resolved
    title: str
    details: str
    dollar_value: Optional[float]
    url: str
    raw_data: dict  # Full original data for AI analysis


# Map EDGAR form types to signal source labels
EDGAR_SOURCE_MAP = {
    "8-K": "SEC_8K",
    "8-K/A": "SEC_8K",
    "4": "SEC_FORM4",
    "SC 13D": "SEC_13D",
    "SC 13D/A": "SEC_13D",
    "10-K/A": "SEC_AMENDMENT",
    "10-Q/A": "SEC_AMENDMENT",
}


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

        # SAM.gov contract awards
        sam_signals = await self._scan_sam_gov()
        signals.extend(sam_signals)

        # SEC EDGAR filings
        edgar_signals = await self._scan_edgar()
        signals.extend(edgar_signals)

        # Federal Register rules and executive orders
        fed_signals = await self._scan_fed_register()
        signals.extend(fed_signals)

        # Congress.gov bills
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
        """Scan SEC EDGAR for material filings and convert to signals."""
        try:
            filings = await self.edgar_feed.poll()
        except Exception as e:
            logger.error(f"SEC EDGAR scan failed: {e}")
            return []

        signals = []
        for filing in filings:
            # Need a company name for resolution
            company_name = filing.company
            if not company_name or company_name == "Unknown":
                continue

            source = EDGAR_SOURCE_MAP.get(filing.form_type, "SEC_FILING")

            signal = Signal(
                source=source,
                company_name=company_name,
                ticker=filing.ticker if filing.ticker else None,
                title=f"{filing.form_type}: {company_name}",
                details=(
                    f"Form: {filing.form_type}\n"
                    f"Company: {company_name}\n"
                    f"Filed: {filing.filed_date}\n"
                    f"Description: {filing.description[:300]}"
                ),
                dollar_value=None,
                url=filing.url,
                raw_data={
                    "form_type": filing.form_type,
                    "company": company_name,
                    "ticker": filing.ticker,
                    "filed_date": filing.filed_date,
                    "description": filing.description,
                },
            )
            signals.append(signal)

        return signals

    async def _scan_fed_register(self) -> List[Signal]:
        """Scan Federal Register for economically significant documents."""
        try:
            docs = await self.fed_register_feed.poll()
        except Exception as e:
            logger.error(f"Federal Register scan failed: {e}")
            return []

        signals = []
        for doc in docs:
            # Federal Register docs don't map to a single company -- use agency/sector
            # The company_name here is the affected sector, which company_resolver won't match.
            # These signals are more macro: the AI analyzer needs sector context.
            agencies_str = ", ".join(doc.agencies) if doc.agencies else "Unknown"

            signal = Signal(
                source="FED_REGISTER",
                company_name=agencies_str,  # Will likely need manual sector->company mapping
                ticker=None,
                title=f"Federal Register: {doc.title[:200]}",
                details=(
                    f"Type: {doc.document_type}\n"
                    f"Agencies: {agencies_str}\n"
                    f"Sector: {doc.affected_sector}\n"
                    f"Published: {doc.publication_date}\n"
                    f"Abstract: {doc.abstract[:300]}"
                ),
                dollar_value=None,
                url=doc.url,
                raw_data={
                    "document_type": doc.document_type,
                    "title": doc.title,
                    "abstract": doc.abstract,
                    "agencies": doc.agencies,
                    "publication_date": doc.publication_date,
                    "significant": doc.significant,
                    "affected_sector": doc.affected_sector,
                },
            )
            signals.append(signal)

        return signals

    async def _scan_congress(self) -> List[Signal]:
        """Scan Congress.gov for market-relevant bills with recent action."""
        try:
            bills = await self.congress_feed.poll()
        except Exception as e:
            logger.error(f"Congress.gov scan failed: {e}")
            return []

        signals = []
        for bill in bills:
            signal = Signal(
                source="CONGRESS",
                company_name=bill.sector,  # Sector name, not company -- similar to FedReg
                ticker=None,
                title=f"Congress: {bill.title[:200]}",
                details=(
                    f"Bill: {bill.bill_id} ({bill.bill_type})\n"
                    f"Sector: {bill.sector}\n"
                    f"Introduced: {bill.introduced_date}\n"
                    f"Latest Action: {bill.latest_action}\n"
                    f"Action Date: {bill.action_date}"
                ),
                dollar_value=None,
                url=bill.url,
                raw_data={
                    "bill_id": bill.bill_id,
                    "title": bill.title,
                    "bill_type": bill.bill_type,
                    "sector": bill.sector,
                    "introduced_date": bill.introduced_date,
                    "latest_action": bill.latest_action,
                    "action_date": bill.action_date,
                },
            )
            signals.append(signal)

        return signals
