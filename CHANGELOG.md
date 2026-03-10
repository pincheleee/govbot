# Changelog

## [0.4.0] - 2026-03-10

### Added
- **QuiverQuant Congress trading feed** (`feeds/congress_trading.py`): polls QuiverQuant API for stock trades by U.S. Congress members. Focuses on purchases as bullish signals. Parses politician name, party, chamber, ticker, transaction type, STOCK Act amount range, transaction date, and disclosure date. Filters for significant trades (>$15K, configurable via `CONGRESS_TRADE_MIN_AMOUNT`). Three signal tiers:
  - `CONGRESS_TRADE_BUY` -- single significant purchase by a Congress member
  - `CONGRESS_TRADE_CLUSTER` -- multiple members buying the same stock within 14 days (with bipartisan detection)
  - `CONGRESS_TRADE_COMMITTEE` -- committee-relevant purchase (e.g., Armed Services member buying defense stocks). Seed list of 18 high-profile traders and their committee assignments. 80+ ticker-to-sector mappings for committee relevance checks.
- Disclosure lag tracking: calculates days between transaction and disclosure date. Late disclosures (>30 days) are flagged in signal details.
- Deduplication: tracks seen trades to avoid re-signaling on subsequent polls.
- `QUIVERQUANT_API_TOKEN` config var and `.env.example` entry.

### Changed
- **Signal scanner** (`core/signal_scanner.py`): integrated Congress trading feed alongside existing feeds. New `_scan_congress_trading()` method converts trade signals into unified Signal objects with full details for AI analysis.
- **Main loop** (`main.py`): signals with pre-resolved tickers (Congress trading, EDGAR with tickers) now skip the company resolver step, avoiding failed fuzzy matches on ticker symbols.

## [0.3.0] - 2026-03-10

### Added
- **Sector-to-ETF mapper** (`core/sector_etf_mapper.py`): maps macro signals (FedReg, Congress) to tradeable sector ETFs. Covers defense (ITA), healthcare (XLV), tech (XLK), energy (XLE), finance (XLF), infrastructure (PAVE), trade (EFA), telecom (XLC), and broad market (SPY). Case-insensitive matching with alternates list.
- **News-enriched AI analysis** (`main.py`): news fetcher output (up to 8 headlines) is now injected into the AI prompt as context, allowing the analyzer to detect already-priced-in signals.
- **Feed test script** (`scripts/test_feeds.py`): standalone test harness for each data feed (SAM, EDGAR, FedReg, Congress, news, ETF mapper). Supports testing individual feeds or all at once.

### Changed
- **Macro signal routing** (`main.py`): FedReg and Congress signals now route through the ETF mapper instead of the company resolver, which previously failed to match sector names to tickers.
- **AI prompt** (`ai/analyzer.py`): added instruction to check if recent news headlines already cover the signal (priced-in detection).

## [0.2.0] - 2026-03-08

### Added
- **SEC EDGAR feed** (`feeds/sec_edgar.py`): real EDGAR EFTS full-text search API integration. Polls for 8-K, Form 4, SC 13D, 10-K/A, 10-Q/A filings. Respects EDGAR rate limit (8 req/sec with User-Agent). Parses hits into EdgarFiling dataclass with fallback search endpoint.
- **Federal Register feed** (`feeds/federal_register.py`): real Federal Register API integration. Polls for Rules and Presidential Documents. Filters for economic significance via keyword matching and agency relevance mapping (DOD, FDA, SEC, FCC, etc.). Supports pagination. Parses into FedRegDocument with sector classification.
- **Congress.gov feed** (`feeds/congress.py`): real Congress API integration (requires CONGRESS_API_KEY). Polls for HR, S, HJRes, SJRes bills with recent action. Filters for market-relevant sectors (defense, healthcare, tech, energy, finance, infrastructure, trade, appropriations). Detects significant actions (passed, floor vote, signed). Supports pagination.
- **News fetcher** (`ai/news_fetcher.py`): RSS-based financial news fetcher. Pulls from Yahoo Finance, Reuters, Google News, Defense News, Federal News Network. Ticker-specific Google News search. Relevance scoring based on keyword matching and ticker mentions. In-memory cache with 10m TTL.
- **Telegram commands** (`utils/notifier.py`): added command polling loop with long-poll. Commands: `/status` (uptime, mode, trade stats, P&L), `/positions` (open positions with unrealized P&L), `/pause` (pause scanning), `/resume` (resume scanning), `/help`. Callback registration pattern from GovBot.
- **Market cap filtering** (`core/company_resolver.py`): `get_financials()` fetches market cap, sector, revenue from Yahoo Finance v8 chart API with quoteSummary fallback, then Alpaca fallback. `passes_market_cap_filter()` enforces MIN_MARKET_CAP/MAX_MARKET_CAP. Financials cache to avoid repeated API calls.
- **Signal scanner EDGAR/FedReg/Congress converters** (`core/signal_scanner.py`): all three stub converters now produce real Signal objects with proper source labels (SEC_8K, SEC_FORM4, SEC_13D, FED_REGISTER, CONGRESS).

### Fixed
- **position_closed() notification**: `main.py` now calls `notifier.position_closed()` when positions are closed during risk checks. Moved position closing logic from `live_trader.check_and_close_positions()` into `main._check_and_close_positions()` so notifications fire.
- **SAM.gov pagination** (`feeds/sam_gov.py`): added offset-based pagination loop (max 10 pages / 1000 results). Previously hardcoded limit=100 with no pagination. Now logs total record count and pages fetched.
- **Market cap filter integration** in main scan cycle: Step 3.5 filters resolved signals through `passes_market_cap_filter()` before AI analysis.

## [0.1.0] - 2026-03-04

### Added
- Initial project scaffold (Phase 1)
- `main.py` -- async scan loop with configurable interval, signal processing pipeline
- `brokerages/alpaca.py` -- Alpaca REST adapter (paper + live), quotes, snapshots, orders
- `brokerages/base.py` -- abstract brokerage interface
- `feeds/sam_gov.py` -- SAM.gov federal contract awards poller with amount filtering
- `feeds/sec_edgar.py` -- EDGAR stub (Phase 2)
- `feeds/federal_register.py` -- Federal Register stub (Phase 3)
- `feeds/congress.py` -- Congress.gov stub (Phase 3)
- `core/signal_scanner.py` -- unified signal scanner across all feeds
- `core/company_resolver.py` -- fuzzy company name to ticker mapping with 50+ seeded contractors
- `core/position_manager.py` -- position tracking, risk rules (stop loss, take profit, trailing stop, time limit)
- `core/live_trader.py` -- order execution facade for paper and live modes
- `ai/analyzer.py` -- AI analysis prompt for gov signals, multi-provider support (Anthropic, DeepSeek, OpenAI)
- `ai/ensemble.py` -- multi-model consensus runner
- `ai/news_fetcher.py` -- news enrichment stub (Phase 2)
- `utils/config.py` -- config dataclass from .env
- `utils/notifier.py` -- Telegram notifications (signals, positions, errors)
- `utils/utils.py` -- retry decorator, rate limiter, formatting helpers
- `data/company_map.json` -- company name to ticker seed data
- Docker + docker-compose setup
- `deploy.sh` -- rsync deploy to Pi
- `.env.example` with all configuration options
- `CLAUDE.md` project instructions
