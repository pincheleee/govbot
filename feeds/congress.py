"""Congress.gov feed poller -- polls Congress API for bills with market-moving potential."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

import aiohttp

from utils.utils import retry_async

logger = logging.getLogger(__name__)

CONGRESS_API_BASE = "https://api.congress.gov/v3"

# Bill types to track
BILL_TYPES = ["hr", "s", "hjres", "sjres"]

# Keywords for market-relevant bills
SECTOR_KEYWORDS = {
    "defense": ["defense", "military", "armed forces", "pentagon", "weapons", "missile", "navy", "army", "air force"],
    "healthcare": ["health", "medicare", "medicaid", "pharmaceutical", "drug pricing", "hospital", "insurance"],
    "technology": ["technology", "cybersecurity", "artificial intelligence", "semiconductor", "broadband", "data privacy", "quantum"],
    "energy": ["energy", "oil", "gas", "solar", "wind", "nuclear", "pipeline", "climate", "carbon", "emission"],
    "finance": ["banking", "financial", "securities", "crypto", "digital asset", "fed", "interest rate"],
    "infrastructure": ["infrastructure", "transportation", "highway", "bridge", "rail", "water"],
    "trade": ["tariff", "trade", "import", "export", "sanction", "embargo"],
    "appropriations": ["appropriation", "spending", "budget", "fiscal"],
}

# Actions that indicate a bill is progressing
SIGNIFICANT_ACTIONS = [
    "passed house", "passed senate", "signed by president",
    "became public law", "reported by committee", "ordered reported",
    "placed on calendar", "cloture motion", "floor consideration",
    "conference report", "veto",
]


@dataclass
class CongressBill:
    bill_id: str
    title: str
    bill_type: str
    introduced_date: str
    latest_action: str
    url: str
    sector: str = ""
    action_date: str = ""
    sponsors: str = ""


class CongressFeed:
    """Polls Congress.gov API for bills with recent action in market-relevant sectors."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    @retry_async(max_retries=2)
    async def poll(self, lookback_hours: int = 24) -> List[CongressBill]:
        """Poll Congress API for recently acted-upon bills."""
        if not self.api_key:
            logger.warning("CONGRESS_API_KEY not set, skipping Congress.gov poll")
            return []

        end_date = datetime.utcnow()
        start_date = end_date - timedelta(hours=lookback_hours)

        bills = []

        for bill_type in BILL_TYPES:
            try:
                batch = await self._fetch_bills(bill_type, start_date, end_date)
                bills.extend(batch)
            except Exception as e:
                logger.error(f"Congress API failed for {bill_type}: {e}")

        # Filter for market-relevant bills
        relevant = [b for b in bills if b.sector]

        logger.info(
            f"Congress: {len(bills)} total bills with recent action, "
            f"{len(relevant)} market-relevant"
        )
        return relevant

    async def _fetch_bills(
        self, bill_type: str, start_date: datetime, end_date: datetime
    ) -> List[CongressBill]:
        """Fetch bills of a specific type with recent action."""
        params = {
            "api_key": self.api_key,
            "format": "json",
            "fromDateTime": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "toDateTime": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sort": "updateDate+desc",
            "limit": 50,
            "offset": 0,
        }

        results = []
        offset = 0
        max_pages = 3

        headers = {
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            for page in range(max_pages):
                params["offset"] = offset

                async with session.get(
                    f"{CONGRESS_API_BASE}/bill", params=params
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            f"Congress API error {resp.status}: {body[:200]}"
                        )
                        break

                    data = await resp.json()

                bills_data = data.get("bills", [])
                if not bills_data:
                    break

                for bill in bills_data:
                    parsed = self._parse_bill(bill)
                    if parsed:
                        results.append(parsed)

                # Check if there are more pages
                pagination = data.get("pagination", {})
                total = pagination.get("count", 0)
                if offset + len(bills_data) >= total:
                    break
                offset += len(bills_data)

        return results

    def _parse_bill(self, bill: dict) -> Optional[CongressBill]:
        """Parse a Congress API bill response into CongressBill."""
        title = bill.get("title", "")
        if not title:
            return None

        bill_type = bill.get("type", "").lower()
        bill_number = bill.get("number", "")
        congress = bill.get("congress", "")
        bill_id = f"{bill_type}{bill_number}-{congress}"

        introduced_date = bill.get("introducedDate", "")
        url = bill.get("url", f"https://congress.gov/bill/{congress}th-congress/{bill_type}/{bill_number}")

        # Latest action
        latest_action_obj = bill.get("latestAction", {})
        latest_action = latest_action_obj.get("text", "")
        action_date = latest_action_obj.get("actionDate", "")

        # Determine sector relevance
        sector = self._classify_sector(title, latest_action)

        # Check if the latest action is significant (progressing)
        action_lower = latest_action.lower()
        is_significant = any(sa in action_lower for sa in SIGNIFICANT_ACTIONS)

        # Only return bills with significant action OR sector relevance
        if not sector and not is_significant:
            return None

        return CongressBill(
            bill_id=bill_id,
            title=title[:300],
            bill_type=bill_type.upper(),
            introduced_date=introduced_date,
            latest_action=latest_action[:300],
            url=url,
            sector=sector,
            action_date=action_date,
        )

    def _classify_sector(self, title: str, action: str) -> str:
        """Classify a bill's market sector based on title and action keywords."""
        combined = (title + " " + action).lower()

        for sector, keywords in SECTOR_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                return sector

        return ""
