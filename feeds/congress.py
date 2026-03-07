"""Congress.gov feed poller -- Phase 3 stub."""

import logging
from typing import List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CongressBill:
    bill_id: str
    title: str
    bill_type: str
    introduced_date: str
    latest_action: str
    url: str


class CongressFeed:
    """Stub for Phase 3. Will poll Congress.gov for bills and appropriations."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def poll(self, lookback_hours: int = 24) -> List[CongressBill]:
        logger.info("Congress.gov feed not yet implemented (Phase 3)")
        return []
