"""Federal Register feed poller -- polls federalregister.gov API for rules and EOs."""

import logging
from datetime import datetime, timedelta
from typing import List
from dataclasses import dataclass, field

import aiohttp

from utils.utils import retry_async

logger = logging.getLogger(__name__)

FR_API_BASE = "https://www.federalregister.gov/api/v1"

# Document types that may move markets
RELEVANT_DOC_TYPES = [
    "Rule",
    "Presidential Document",
]

# Keywords suggesting economic significance (used for filtering)
ECONOMIC_KEYWORDS = [
    "billion", "million", "tariff", "sanction", "defense", "procurement",
    "pharmaceutical", "drug", "energy", "oil", "gas", "semiconductor",
    "technology", "cybersecurity", "healthcare", "medicare", "medicaid",
    "infrastructure", "telecommunications", "aviation", "banking",
    "financial", "securities", "export", "import", "trade",
    "contractor", "contract", "appropriation", "budget",
    "executive order", "emergency", "national security",
]

# Agencies whose rules tend to move specific sectors
MARKET_RELEVANT_AGENCIES = {
    "defense-department": "Defense",
    "health-and-human-services-department": "Healthcare",
    "food-and-drug-administration": "Pharma",
    "environmental-protection-agency": "Energy/Utilities",
    "federal-communications-commission": "Telecom",
    "securities-and-exchange-commission": "Financial",
    "federal-trade-commission": "Tech/Antitrust",
    "federal-aviation-administration": "Aviation/Defense",
    "energy-department": "Energy",
    "commerce-department": "Trade",
    "treasury-department": "Financial",
    "homeland-security-department": "Defense/Security",
    "executive-office-of-the-president": "Broad market",
}


@dataclass
class FedRegDocument:
    document_type: str  # "rule", "proposed_rule", "executive_order"
    title: str
    abstract: str
    agencies: List[str]
    publication_date: str
    url: str
    significant: bool = False
    affected_sector: str = ""


class FederalRegisterFeed:
    """Polls Federal Register API for rules, executive orders, and presidential documents."""

    @retry_async(max_retries=2)
    async def poll(self, lookback_hours: int = 24) -> List[FedRegDocument]:
        """Poll Federal Register for recent economically significant documents."""
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(hours=lookback_hours)

        documents = []

        for doc_type in RELEVANT_DOC_TYPES:
            try:
                batch = await self._fetch_documents(doc_type, start_date, end_date)
                documents.extend(batch)
            except Exception as e:
                logger.error(f"Federal Register poll failed for {doc_type}: {e}")

        # Filter for economic significance
        significant = [d for d in documents if d.significant]

        logger.info(
            f"Federal Register: {len(documents)} total docs, "
            f"{len(significant)} economically significant"
        )
        return significant if significant else documents[:20]

    async def _fetch_documents(
        self, doc_type: str, start_date: datetime, end_date: datetime
    ) -> List[FedRegDocument]:
        """Fetch documents of a specific type from the Federal Register API."""
        params = {
            "conditions[type][]": doc_type,
            "conditions[publication_date][gte]": start_date.strftime("%Y-%m-%d"),
            "conditions[publication_date][lte]": end_date.strftime("%Y-%m-%d"),
            "fields[]": [
                "title", "abstract", "document_number", "type",
                "publication_date", "html_url", "agencies",
                "significant", "regulation_id_number_info",
            ],
            "per_page": 100,
            "page": 1,
            "order": "newest",
        }

        results = []
        page = 1
        max_pages = 5

        async with aiohttp.ClientSession() as session:
            while page <= max_pages:
                params["page"] = page

                async with session.get(
                    f"{FR_API_BASE}/documents.json", params=params
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            f"Federal Register API error {resp.status}: {body[:200]}"
                        )
                        break

                    data = await resp.json()

                results_list = data.get("results", [])
                if not results_list:
                    break

                for doc in results_list:
                    parsed = self._parse_document(doc)
                    if parsed:
                        results.append(parsed)

                # Check pagination
                total_pages = data.get("total_pages", 1)
                if page >= total_pages:
                    break
                page += 1

        return results

    def _parse_document(self, doc: dict) -> FedRegDocument:
        """Parse a Federal Register API document into FedRegDocument."""
        title = doc.get("title", "")
        abstract = doc.get("abstract", "") or ""
        doc_type = doc.get("type", "").lower().replace(" ", "_")
        pub_date = doc.get("publication_date", "")
        url = doc.get("html_url", "")

        # Extract agency names
        agencies_raw = doc.get("agencies", [])
        agency_names = []
        agency_slugs = []
        for ag in agencies_raw:
            name = ag.get("name", "")
            slug = ag.get("slug", "")
            if name:
                agency_names.append(name)
            if slug:
                agency_slugs.append(slug)

        # Determine if this document is economically significant
        is_significant = doc.get("significant", False)

        # Also check for presidential documents (always significant)
        if doc_type == "presidential_document":
            is_significant = True

        # Check title + abstract for economic keywords
        combined_text = (title + " " + abstract).lower()
        keyword_match = any(kw in combined_text for kw in ECONOMIC_KEYWORDS)
        if keyword_match:
            is_significant = True

        # Check if agency is market-relevant
        affected_sector = ""
        for slug in agency_slugs:
            if slug in MARKET_RELEVANT_AGENCIES:
                affected_sector = MARKET_RELEVANT_AGENCIES[slug]
                is_significant = True
                break

        return FedRegDocument(
            document_type=doc_type,
            title=title,
            abstract=abstract[:500],
            agencies=agency_names,
            publication_date=pub_date,
            url=url,
            significant=is_significant,
            affected_sector=affected_sector,
        )
