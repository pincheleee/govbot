"""SEC EDGAR feed poller -- Phase 2 stub."""

import logging
from typing import List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EdgarFiling:
    form_type: str  # "8-K", "4", "SC 13D"
    company: str
    ticker: str
    filed_date: str
    description: str
    url: str


class SecEdgarFeed:
    """Stub for Phase 2. Will poll EDGAR for 8-K, Form 4, and SC 13D filings."""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    async def poll(self, lookback_hours: int = 24) -> List[EdgarFiling]:
        logger.info("SEC EDGAR feed not yet implemented (Phase 2)")
        return []
