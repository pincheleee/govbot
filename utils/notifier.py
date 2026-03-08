"""Telegram notification bot for trade alerts, status updates, and commands."""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional, Callable, Awaitable

import aiohttp

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""

        # Command polling state
        self._polling = False
        self._poll_task: Optional[asyncio.Task] = None
        self._last_update_id = 0
        self._start_time = time.time()

        # Callbacks set by GovBot
        self._get_status: Optional[Callable[[], dict]] = None
        self._get_positions: Optional[Callable[[], dict]] = None
        self._pause_callback: Optional[Callable[[], None]] = None
        self._resume_callback: Optional[Callable[[], None]] = None
        self._is_paused: Optional[Callable[[], bool]] = None

    def register_callbacks(
        self,
        get_status: Callable[[], dict],
        get_positions: Callable[[], dict],
        pause_callback: Callable[[], None],
        resume_callback: Callable[[], None],
        is_paused: Callable[[], bool],
    ):
        """Register callback functions from the bot for command handling."""
        self._get_status = get_status
        self._get_positions = get_positions
        self._pause_callback = pause_callback
        self._resume_callback = resume_callback
        self._is_paused = is_paused

    async def start_polling(self):
        """Start polling for incoming Telegram messages (commands)."""
        if not self.enabled:
            logger.debug("Telegram not configured, command polling disabled")
            return
        self._polling = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram command polling started")

    async def stop_polling(self):
        """Stop the polling loop."""
        self._polling = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("Telegram command polling stopped")

    async def _poll_loop(self):
        """Long-poll for incoming Telegram updates."""
        while self._polling:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
                await asyncio.sleep(5)

    async def _get_updates(self) -> list:
        """Fetch new updates from Telegram using long polling."""
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": '["message"]',
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/getUpdates",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    results = data.get("result", [])
                    if results:
                        self._last_update_id = results[-1]["update_id"]
                    return results
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            logger.error(f"Telegram getUpdates error: {e}")
            await asyncio.sleep(2)
            return []

    async def _handle_update(self, update: dict):
        """Route an incoming Telegram message to the appropriate command handler."""
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Only respond to messages from the configured chat
        if chat_id != self.chat_id:
            return

        if not text.startswith("/"):
            return

        command = text.split()[0].lower()
        # Strip @botname suffix if present (e.g. /status@mybotname)
        if "@" in command:
            command = command.split("@")[0]

        logger.info(f"Telegram command received: {command}")

        if command == "/status":
            await self._cmd_status()
        elif command == "/positions":
            await self._cmd_positions()
        elif command == "/pause":
            await self._cmd_pause()
        elif command == "/resume":
            await self._cmd_resume()
        elif command == "/help":
            await self._cmd_help()
        else:
            await self.send(f"Unknown command: {command}\nTry /help")

    async def _cmd_status(self):
        """Handle /status command: show bot status, uptime, position count."""
        uptime_secs = int(time.time() - self._start_time)
        hours = uptime_secs // 3600
        minutes = (uptime_secs % 3600) // 60

        paused_str = ""
        if self._is_paused:
            paused_str = "PAUSED" if self._is_paused() else "RUNNING"

        status_info = {}
        if self._get_status:
            status_info = self._get_status()

        open_count = status_info.get("open_positions", 0)
        capital_deployed = status_info.get("capital_deployed", 0)
        total_trades = status_info.get("total_trades", 0)
        wins = status_info.get("wins", 0)
        losses = status_info.get("losses", 0)
        win_rate = status_info.get("win_rate", 0)
        total_pnl = status_info.get("total_pnl", 0)
        mode = status_info.get("mode", "PAPER")

        msg = (
            f"<b>GovBot Status</b>\n"
            f"State: <b>{paused_str}</b>\n"
            f"Mode: {mode}\n"
            f"Uptime: {hours}h {minutes}m\n\n"
            f"Open positions: {open_count}\n"
            f"Capital deployed: ${capital_deployed:,.2f}\n"
            f"Total trades: {total_trades}\n"
            f"Win/Loss: {wins}/{losses} ({win_rate:.0f}%)\n"
            f"Total P&L: ${total_pnl:+,.2f}"
        )
        await self.send(msg)

    async def _cmd_positions(self):
        """Handle /positions command: list open positions with P&L."""
        if not self._get_positions:
            await self.send("Position data not available")
            return

        positions_data = self._get_positions()
        positions = positions_data.get("positions", [])

        if not positions:
            await self.send("No open positions.")
            return

        lines = ["<b>Open Positions</b>\n"]
        total_unrealized = 0.0

        for pos in positions:
            ticker = pos.get("ticker", "?")
            side = pos.get("side", "?")
            entry = pos.get("entry_price", 0)
            shares = pos.get("shares", 0)
            current = pos.get("current_price", entry)
            catalyst = pos.get("catalyst", "")

            if side == "LONG":
                pnl = (current - entry) * shares
                pnl_pct = ((current - entry) / entry * 100) if entry else 0
            else:
                pnl = (entry - current) * shares
                pnl_pct = ((entry - current) / entry * 100) if entry else 0

            total_unrealized += pnl
            sign = "+" if pnl >= 0 else ""

            lines.append(
                f"<b>{ticker}</b> ({side})\n"
                f"  {shares} shares @ ${entry:.2f}\n"
                f"  P&L: {sign}${pnl:,.2f} ({sign}{pnl_pct:.1f}%)\n"
                f"  Catalyst: {catalyst}\n"
            )

        sign = "+" if total_unrealized >= 0 else ""
        lines.append(f"\nTotal unrealized: {sign}${total_unrealized:,.2f}")

        await self.send("\n".join(lines))

    async def _cmd_pause(self):
        """Handle /pause command."""
        if self._pause_callback:
            self._pause_callback()
            await self.send("Bot scanning <b>paused</b>. Use /resume to restart.")
        else:
            await self.send("Pause not available.")

    async def _cmd_resume(self):
        """Handle /resume command."""
        if self._resume_callback:
            self._resume_callback()
            await self.send("Bot scanning <b>resumed</b>.")
        else:
            await self.send("Resume not available.")

    async def _cmd_help(self):
        """Handle /help command."""
        msg = (
            "<b>GovBot Commands</b>\n\n"
            "/status -- bot status, uptime, trade stats\n"
            "/positions -- list open positions with P&L\n"
            "/pause -- pause signal scanning\n"
            "/resume -- resume signal scanning\n"
            "/help -- show this help message"
        )
        await self.send(msg)

    # --- Notification methods (unchanged interface) ---

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
        sign = "+" if pnl >= 0 else ""
        msg = (
            f"<b>Position Closed</b>\n"
            f"Ticker: <b>{ticker}</b>\n"
            f"Reason: {exit_reason}\n"
            f"P&L: {sign}${pnl:,.2f}"
        )
        await self.send(msg)

    async def error(self, message: str):
        await self.send(f"<b>Error</b>\n{message}")
