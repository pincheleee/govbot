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
from core.sector_etf_mapper import SectorETFMapper
from ai.analyzer import GovSignalAnalyzer
from ai.ensemble import EnsembleRunner
from ai.news_fetcher import NewsFetcher
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
        self.news_fetcher = NewsFetcher()
        self.sector_etf_mapper = SectorETFMapper()
        self.notifier = TelegramNotifier(self.config.telegram_token, self.config.telegram_chat_id)

        # Register Telegram command callbacks
        self.notifier.register_callbacks(
            get_status=self._get_status,
            get_positions=self._get_positions,
            pause_callback=self._pause,
            resume_callback=self._resume,
            is_paused=lambda: self.paused,
        )

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

    # --- Telegram command callbacks ---

    def _get_status(self) -> dict:
        """Return status dict for /status command."""
        summary = self.position_manager.summary()
        summary["mode"] = "PAPER" if self.config.paper_mode else "LIVE"
        return summary

    def _get_positions(self) -> dict:
        """Return positions dict for /positions command."""
        positions = []
        for pos in self.position_manager.open_positions:
            positions.append({
                "ticker": pos.ticker,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "shares": pos.shares,
                "current_price": pos.high_water_mark,  # best available without live quote
                "catalyst": pos.catalyst,
            })
        return {"positions": positions}

    def _pause(self):
        """Pause scanning."""
        self.paused = True
        logger.info("Bot paused via Telegram command")

    def _resume(self):
        """Resume scanning."""
        self.paused = False
        logger.info("Bot resumed via Telegram command")

    # --- Main run loop ---

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

        # Start Telegram command polling
        await self.notifier.start_polling()

        # Verify brokerage connection
        try:
            account = await self.brokerage.get_account()
            logger.info(f"Brokerage connected. Equity: ${account['equity']:,.2f}")
        except Exception as e:
            logger.warning(f"Brokerage connection check failed: {e}")
            if not self.config.paper_mode:
                logger.error("Cannot run in LIVE mode without brokerage connection")
                await self.notifier.stop_polling()
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

        # Clean up
        await self.notifier.stop_polling()
        logger.info("GovBot stopped")

    async def _run_scan_cycle(self):
        """One full scan cycle: poll feeds -> resolve companies -> analyze -> trade."""
        cycle_start = datetime.utcnow()
        logger.info(f"--- Scan cycle starting at {cycle_start.isoformat()} ---")

        # Step 1: Check existing positions for risk exits
        await self._check_and_close_positions()

        # Step 2: Scan all data feeds
        signals = await self.scanner.scan_feeds()
        if not signals:
            logger.info("No signals this cycle")
            return

        # Step 3: Resolve company names to tickers (or ETFs for macro signals)
        resolved_signals = []
        for sig in signals:
            if self.sector_etf_mapper.is_macro_signal(sig.source):
                # Macro signal -- map sector to ETF instead of company resolver
                sector = sig.raw_data.get("affected_sector") or sig.raw_data.get("sector") or sig.company_name
                etf = self.sector_etf_mapper.resolve(sector)
                if etf:
                    sig.ticker = etf
                    resolved_signals.append(sig)
                    logger.info(f"Macro signal: {sig.source} sector '{sector}' -> {etf}")
                else:
                    logger.info(f"No ETF mapping for macro signal sector: {sector}")
            else:
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

        # Step 3.5: Market cap filter
        if self.config.min_market_cap > 0 or self.config.max_market_cap > 0:
            filtered = []
            for sig in resolved_signals:
                passes = await self.company_resolver.passes_market_cap_filter(
                    sig.ticker,
                    min_cap=self.config.min_market_cap,
                    max_cap=self.config.max_market_cap,
                )
                if passes:
                    filtered.append(sig)
                else:
                    logger.info(f"Filtered out {sig.ticker}: market cap outside range")
            resolved_signals = filtered

            if not resolved_signals:
                logger.info("No signals passed market cap filter")
                return

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

    async def _check_and_close_positions(self):
        """Wrapper around trader.check_and_close_positions that sends notifications."""
        for position in list(self.position_manager.open_positions):
            try:
                quote = await self.brokerage.get_quote(position.ticker)
                current_price = quote["price"]
            except Exception as e:
                logger.error(f"Failed to get price for {position.ticker}: {e}")
                continue

            exit_reason = self.position_manager.check_risk(position, current_price)
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

                # Calculate P&L before closing for notification
                if position.side == "LONG":
                    pnl = (current_price - position.entry_price) * position.shares
                else:
                    pnl = (position.entry_price - current_price) * position.shares

                self.position_manager.close_position(position, current_price, exit_reason)

                # Send position_closed notification
                await self.notifier.position_closed(
                    ticker=position.ticker,
                    exit_reason=exit_reason,
                    pnl=pnl,
                )

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

        # Fetch recent news to enrich AI context
        news_context = ""
        try:
            news_items = await self.news_fetcher.fetch(ticker)
            if news_items:
                headlines = [f"- {n['title']} ({n['source']})" for n in news_items[:8]]
                news_context = "\n\nRECENT NEWS:\n" + "\n".join(headlines)
                logger.info(f"News: {len(news_items)} items fetched for {ticker}")
        except Exception as e:
            logger.debug(f"News fetch failed for {ticker}: {e}")

        enriched_details = sig.details + news_context

        # Run AI analysis (ensemble if multiple providers configured)
        if len(self.config.ai_ensemble_providers) > 1:
            consensus = await self.ensemble.run(
                ticker=ticker,
                catalyst_type=sig.source,
                catalyst_details=enriched_details,
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
                catalyst_details=enriched_details,
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
