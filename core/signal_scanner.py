"""Signal scanner: polls all data feeds and produces unified signals."""

import logging
from dataclasses import dataclass
from typing import List, Optional

from feeds.sam_gov import SamGovFeed, ContractAward
from feeds.sec_edgar import SecEdgarFeed, EdgarFiling
from feeds.federal_register import FederalRegisterFeed, FedRegDocument
from feeds.congress import CongressFeed, CongressBill
from feeds.congress_trading import CongressTradingFeed, CongressTradeSignal

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A unified signal from any data feed."""
    source: str  # "SAM_CONTRACT" | "SEC_8K" | "SEC_FORM4" | "SEC_13D" | "FED_REGISTER" | "CONGRESS" | "CONGRESS_TRADE_BUY" | "CONGRESS_TRADE_CLUSTER" | "CONGRESS_TRADE_COMMITTEE"
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
        self.congress_trading_feed = CongressTradingFeed(
            api_token=config.quiverquant_api_token,
            min_amount=config.congress_trade_min_amount,
        )

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

        # QuiverQuant Congress member stock trades
        congress_trade_signals = await self._scan_congress_trading()
        signals.extend(congress_trade_signals)

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

    async def _scan_congress_trading(self) -> List[Signal]:
        """Scan QuiverQuant for Congress member stock trades."""
        try:
            trade_signals = await self.congress_trading_feed.poll()
        except Exception as e:
            logger.error(f"Congress trading scan failed: {e}")
            return []

        signals = []
        for ts in trade_signals:
            if ts.cluster:
                # Cluster signal -- multiple members buying same stock
                cluster = ts.cluster
                members = [t.representative for t in cluster.trades]
                parties = [t.party for t in cluster.trades]
                member_list = ", ".join(
                    f"{name} ({party})" for name, party in zip(members, parties)
                )
                bipartisan_tag = " [BIPARTISAN]" if cluster.bipartisan else ""

                signal = Signal(
                    source=ts.signal_type,
                    company_name=cluster.ticker,  # Already a ticker
                    ticker=cluster.ticker,
                    title=(
                        f"Congress Cluster Buy: {cluster.unique_members} members buying "
                        f"{cluster.ticker}{bipartisan_tag}"
                    ),
                    details=(
                        f"Ticker: {cluster.ticker}\n"
                        f"Members ({cluster.unique_members}): {member_list}\n"
                        f"Combined amount: ${cluster.total_amount_low:,.0f} - ${cluster.total_amount_high:,.0f}\n"
                        f"Window: {cluster.window_days} days\n"
                        f"Bipartisan: {'Yes' if cluster.bipartisan else 'No'}"
                        + (f"\nCommittee relevance: {ts.committee_relevance} ({ts.sector_match})" if ts.committee_relevance else "")
                    ),
                    dollar_value=(cluster.total_amount_low + cluster.total_amount_high) / 2,
                    url="https://www.quiverquant.com/congresstrading/",
                    raw_data={
                        "signal_type": ts.signal_type,
                        "ticker": cluster.ticker,
                        "unique_members": cluster.unique_members,
                        "members": members,
                        "parties": parties,
                        "total_amount_low": cluster.total_amount_low,
                        "total_amount_high": cluster.total_amount_high,
                        "bipartisan": cluster.bipartisan,
                        "committee_relevance": ts.committee_relevance,
                        "sector_match": ts.sector_match,
                        "trades": [
                            {
                                "representative": t.representative,
                                "party": t.party,
                                "chamber": t.chamber,
                                "amount_range": t.amount_range,
                                "transaction_date": t.transaction_date,
                                "report_date": t.report_date,
                                "disclosure_lag_days": t.disclosure_lag_days,
                            }
                            for t in cluster.trades
                        ],
                    },
                )
                signals.append(signal)

            elif ts.trade:
                # Individual trade signal (BUY or COMMITTEE)
                trade = ts.trade
                lag_note = ""
                if trade.disclosure_lag_days > 30:
                    lag_note = f" [LATE DISCLOSURE: {trade.disclosure_lag_days}d lag]"
                elif trade.disclosure_lag_days > 14:
                    lag_note = f" [Disclosure lag: {trade.disclosure_lag_days}d]"

                committee_note = ""
                if ts.committee_relevance:
                    committee_note = (
                        f"\nCommittee relevance: {ts.committee_relevance} "
                        f"committee member trading in {ts.sector_match} sector"
                    )

                signal = Signal(
                    source=ts.signal_type,
                    company_name=trade.ticker,  # Already a ticker
                    ticker=trade.ticker,
                    title=(
                        f"Congress Trade: {trade.representative} ({trade.party}-{trade.chamber}) "
                        f"bought {trade.ticker}{lag_note}"
                    ),
                    details=(
                        f"Ticker: {trade.ticker}\n"
                        f"Representative: {trade.representative}\n"
                        f"Party: {trade.party} | Chamber: {trade.chamber}\n"
                        f"Transaction: {trade.transaction}\n"
                        f"Amount: {trade.amount_range}\n"
                        f"Transaction date: {trade.transaction_date}\n"
                        f"Disclosure date: {trade.report_date}\n"
                        f"Disclosure lag: {trade.disclosure_lag_days} days"
                        + committee_note
                        + (f"\nDescription: {trade.description}" if trade.description else "")
                    ),
                    dollar_value=(trade.amount_low + trade.amount_high) / 2,
                    url="https://www.quiverquant.com/congresstrading/",
                    raw_data={
                        "signal_type": ts.signal_type,
                        "representative": trade.representative,
                        "party": trade.party,
                        "chamber": trade.chamber,
                        "ticker": trade.ticker,
                        "transaction": trade.transaction,
                        "amount_range": trade.amount_range,
                        "amount_low": trade.amount_low,
                        "amount_high": trade.amount_high,
                        "transaction_date": trade.transaction_date,
                        "report_date": trade.report_date,
                        "disclosure_lag_days": trade.disclosure_lag_days,
                        "committee_relevance": ts.committee_relevance,
                        "sector_match": ts.sector_match,
                    },
                )
                signals.append(signal)

        return signals
