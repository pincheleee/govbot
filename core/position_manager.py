"""Position tracking and risk management."""

import json
import csv
import logging
import os
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    shares: int
    size_usd: float
    opened_at: str  # ISO format
    edge_at_entry: float
    confidence_at_entry: int
    catalyst: str  # "SAM_CONTRACT" | "SEC_8K" | "FED_REGISTER" | "CONGRESS"
    reasoning: str
    status: str = "OPEN"  # "OPEN" | "CLOSED"
    order_id: str = ""
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "STOP_LOSS" | "TAKE_PROFIT" | "TRAILING_STOP" | "TIME_LIMIT" | "THESIS_BREAK" | "MANUAL"
    closed_at: Optional[str] = None
    pnl: float = 0.0
    high_water_mark: float = 0.0  # For trailing stop


class PositionManager:
    def __init__(self, data_dir: str, config):
        self.data_dir = data_dir
        self.config = config
        self.portfolio_path = os.path.join(data_dir, "portfolio.json")
        self.trades_csv = os.path.join(data_dir, "trades.csv")
        self.positions: List[Position] = []
        self.closed: List[Position] = []
        self.stats = {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
        self._load()

    def _load(self):
        os.makedirs(self.data_dir, exist_ok=True)
        if os.path.exists(self.portfolio_path):
            with open(self.portfolio_path, "r") as f:
                data = json.load(f)
            self.positions = [Position(**p) for p in data.get("positions", [])]
            self.closed = [Position(**p) for p in data.get("closed", [])]
            self.stats = data.get("stats", self.stats)
            logger.info(f"Loaded {len(self.positions)} open, {len(self.closed)} closed positions")

    def _save(self):
        data = {
            "positions": [asdict(p) for p in self.positions],
            "closed": [asdict(p) for p in self.closed],
            "stats": self.stats,
        }
        with open(self.portfolio_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _log_trade(self, position: Position):
        file_exists = os.path.exists(self.trades_csv)
        with open(self.trades_csv, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "ticker", "side", "catalyst", "entry_price", "exit_price",
                    "shares", "pnl", "exit_reason", "confidence", "edge",
                    "opened_at", "closed_at", "reasoning"
                ])
            writer.writerow([
                position.ticker, position.side, position.catalyst,
                position.entry_price, position.exit_price,
                position.shares, position.pnl, position.exit_reason,
                position.confidence_at_entry, position.edge_at_entry,
                position.opened_at, position.closed_at, position.reasoning[:200]
            ])

    @property
    def open_positions(self) -> List[Position]:
        return [p for p in self.positions if p.status == "OPEN"]

    @property
    def open_count(self) -> int:
        return len(self.open_positions)

    @property
    def capital_deployed(self) -> float:
        return sum(p.size_usd for p in self.open_positions)

    def can_open_position(self, size_usd: float) -> bool:
        if self.open_count >= self.config.max_concurrent_positions:
            return False
        total = self.capital_deployed + size_usd
        if total > self.config.initial_capital:
            return False
        if size_usd > self.config.initial_capital * self.config.max_position_pct:
            return False
        return True

    def open_position(
        self,
        ticker: str,
        side: str,
        entry_price: float,
        shares: int,
        edge: float,
        confidence: int,
        catalyst: str,
        reasoning: str,
        order_id: str = "",
    ) -> Position:
        pos = Position(
            ticker=ticker,
            side=side,
            entry_price=entry_price,
            shares=shares,
            size_usd=entry_price * shares,
            opened_at=datetime.utcnow().isoformat(),
            edge_at_entry=edge,
            confidence_at_entry=confidence,
            catalyst=catalyst,
            reasoning=reasoning,
            order_id=order_id,
            high_water_mark=entry_price,
        )
        self.positions.append(pos)
        self._save()
        logger.info(f"Opened {side} {shares} {ticker} @ ${entry_price:.2f}")
        return pos

    def close_position(self, position: Position, exit_price: float, exit_reason: str):
        if position.side == "LONG":
            position.pnl = (exit_price - position.entry_price) * position.shares
        else:
            position.pnl = (position.entry_price - exit_price) * position.shares

        position.exit_price = exit_price
        position.exit_reason = exit_reason
        position.closed_at = datetime.utcnow().isoformat()
        position.status = "CLOSED"

        self.positions.remove(position)
        self.closed.append(position)

        self.stats["total_trades"] += 1
        self.stats["total_pnl"] += position.pnl
        if position.pnl >= 0:
            self.stats["wins"] += 1
        else:
            self.stats["losses"] += 1

        self._log_trade(position)
        self._save()
        logger.info(
            f"Closed {position.ticker} ({exit_reason}): "
            f"P&L ${position.pnl:+.2f}"
        )

    def check_risk(self, position: Position, current_price: float) -> Optional[str]:
        """Check if a position should be closed due to risk rules. Returns exit_reason or None."""
        if position.side == "LONG":
            pnl_pct = (current_price - position.entry_price) / position.entry_price
        else:
            pnl_pct = (position.entry_price - current_price) / position.entry_price

        # Stop loss
        if pnl_pct <= -self.config.stop_loss_pct:
            return "STOP_LOSS"

        # Take profit
        if pnl_pct >= self.config.take_profit_pct:
            return "TAKE_PROFIT"

        # Trailing stop
        if position.side == "LONG":
            if current_price > position.high_water_mark:
                position.high_water_mark = current_price
            if position.high_water_mark > position.entry_price * (1 + self.config.trailing_stop_activation_pct):
                drawdown = (position.high_water_mark - current_price) / position.high_water_mark
                if drawdown >= self.config.trailing_stop_pct:
                    return "TRAILING_STOP"
        else:
            if current_price < position.high_water_mark or position.high_water_mark == position.entry_price:
                position.high_water_mark = current_price
            if position.high_water_mark < position.entry_price * (1 - self.config.trailing_stop_activation_pct):
                drawup = (current_price - position.high_water_mark) / position.high_water_mark
                if drawup >= self.config.trailing_stop_pct:
                    return "TRAILING_STOP"

        # Time limit
        opened = datetime.fromisoformat(position.opened_at)
        hours_held = (datetime.utcnow() - opened).total_seconds() / 3600
        if hours_held >= self.config.max_hold_hours:
            return "TIME_LIMIT"

        return None

    def get_position_by_ticker(self, ticker: str) -> Optional[Position]:
        for p in self.open_positions:
            if p.ticker == ticker:
                return p
        return None

    def summary(self) -> dict:
        return {
            "open_positions": self.open_count,
            "capital_deployed": self.capital_deployed,
            "total_trades": self.stats["total_trades"],
            "wins": self.stats["wins"],
            "losses": self.stats["losses"],
            "win_rate": (self.stats["wins"] / self.stats["total_trades"] * 100)
            if self.stats["total_trades"] > 0
            else 0,
            "total_pnl": self.stats["total_pnl"],
        }
