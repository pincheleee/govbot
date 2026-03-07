"""GovBot -- AI-assisted equity trading bot exploiting government data publication lag."""

import asyncio
import logging
import signal
import sys
import csv
import os
from datetime import datetime

from utils.config import Config
from utils.notifier import TelegramNotifier
from core.signal_scanner import SignalScanner
from core.company_resolver import CompanyResolver
from core.position_manager import PositionManager
from core.live_trader import LiveTrader
from ai.analyzer import GovSignalAnalyzer
from ai.ensemble import EnsembleRunner
from brokerages.alpaca import AlpacaBrokerage

logger = logging.getLogger("govbot")


class GovBot:
    def __init__(self):
        self.config = Config()
        self._setup_logging()
        self.running = False
        self.paused = False

        # Brokerage
        self.brokerage = AlpacaBrokerage(
            api_key=self.config.alpaca_api_key,
            secret_key=self.config.alpaca_secret_key,
            paper=self.config.alpaca_paper,
        )

        # Core components
        alpaca_headers = {
            "APCA-API-KEY-ID": self.config.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.config.alpaca_secret_key,
        }
        self.company_resolver = CompanyResolver(self.config.data_dir, alpaca_headers)
        self.position_manager = PositionManager(self.config.data_dir, self.config)
        self.scanner = SignalScanner(self.config)
        self.trader = LiveTrader(self.brokerage, self.position_manager, self.config)
        self.analyzer = GovSignalAnalyzer(self.config)
        self.ensemble = EnsembleRunner(self.config)
        self.notifier = TelegramNotifier(self.config.telegram_token, self.config.telegram_chat_id)

    def _setup_logging(self):
        log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        handlers = [logging.StreamHandler(sys.stdout)]
        if self.config.log_file:
            os.makedirs(os.path.dirname(self.config.log_file) if os.path.dirname(self.config.log_file) else ".", exist_ok=True)
            handlers.append(logging.FileHandler(self.config.log_file))

        logging.basicConfig(
            level=getattr(logging, self.config.log_level.upper(), logging.INFO),
            format=log_format,
            handlers=handlers,
        )

    async def run(self):
        """Main run loop."""
        self.running = True
        mode = "PAPER" if self.config.paper_mode else "LIVE"
        logger.info(f"GovBot starting in {mode} mode")
        logger.info(f"Scan interval: {self.config.scan_interval_minutes} minutes")
        logger.info(f"Min confidence: {self.config.min_confidence}, Min edge: {self.config.min_edge}")

        await self.notifier.send(
            f"<b>GovBot Started</b>\n"
            f"Mode: {mode}\n"
            f"Capital: ${self.config.initial_capital:,.2f}\n"
            f"Scan interval: {self.config.scan_interval_minutes}m"
        )

        # Verify brokerage connection
        try:
            account = await self.brokerage.get_account()
            logger.info(f"Brokerage connected. Equity: ${account['equity']:,.2f}")
        except Exception as e:
            logger.warning(f"Brokerage connection check failed: {e}")
            if not self.config.paper_mode:
                logger.error("Cannot run in LIVE mode without brokerage connection")
                return

        while self.running:
            try:
                if not self.paused:
                    await self._run_scan_cycle()
                else:
                    logger.debug("Bot is paused, skipping scan")

                # Wait for next cycle
                await asyncio.sleep(self.config.scan_interval_minutes * 60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scan cycle error: {e}", exc_info=True)
                await self.notifier.error(f"Scan cycle error: {e}")
                await asyncio.sleep(60)  # Brief pause on error

        logger.info("GovBot stopped")

    async def _run_scan_cycle(self):
        """One full scan cycle: poll feeds -> resolve companies -> analyze -> trade."""
        cycle_start = datetime.utcnow()
        logger.info(f"--- Scan cycle starting at {cycle_start.isoformat()} ---")

        # Step 1: Check existing positions for risk exits
        await self.trader.check_and_close_positions()

        # Step 2: Scan all data feeds
        signals = await self.scanner.scan_feeds()
        if not signals:
            logger.info("No signals this cycle")
            return

        # Step 3: Resolve company names to tickers
        resolved_signals = []
        for sig in signals:
            ticker = self.company_resolver.resolve(sig.company_name)
            if ticker:
                sig.ticker = ticker
                resolved_signals.append(sig)
            else:
                logger.info(f"Could not resolve company: {sig.company_name}")

        if not resolved_signals:
            logger.info("No signals with resolved tickers this cycle")
            return

        logger.info(f"Resolved {len(resolved_signals)}/{len(signals)} signals to tickers")

        # Step 4: Get market data and run AI analysis
        for sig in resolved_signals:
            try:
                await self._process_signal(sig)
            except Exception as e:
                logger.error(f"Error processing signal for {sig.ticker}: {e}")

        # Log signals to CSV
        self._log_signals(resolved_signals)

        elapsed = (datetime.utcnow() - cycle_start).total_seconds()
        logger.info(f"--- Scan cycle complete in {elapsed:.1f}s ---")

    async def _process_signal(self, sig):
        """Process a single resolved signal: get price, analyze, maybe trade."""
        ticker = sig.ticker

        # Skip if we already have a position
        if self.position_manager.get_position_by_ticker(ticker):
            logger.info(f"Already have position in {ticker}, skipping signal")
            return

        # Get current market data
        try:
            snapshot = await self.brokerage.get_snapshot(ticker)
            current_price = snapshot["price"]
            daily_change = snapshot["daily_change_pct"]
            volume = snapshot["volume"]
        except Exception as e:
            logger.error(f"Failed to get market data for {ticker}: {e}")
            return

        if current_price <= 0:
            return

        # Run AI analysis (ensemble if multiple providers configured)
        if len(self.config.ai_ensemble_providers) > 1:
            consensus = await self.ensemble.run(
                ticker=ticker,
                catalyst_type=sig.source,
                catalyst_details=sig.details,
                current_price=current_price,
                daily_change=daily_change,
                volume=volume,
            )
            action = consensus.action
            confidence = consensus.confidence
            edge = consensus.edge
            reasoning = consensus.reasoning
        else:
            result = await self.analyzer.analyze(
                ticker=ticker,
                catalyst_type=sig.source,
                catalyst_details=sig.details,
                current_price=current_price,
                daily_change=daily_change,
                volume=volume,
            )
            action = result.action
            confidence = result.confidence
            edge = result.edge
            reasoning = result.reasoning

        logger.info(
            f"AI: {action} {ticker} | confidence={confidence} edge={edge:.3f} | {reasoning[:100]}"
        )

        # Notify on signal detection
        await self.notifier.signal_detected(sig.source, ticker, confidence, reasoning[:200])

        # Filter: must meet thresholds
        if action == "SKIP":
            return
        if confidence < self.config.min_confidence:
            logger.info(f"Confidence {confidence} below threshold {self.config.min_confidence}")
            return
        if edge < self.config.min_edge:
            logger.info(f"Edge {edge:.3f} below threshold {self.config.min_edge}")
            return

        # Execute trade
        if action == "BUY":
            success = await self.trader.execute_buy(
                ticker=ticker,
                confidence=confidence,
                edge=edge,
                catalyst=sig.source,
                reasoning=reasoning,
            )
        elif action == "SHORT":
            success = await self.trader.execute_short(
                ticker=ticker,
                confidence=confidence,
                edge=edge,
                catalyst=sig.source,
                reasoning=reasoning,
            )
        else:
            return

        if success:
            await self.notifier.position_opened(
                ticker=ticker,
                side="LONG" if action == "BUY" else "SHORT",
                shares=int(self.config.initial_capital * self.config.max_position_pct / current_price),
                price=current_price,
                reasoning=reasoning[:300],
            )

    def _log_signals(self, signals):
        """Append all signals (including skipped) to signals.csv for backtesting."""
        signals_csv = os.path.join(self.config.data_dir, "signals.csv")
        file_exists = os.path.exists(signals_csv)
        with open(signals_csv, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp", "source", "ticker", "company", "title",
                    "dollar_value", "url"
                ])
            for sig in signals:
                writer.writerow([
                    datetime.utcnow().isoformat(),
                    sig.source,
                    sig.ticker,
                    sig.company_name,
                    sig.title[:200],
                    sig.dollar_value,
                    sig.url,
                ])


def main():
    bot = GovBot()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_shutdown(signum, frame):
        logger.info("Shutdown signal received")
        bot.running = False

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
