"""QuiverQuant Congress trading feed -- polls for stock trades by U.S. Congress members."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Set
from dataclasses import dataclass, field

import aiohttp

from utils.utils import retry_async

logger = logging.getLogger(__name__)

QUIVER_API_BASE = "https://api.quiverquant.com"
LIVE_ENDPOINT = "/beta/live/congresstrading"
BULK_ENDPOINT = "/beta/bulk/congresstrading"

# Amount ranges reported by Congress (STOCK Act disclosure format)
# Map range strings to estimated midpoint dollar values for filtering
AMOUNT_RANGE_MAP = {
    "$1,001 - $15,000": (1_001, 15_000),
    "$15,001 - $50,000": (15_001, 50_000),
    "$50,001 - $100,000": (50_001, 100_000),
    "$100,001 - $250,000": (100_001, 250_000),
    "$250,001 - $500,000": (250_001, 500_000),
    "$500,001 - $1,000,000": (500_001, 1_000_000),
    "$1,000,001 - $5,000,000": (1_000_001, 5_000_000),
    "$5,000,001 - $25,000,000": (5_000_001, 25_000_000),
    "$25,000,001 - $50,000,000": (25_000_001, 50_000_000),
    "$50,000,001+": (50_000_001, 100_000_000),
    "Over $50,000,000": (50_000_001, 100_000_000),
}

# Committee-sector mapping: Congress members on these committees trading
# in the related sector is a stronger signal (potential information advantage)
COMMITTEE_SECTOR_MAP = {
    # Armed Services committees
    "armed services": {"defense", "aerospace"},
    "defense": {"defense", "aerospace"},
    # Finance / Banking
    "finance": {"finance", "banking", "insurance", "crypto"},
    "banking": {"finance", "banking", "insurance", "crypto"},
    "financial services": {"finance", "banking", "insurance", "crypto"},
    # Energy
    "energy and natural resources": {"energy", "oil", "gas", "utilities", "solar", "nuclear"},
    "energy and commerce": {"energy", "healthcare", "technology", "telecom"},
    # Health
    "health": {"healthcare", "pharma", "biotech"},
    "health, education, labor, and pensions": {"healthcare", "pharma", "education"},
    # Tech / Science
    "science, space, and technology": {"technology", "aerospace", "semiconductor"},
    "commerce, science, and transportation": {"technology", "telecom", "transportation"},
    # Agriculture
    "agriculture": {"agriculture", "food", "commodities"},
    # Appropriations (broad, but signals funding direction)
    "appropriations": {"defense", "healthcare", "infrastructure", "technology"},
    # Intelligence
    "intelligence": {"defense", "technology", "cybersecurity"},
    # Transportation
    "transportation and infrastructure": {"transportation", "infrastructure", "airlines"},
    # Judiciary (antitrust, tech regulation)
    "judiciary": {"technology", "media"},
}

# Known committee memberships for high-profile traders
# This is a seed list -- in production you'd fetch from a congressional API
# Keys are lowercased politician names
KNOWN_COMMITTEES: Dict[str, List[str]] = {
    "nancy pelosi": ["financial services", "intelligence"],
    "dan crenshaw": ["energy and commerce", "intelligence"],
    "tommy tuberville": ["armed services", "agriculture"],
    "mark green": ["armed services", "homeland security"],
    "michael mccaul": ["foreign affairs", "science, space, and technology"],
    "ro khanna": ["armed services", "oversight"],
    "josh gottheimer": ["financial services"],
    "marjorie taylor greene": ["homeland security", "oversight"],
    "john curtis": ["energy and commerce"],
    "pat fallon": ["armed services", "oversight"],
    "kevin hern": ["energy and commerce"],
    "garret graves": ["transportation and infrastructure", "energy and commerce"],
    "markwayne mullin": ["armed services", "energy and natural resources"],
    "rick scott": ["armed services", "commerce, science, and transportation"],
    "bill hagerty": ["banking", "appropriations"],
    "john hickenlooper": ["commerce, science, and transportation", "energy and natural resources"],
    "shelley moore capito": ["appropriations", "commerce, science, and transportation", "energy and natural resources"],
    "cynthia lummis": ["banking", "commerce, science, and transportation"],
}

# Ticker -> sector mapping for committee relevance checks
# Covers major stocks Congress members frequently trade
TICKER_SECTOR_MAP = {
    # Defense
    "LMT": "defense", "RTX": "defense", "NOC": "defense", "BA": "defense",
    "GD": "defense", "LHX": "defense", "HII": "defense", "TXT": "defense",
    "KTOS": "defense", "PLTR": "defense",
    # Tech
    "AAPL": "technology", "MSFT": "technology", "GOOG": "technology",
    "GOOGL": "technology", "AMZN": "technology", "META": "technology",
    "NVDA": "semiconductor", "AMD": "semiconductor", "INTC": "semiconductor",
    "TSM": "semiconductor", "AVGO": "semiconductor", "CRM": "technology",
    "ORCL": "technology", "CSCO": "technology", "ADBE": "technology",
    # Healthcare / Pharma
    "JNJ": "healthcare", "PFE": "pharma", "UNH": "healthcare",
    "ABBV": "pharma", "MRK": "pharma", "LLY": "pharma",
    "BMY": "pharma", "GILD": "biotech", "AMGN": "biotech",
    "MRNA": "biotech", "BNTX": "biotech",
    # Finance
    "JPM": "finance", "BAC": "finance", "GS": "finance",
    "MS": "finance", "WFC": "finance", "C": "finance",
    "BLK": "finance", "SCHW": "finance", "V": "finance",
    "MA": "finance",
    # Energy
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    "OXY": "oil", "SLB": "energy", "EOG": "oil",
    "DVN": "oil", "FSLR": "solar", "ENPH": "solar",
    # Airlines / Transport
    "DAL": "airlines", "UAL": "airlines", "LUV": "airlines",
    "AAL": "airlines", "UNP": "transportation", "CSX": "transportation",
    # Telecom
    "T": "telecom", "VZ": "telecom", "TMUS": "telecom",
}


@dataclass
class CongressTrade:
    """A single Congress member stock trade from QuiverQuant."""
    representative: str
    party: str           # "D", "R", "I"
    chamber: str         # "House" or "Senate"
    ticker: str
    transaction: str     # "Purchase", "Sale", "Sale (Full)", "Sale (Partial)", "Exchange"
    amount_range: str    # e.g. "$15,001 - $50,000"
    amount_low: float    # Lower bound of range
    amount_high: float   # Upper bound of range
    transaction_date: str
    report_date: str     # Disclosure date
    disclosure_lag_days: int
    description: str
    district: str


@dataclass
class CongressTradeCluster:
    """Multiple Congress members buying the same stock within a window."""
    ticker: str
    trades: List[CongressTrade]
    unique_members: int
    total_amount_low: float
    total_amount_high: float
    window_days: int

    @property
    def bipartisan(self) -> bool:
        parties = {t.party for t in self.trades}
        return len(parties) > 1


@dataclass
class CongressTradeSignal:
    """Processed signal from Congress trading data."""
    signal_type: str  # CONGRESS_TRADE_BUY, CONGRESS_TRADE_CLUSTER, CONGRESS_TRADE_COMMITTEE
    trade: Optional[CongressTrade] = None
    cluster: Optional[CongressTradeCluster] = None
    committee_relevance: str = ""  # Which committee is relevant
    sector_match: str = ""         # Which sector the trade maps to


class CongressTradingFeed:
    """Polls QuiverQuant API for Congress member stock trades."""

    def __init__(
        self,
        api_token: str,
        min_amount: float = 15_000,
        cluster_window_days: int = 14,
        cluster_min_members: int = 2,
    ):
        self.api_token = api_token
        self.min_amount = min_amount
        self.cluster_window_days = cluster_window_days
        self.cluster_min_members = cluster_min_members
        self._seen_trades: Set[str] = set()  # Dedup key: "representative|ticker|transaction_date"

    @retry_async(max_retries=2)
    async def poll(self, lookback_days: int = 7) -> List[CongressTradeSignal]:
        """Poll QuiverQuant for recent Congress trades and produce signals.

        Returns a list of CongressTradeSignal objects (individual buys,
        cluster buys, committee-relevant trades).
        """
        if not self.api_token:
            logger.warning("QUIVERQUANT_API_TOKEN not set, skipping Congress trading poll")
            return []

        trades = await self._fetch_trades(lookback_days)
        if not trades:
            return []

        # Filter for purchases only (the bullish signal)
        purchases = [t for t in trades if self._is_purchase(t)]
        logger.info(
            f"Congress trading: {len(trades)} total trades, "
            f"{len(purchases)} purchases above ${self.min_amount:,.0f}"
        )

        if not purchases:
            return []

        signals = []

        # 1) Detect cluster buys (multiple members buying same ticker)
        clusters = self._detect_clusters(purchases)
        cluster_tickers = set()
        for cluster in clusters:
            cluster_tickers.add(cluster.ticker)
            signal = CongressTradeSignal(
                signal_type="CONGRESS_TRADE_CLUSTER",
                cluster=cluster,
            )
            # Check if any member in the cluster has committee relevance
            for trade in cluster.trades:
                committee, sector = self._check_committee_relevance(trade)
                if committee:
                    signal.signal_type = "CONGRESS_TRADE_COMMITTEE"
                    signal.committee_relevance = committee
                    signal.sector_match = sector
                    break
            signals.append(signal)

        # 2) Individual significant trades (not already in a cluster)
        for trade in purchases:
            if trade.ticker in cluster_tickers:
                continue  # Already covered by cluster signal

            # Check committee relevance
            committee, sector = self._check_committee_relevance(trade)
            if committee:
                signal = CongressTradeSignal(
                    signal_type="CONGRESS_TRADE_COMMITTEE",
                    trade=trade,
                    committee_relevance=committee,
                    sector_match=sector,
                )
            else:
                signal = CongressTradeSignal(
                    signal_type="CONGRESS_TRADE_BUY",
                    trade=trade,
                )
            signals.append(signal)

        logger.info(
            f"Congress trading signals: {len(signals)} "
            f"({sum(1 for s in signals if s.signal_type == 'CONGRESS_TRADE_CLUSTER')} clusters, "
            f"{sum(1 for s in signals if s.signal_type == 'CONGRESS_TRADE_COMMITTEE')} committee, "
            f"{sum(1 for s in signals if s.signal_type == 'CONGRESS_TRADE_BUY')} individual)"
        )
        return signals

    async def _fetch_trades(self, lookback_days: int) -> List[CongressTrade]:
        """Fetch recent trades from QuiverQuant API."""
        headers = {
            "Authorization": f"Token {self.api_token}",
            "Accept": "application/json",
        }

        trades = []

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                # Use the bulk endpoint with pagination for broader coverage
                page = 1
                max_pages = 5
                page_size = 100

                while page <= max_pages:
                    params = {
                        "page": page,
                        "page_size": page_size,
                    }

                    url = f"{QUIVER_API_BASE}{BULK_ENDPOINT}"

                    async with session.get(url, params=params) as resp:
                        if resp.status == 401:
                            logger.error("QuiverQuant API: authentication failed (invalid token)")
                            return []
                        if resp.status == 403:
                            logger.error("QuiverQuant API: access forbidden (check subscription)")
                            return []
                        if resp.status == 429:
                            logger.warning("QuiverQuant API: rate limited, stopping pagination")
                            break
                        if resp.status != 200:
                            body = await resp.text()
                            logger.error(f"QuiverQuant API error {resp.status}: {body[:300]}")
                            break

                        data = await resp.json()

                    # Response is a list of trade objects
                    if not data:
                        break

                    # If it's a dict with results key (paginated response)
                    if isinstance(data, dict):
                        items = data.get("results", data.get("data", []))
                        total = data.get("count", 0)
                    else:
                        items = data
                        total = 0

                    if not items:
                        break

                    cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)
                    page_has_old = False

                    for item in items:
                        trade = self._parse_trade(item)
                        if not trade:
                            continue

                        # Check if trade is within lookback window
                        try:
                            tx_date = datetime.strptime(trade.report_date[:10], "%Y-%m-%d")
                            if tx_date < cutoff_date:
                                page_has_old = True
                                continue
                        except (ValueError, TypeError):
                            pass  # Keep trades with unparseable dates

                        # Dedup
                        dedup_key = f"{trade.representative}|{trade.ticker}|{trade.transaction_date}"
                        if dedup_key in self._seen_trades:
                            continue
                        self._seen_trades.add(dedup_key)

                        # Amount filter
                        if trade.amount_high < self.min_amount:
                            continue

                        trades.append(trade)

                    # Stop if we've gone past our lookback window
                    if page_has_old and not items:
                        break

                    # Stop if no more pages
                    if isinstance(data, dict) and total > 0:
                        if page * page_size >= total:
                            break
                    elif len(items) < page_size:
                        break

                    page += 1

        except aiohttp.ClientError as e:
            logger.error(f"QuiverQuant API connection error: {e}")
            raise

        # Prune seen trades older than 30 days (prevent unbounded memory growth)
        if len(self._seen_trades) > 5000:
            self._seen_trades = set(list(self._seen_trades)[-2000:])

        logger.info(f"QuiverQuant: fetched {len(trades)} trades in lookback window")
        return trades

    def _parse_trade(self, item: dict) -> Optional[CongressTrade]:
        """Parse a QuiverQuant API trade response into a CongressTrade."""
        ticker = item.get("Ticker") or item.get("ticker", "")
        if not ticker:
            return None

        representative = item.get("Representative") or item.get("representative", "")
        if not representative:
            return None

        transaction = item.get("Transaction") or item.get("transaction", "")
        amount_range = item.get("Range") or item.get("range", "")
        party = item.get("Party") or item.get("party", "")
        chamber = item.get("House") or item.get("house", "")
        district = item.get("District") or item.get("district", "")
        description = item.get("Description") or item.get("description", "")

        # Parse transaction date
        tx_date_raw = item.get("TransactionDate") or item.get("transaction_date", "")
        report_date_raw = item.get("ReportDate") or item.get("report_date", "")

        # Normalize dates to YYYY-MM-DD
        tx_date = self._normalize_date(tx_date_raw)
        report_date = self._normalize_date(report_date_raw)

        # Parse amount range
        amount_low, amount_high = self._parse_amount_range(amount_range)

        # If Amount field is available as a number, use it
        amount_num = item.get("Amount") or item.get("amount")
        if amount_num and isinstance(amount_num, (int, float)) and amount_num > 0:
            # Amount field is sometimes the midpoint or estimated value
            if amount_low == 0:
                amount_low = amount_num * 0.5
                amount_high = amount_num * 1.5

        # Calculate disclosure lag
        disclosure_lag = self._calc_disclosure_lag(tx_date, report_date)

        return CongressTrade(
            representative=representative,
            party=party,
            chamber=chamber,
            ticker=ticker.upper(),
            transaction=transaction,
            amount_range=amount_range or f"${amount_low:,.0f} - ${amount_high:,.0f}",
            amount_low=amount_low,
            amount_high=amount_high,
            transaction_date=tx_date,
            report_date=report_date,
            disclosure_lag_days=disclosure_lag,
            description=description[:300] if description else "",
            district=district,
        )

    def _normalize_date(self, date_val) -> str:
        """Normalize various date formats to YYYY-MM-DD string."""
        if not date_val:
            return ""
        date_str = str(date_val)
        # Handle ISO format with time component
        if "T" in date_str:
            date_str = date_str.split("T")[0]
        # Already YYYY-MM-DD
        if len(date_str) == 10 and date_str[4] == "-":
            return date_str
        # Try common formats
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%b %d, %Y"):
            try:
                return datetime.strptime(date_str[:10], fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return date_str[:10]

    def _parse_amount_range(self, range_str: str) -> tuple:
        """Parse a STOCK Act amount range string into (low, high) bounds."""
        if not range_str:
            return (0, 0)

        # Direct lookup
        if range_str in AMOUNT_RANGE_MAP:
            return AMOUNT_RANGE_MAP[range_str]

        # Normalize and try again
        normalized = range_str.replace(" ", " ").strip()
        for key, bounds in AMOUNT_RANGE_MAP.items():
            if key.lower() == normalized.lower():
                return bounds

        # Try to parse "$X - $Y" format
        try:
            parts = range_str.replace("$", "").replace(",", "").split("-")
            if len(parts) == 2:
                low = float(parts[0].strip().replace("+", ""))
                high = float(parts[1].strip().replace("+", ""))
                return (low, high)
            elif "+" in range_str:
                val = float(parts[0].strip().replace("+", ""))
                return (val, val * 2)
        except (ValueError, IndexError):
            pass

        return (0, 0)

    def _calc_disclosure_lag(self, tx_date: str, report_date: str) -> int:
        """Calculate days between transaction and disclosure."""
        if not tx_date or not report_date:
            return 0
        try:
            tx = datetime.strptime(tx_date[:10], "%Y-%m-%d")
            report = datetime.strptime(report_date[:10], "%Y-%m-%d")
            lag = (report - tx).days
            return max(lag, 0)
        except (ValueError, TypeError):
            return 0

    def _is_purchase(self, trade: CongressTrade) -> bool:
        """Check if a trade is a purchase (bullish signal)."""
        tx = trade.transaction.lower()
        return "purchase" in tx or tx == "buy"

    def _detect_clusters(self, purchases: List[CongressTrade]) -> List[CongressTradeCluster]:
        """Detect cluster buys: multiple members buying the same ticker within a window."""
        # Group purchases by ticker
        by_ticker: Dict[str, List[CongressTrade]] = defaultdict(list)
        for trade in purchases:
            by_ticker[trade.ticker].append(trade)

        clusters = []
        for ticker, trades in by_ticker.items():
            if len(trades) < self.cluster_min_members:
                continue

            # Check unique members
            unique_members = {t.representative for t in trades}
            if len(unique_members) < self.cluster_min_members:
                continue

            # Check time window
            dates = []
            for t in trades:
                try:
                    dates.append(datetime.strptime(t.transaction_date[:10], "%Y-%m-%d"))
                except (ValueError, TypeError):
                    continue

            if len(dates) < 2:
                # Can't verify window without dates, include anyway
                pass
            else:
                date_span = (max(dates) - min(dates)).days
                if date_span > self.cluster_window_days:
                    continue

            cluster = CongressTradeCluster(
                ticker=ticker,
                trades=trades,
                unique_members=len(unique_members),
                total_amount_low=sum(t.amount_low for t in trades),
                total_amount_high=sum(t.amount_high for t in trades),
                window_days=self.cluster_window_days,
            )
            clusters.append(cluster)

        return clusters

    def _check_committee_relevance(self, trade: CongressTrade) -> tuple:
        """Check if a Congress member's trade is relevant to their committee assignment.

        Returns (committee_name, sector) if relevant, ("", "") otherwise.
        """
        name_lower = trade.representative.lower().strip()
        committees = KNOWN_COMMITTEES.get(name_lower, [])

        if not committees:
            return ("", "")

        # Get the sector of the traded stock
        ticker_sector = TICKER_SECTOR_MAP.get(trade.ticker, "")
        if not ticker_sector:
            return ("", "")

        # Check if any committee covers this sector
        for committee in committees:
            committee_lower = committee.lower()
            related_sectors = COMMITTEE_SECTOR_MAP.get(committee_lower, set())
            if ticker_sector in related_sectors:
                return (committee, ticker_sector)

        return ("", "")
