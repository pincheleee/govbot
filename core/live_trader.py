"""Order execution facade -- handles paper and live trading."""

import logging
from typing import Optional

from brokerages.base import BrokerageBase
from core.position_manager import PositionManager

logger = logging.getLogger(__name__)


class LiveTrader:
    def __init__(self, brokerage: BrokerageBase, position_manager: PositionManager, config):
        self.brokerage = brokerage
        self.pm = position_manager
        self.config = config

    async def execute_buy(
        self,
        ticker: str,
        confidence: int,
        edge: float,
        catalyst: str,
        reasoning: str,
        target_price: Optional[float] = None,
    ) -> bool:
        """Execute a BUY signal: size the position, place the order, track it."""
        # Get current price
        try:
            quote = await self.brokerage.get_quote(ticker)
            price = quote["price"]
        except Exception as e:
            logger.error(f"Failed to get quote for {ticker}: {e}")
            return False

        if price <= 0:
            logger.error(f"Invalid price for {ticker}: {price}")
            return False

        # Size the position
        max_size = self.config.initial_capital * self.config.max_position_pct
        shares = int(max_size / price)
        if shares < 1:
            logger.warning(f"Position too small for {ticker} at ${price:.2f}")
            return False

        size_usd = shares * price

        if not self.pm.can_open_position(size_usd):
            logger.warning(f"Cannot open position: capacity or capital limit reached")
            return False

        # Already have a position in this ticker?
        if self.pm.get_position_by_ticker(ticker):
            logger.warning(f"Already have open position in {ticker}, skipping")
            return False

        # Execute order
        if self.config.paper_mode:
            order_id = f"PAPER-{ticker}-{self.pm.stats['total_trades'] + self.pm.open_count + 1}"
            logger.info(f"[PAPER] BUY {shares} {ticker} @ ${price:.2f} (${size_usd:,.2f})")
        else:
            order_id = await self.brokerage.place_order(
                ticker=ticker, side="buy", qty=shares
            )
            if not order_id:
                logger.error(f"Order execution failed for {ticker}")
                return False

        # Track position
        self.pm.open_position(
            ticker=ticker,
            side="LONG",
            entry_price=price,
            shares=shares,
            edge=edge,
            confidence=confidence,
            catalyst=catalyst,
            reasoning=reasoning,
            order_id=order_id,
        )
        return True

    async def execute_short(
        self,
        ticker: str,
        confidence: int,
        edge: float,
        catalyst: str,
        reasoning: str,
    ) -> bool:
        """Execute a SHORT signal. Same flow as buy but opposite side."""
        try:
            quote = await self.brokerage.get_quote(ticker)
            price = quote["price"]
        except Exception as e:
            logger.error(f"Failed to get quote for {ticker}: {e}")
            return False

        max_size = self.config.initial_capital * self.config.max_position_pct
        shares = int(max_size / price)
        if shares < 1:
            return False

        size_usd = shares * price
        if not self.pm.can_open_position(size_usd):
            return False

        if self.pm.get_position_by_ticker(ticker):
            logger.warning(f"Already have open position in {ticker}")
            return False

        if self.config.paper_mode:
            order_id = f"PAPER-SHORT-{ticker}-{self.pm.stats['total_trades'] + self.pm.open_count + 1}"
            logger.info(f"[PAPER] SHORT {shares} {ticker} @ ${price:.2f}")
        else:
            order_id = await self.brokerage.place_order(
                ticker=ticker, side="sell", qty=shares
            )
            if not order_id:
                return False

        self.pm.open_position(
            ticker=ticker,
            side="SHORT",
            entry_price=price,
            shares=shares,
            edge=edge,
            confidence=confidence,
            catalyst=catalyst,
            reasoning=reasoning,
            order_id=order_id,
        )
        return True

    async def check_and_close_positions(self):
        """Check all open positions against risk rules and close if triggered."""
        for position in list(self.pm.open_positions):
            try:
                quote = await self.brokerage.get_quote(position.ticker)
                current_price = quote["price"]
            except Exception as e:
                logger.error(f"Failed to get price for {position.ticker}: {e}")
                continue

            exit_reason = self.pm.check_risk(position, current_price)
            if exit_reason:
                if self.config.paper_mode:
                    logger.info(
                        f"[PAPER] Closing {position.ticker}: {exit_reason} "
                        f"@ ${current_price:.2f}"
                    )
                else:
                    side = "sell" if position.side == "LONG" else "buy"
                    order_id = await self.brokerage.place_order(
                        ticker=position.ticker,
                        side=side,
                        qty=position.shares,
                    )
                    if not order_id:
                        logger.error(f"Failed to close {position.ticker}")
                        continue

                self.pm.close_position(position, current_price, exit_reason)
