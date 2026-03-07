"""Telegram notification bot for trade alerts and status updates."""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.debug("Telegram not configured, skipping notification")
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                    },
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Telegram send failed ({resp.status}): {body}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    async def signal_detected(self, catalyst_type: str, ticker: str, confidence: int, summary: str):
        msg = (
            f"<b>Signal Detected</b>\n"
            f"Type: {catalyst_type}\n"
            f"Ticker: <b>{ticker}</b>\n"
            f"Confidence: {confidence}%\n"
            f"{summary}"
        )
        await self.send(msg)

    async def position_opened(self, ticker: str, side: str, shares: int, price: float, reasoning: str):
        msg = (
            f"<b>Position Opened</b>\n"
            f"{'BUY' if side == 'LONG' else 'SHORT'} <b>{ticker}</b>\n"
            f"Shares: {shares} @ ${price:.2f}\n"
            f"Size: ${shares * price:,.2f}\n\n"
            f"{reasoning}"
        )
        await self.send(msg)

    async def position_closed(self, ticker: str, exit_reason: str, pnl: float):
        emoji = "+" if pnl >= 0 else ""
        msg = (
            f"<b>Position Closed</b>\n"
            f"Ticker: <b>{ticker}</b>\n"
            f"Reason: {exit_reason}\n"
            f"P&L: {emoji}${pnl:,.2f}"
        )
        await self.send(msg)

    async def error(self, message: str):
        await self.send(f"<b>Error</b>\n{message}")
