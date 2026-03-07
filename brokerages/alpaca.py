"""Alpaca brokerage adapter for paper and live trading."""

import logging
from typing import List, Optional

import aiohttp

from brokerages.base import BrokerageBase

logger = logging.getLogger(__name__)

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"


class AlpacaBrokerage(BrokerageBase):
    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = PAPER_BASE if paper else LIVE_BASE
        self.data_url = DATA_BASE
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Alpaca {method} {url} -> {resp.status}: {body}")
                    raise Exception(f"Alpaca API error {resp.status}: {body}")
                return await resp.json()

    async def get_account(self) -> dict:
        data = await self._request("GET", f"{self.base_url}/v2/account")
        return {
            "equity": float(data["equity"]),
            "buying_power": float(data["buying_power"]),
            "cash": float(data["cash"]),
            "status": data["status"],
        }

    async def get_positions(self) -> List[dict]:
        data = await self._request("GET", f"{self.base_url}/v2/positions")
        return [
            {
                "ticker": p["symbol"],
                "qty": int(p["qty"]),
                "side": "LONG" if int(p["qty"]) > 0 else "SHORT",
                "avg_entry": float(p["avg_entry_price"]),
                "current_price": float(p["current_price"]),
                "market_value": float(p["market_value"]),
                "unrealized_pl": float(p["unrealized_pl"]),
                "unrealized_plpc": float(p["unrealized_plpc"]),
            }
            for p in data
        ]

    async def place_order(
        self,
        ticker: str,
        side: str,
        qty: int,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Optional[str]:
        order_data = {
            "symbol": ticker,
            "qty": str(qty),
            "side": side.lower(),
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            order_data["limit_price"] = str(limit_price)

        try:
            data = await self._request(
                "POST", f"{self.base_url}/v2/orders", json=order_data
            )
            order_id = data.get("id")
            logger.info(f"Order placed: {side} {qty} {ticker} -> {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Order failed: {side} {qty} {ticker}: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._request("DELETE", f"{self.base_url}/v2/orders/{order_id}")
            return True
        except Exception:
            return False

    async def get_quote(self, ticker: str) -> dict:
        data = await self._request(
            "GET", f"{self.data_url}/v2/stocks/{ticker}/quotes/latest"
        )
        quote = data.get("quote", {})
        return {
            "bid": float(quote.get("bp", 0)),
            "ask": float(quote.get("ap", 0)),
            "price": (float(quote.get("bp", 0)) + float(quote.get("ap", 0))) / 2,
        }

    async def get_bars(self, ticker: str, timeframe: str, limit: int) -> List[dict]:
        params = {"timeframe": timeframe, "limit": limit}
        data = await self._request(
            "GET", f"{self.data_url}/v2/stocks/{ticker}/bars", params=params
        )
        return [
            {
                "timestamp": bar["t"],
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": int(bar["v"]),
            }
            for bar in data.get("bars", [])
        ]

    async def get_snapshot(self, ticker: str) -> dict:
        """Get a full snapshot: quote + latest trade + daily bar."""
        data = await self._request(
            "GET", f"{self.data_url}/v2/stocks/{ticker}/snapshot"
        )
        daily = data.get("dailyBar", {})
        latest = data.get("latestTrade", {})
        prev = data.get("prevDailyBar", {})
        prev_close = float(prev.get("c", 0))
        current = float(latest.get("p", 0))
        daily_change = ((current - prev_close) / prev_close * 100) if prev_close else 0

        return {
            "price": current,
            "daily_change_pct": round(daily_change, 2),
            "volume": int(daily.get("v", 0)),
            "open": float(daily.get("o", 0)),
            "high": float(daily.get("h", 0)),
            "low": float(daily.get("l", 0)),
        }
