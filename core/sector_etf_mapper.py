"""Maps macro sectors (from FedReg/Congress signals) to tradeable sector ETFs."""

import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

# Sector -> primary ETF + alternates
# These are the most liquid sector ETFs for each category
SECTOR_ETF_MAP = {
    # Defense / Military
    "defense": {"primary": "ITA", "alternates": ["PPA", "XAR"]},
    "Defense": {"primary": "ITA", "alternates": ["PPA", "XAR"]},
    "Defense/Security": {"primary": "ITA", "alternates": ["PPA", "XAR"]},
    "Aviation/Defense": {"primary": "ITA", "alternates": ["PPA", "XAR"]},

    # Healthcare / Pharma
    "healthcare": {"primary": "XLV", "alternates": ["VHT", "IBB"]},
    "Healthcare": {"primary": "XLV", "alternates": ["VHT", "IBB"]},
    "Pharma": {"primary": "IBB", "alternates": ["XBI", "XLV"]},

    # Technology
    "technology": {"primary": "XLK", "alternates": ["VGT", "QQQ"]},
    "Tech/Antitrust": {"primary": "XLK", "alternates": ["VGT", "QQQ"]},

    # Energy
    "energy": {"primary": "XLE", "alternates": ["VDE", "IYE"]},
    "Energy": {"primary": "XLE", "alternates": ["VDE", "IYE"]},
    "Energy/Utilities": {"primary": "XLU", "alternates": ["VPU", "XLE"]},

    # Finance
    "finance": {"primary": "XLF", "alternates": ["VFH", "KBE"]},
    "Financial": {"primary": "XLF", "alternates": ["VFH", "KBE"]},

    # Infrastructure / Industrials
    "infrastructure": {"primary": "PAVE", "alternates": ["XLI", "IFRA"]},

    # Trade / Commerce
    "trade": {"primary": "EFA", "alternates": ["EEM", "XLI"]},
    "Trade": {"primary": "EFA", "alternates": ["EEM", "XLI"]},
    "Telecom": {"primary": "XLC", "alternates": ["VOX", "FCOM"]},

    # Broad market (executive orders, presidential actions)
    "Broad market": {"primary": "SPY", "alternates": ["QQQ", "IWM"]},
    "appropriations": {"primary": "SPY", "alternates": ["QQQ", "IWM"]},
}


class SectorETFMapper:
    """Resolves sector names from macro signals to tradeable ETF tickers."""

    def resolve(self, sector: str) -> Optional[str]:
        """Return the primary ETF ticker for a sector, or None if unknown."""
        entry = SECTOR_ETF_MAP.get(sector)
        if entry:
            return entry["primary"]

        # Try case-insensitive match
        sector_lower = sector.lower().strip()
        for key, val in SECTOR_ETF_MAP.items():
            if key.lower() == sector_lower:
                return val["primary"]

        logger.debug(f"No ETF mapping for sector: '{sector}'")
        return None

    def resolve_all(self, sector: str) -> List[str]:
        """Return primary + alternate ETFs for a sector."""
        entry = SECTOR_ETF_MAP.get(sector)
        if entry:
            return [entry["primary"]] + entry["alternates"]

        sector_lower = sector.lower().strip()
        for key, val in SECTOR_ETF_MAP.items():
            if key.lower() == sector_lower:
                return [val["primary"]] + val["alternates"]

        return []

    def is_macro_signal(self, source: str) -> bool:
        """Check if a signal source is a macro signal that needs ETF mapping."""
        return source in ("FED_REGISTER", "CONGRESS")
