"""Abstract brokerage interface."""

from abc import ABC, abstractmethod
from typing import List, Optional


class BrokerageBase(ABC):
    @abstractmethod
    async def get_account(self) -> dict:
        """Return account info: equity, buying_power, cash."""
        ...

    @abstractmethod
    async def get_positions(self) -> List[dict]:
        """Return list of open positions from the brokerage."""
        ...

    @abstractmethod
    async def place_order(
        self,
        ticker: str,
        side: str,
        qty: int,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Optional[str]:
        """Place an order. Returns order_id or None on failure."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        ...

    @abstractmethod
    async def get_quote(self, ticker: str) -> dict:
        """Get current quote: price, bid, ask, volume."""
        ...

    @abstractmethod
    async def get_bars(self, ticker: str, timeframe: str, limit: int) -> List[dict]:
        """Get historical bars."""
        ...
