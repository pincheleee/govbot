# Dev Log

## 2026-03-08 -- Session 2: Phase 2+3 Implementation (All Feeds Live)

Fleshed out all stubs and incomplete features. Every data feed now has real API integration. No new dependencies added -- everything uses aiohttp and stdlib XML parsing.

**What got built/fixed:**

1. **SEC EDGAR feed** -- Polls EDGAR EFTS full-text search API for material filings (8-K current events, Form 4 insider trades, SC 13D activist investors, 10-K/A and 10-Q/A amendments). Rate-limited to 8 req/sec via the existing RateLimiter class. Requires proper User-Agent with real email per SEC policy. Falls back to alternate search endpoint if primary returns nothing.

2. **Federal Register feed** -- Polls federalregister.gov/api/v1 for Rules and Presidential Documents. Filters for economic significance three ways: (a) the API's own `significant` flag, (b) keyword matching against 25+ economic terms in title/abstract, (c) agency slug mapping (DOD, FDA, SEC, FCC, etc. each tagged with their market sector). Paginated up to 5 pages.

3. **Congress.gov feed** -- Polls api.congress.gov/v3 for HR/S/HJRes/SJRes bills with recent action. Classifies bills into 8 sectors via keyword matching (defense, healthcare, tech, energy, finance, infrastructure, trade, appropriations). Filters for significant actions (passed house/senate, signed, floor consideration, etc.). Paginated up to 3 pages.

4. **News fetcher** -- RSS-based, zero API keys needed. Pulls from 5 free feeds (Yahoo Finance, Reuters, Google News Business, Defense News, Federal News Network) plus a ticker-specific Google News search. Scores each item for relevance based on ticker mentions and 15+ government/defense keywords. 10-minute in-memory cache to avoid hammering feeds.

5. **Telegram commands** -- Added long-poll command listener to TelegramNotifier. Five commands: /status (uptime, mode, stats, P&L), /positions (open positions with unrealized P&L per position), /pause, /resume, /help. Uses callback registration pattern so the notifier doesn't import GovBot.

6. **position_closed() notification fix** -- Moved the position closing loop from LiveTrader into GovBot._check_and_close_positions() so the notifier.position_closed() call fires after every close. Previously the LiveTrader closed positions silently.

7. **SAM.gov pagination** -- Added offset-based pagination loop with safety limit (10 pages = 1000 results max). Previously fetched only the first 100 results and ignored the rest.

8. **Market cap filtering** -- CompanyResolver.get_financials() now fetches real data from Yahoo Finance (v8 chart API with quoteSummary fallback, then Alpaca fallback). New passes_market_cap_filter() method enforces MIN_MARKET_CAP/MAX_MARKET_CAP from config. Integrated into main scan cycle as Step 3.5 between company resolution and AI analysis. Financials are cached per-ticker.

9. **Signal scanner converters** -- All three stub converters (_scan_edgar, _scan_fed_register, _scan_congress) now produce real Signal objects with proper source labels and structured details for the AI analyzer.

**Architecture notes:**
- Federal Register and Congress signals use agency/sector names as company_name, which the fuzzy resolver won't match to tickers. These are macro signals -- the AI analyzer gets the sector context and can still provide directional guidance. Future improvement: add a sector-to-ETF mapper.
- EDGAR filings include tickers from the EFTS response when available, which gets passed through to the signal's ticker field (bypasses company resolver).
- No new pip dependencies. RSS parsing uses stdlib xml.etree.ElementTree. Yahoo Finance uses their public JSON endpoints.

**Next steps:**
- Get SEC_USER_AGENT set to a real email address (SEC requirement)
- Get CONGRESS_API_KEY from api.congress.gov
- Test each feed in isolation to verify response parsing
- Add sector-to-ETF mapping for macro signals (FedReg, Congress)
- Consider adding the news fetcher output to the AI prompt for richer context

## 2026-03-04 -- Session 1: Project Scaffold (Phase 1)

Built the full Phase 1 scaffold for govbot. The architecture mirrors polybot's three-agent pipeline but adapted for equities:

**What got built:**
- Complete project structure with all modules, feeds, brokerages, AI, and utils
- SAM.gov feed poller -- pulls contract awards above configurable threshold ($50M default), parses award amounts, extracts awardee names
- Alpaca brokerage adapter -- full REST API coverage: account info, positions, orders (market/limit), quotes, snapshots, historical bars. Supports both paper and live endpoints
- Company resolver -- fuzzy matching (thefuzz) of government contractor names to tickers. Seeded with 50+ major contractors (defense, tech, healthcare, engineering). Auto-learns new name variants and persists to company_map.json
- Position manager -- tracks open/closed positions, enforces risk rules (stop loss 5%, take profit 8%, trailing stop 2% after 3% gain, 72h time limit), logs trades to CSV
- Live trader -- order execution facade that handles paper mode logging and live order placement, position sizing (max 10% per position)
- AI analyzer -- prompts tuned for gov data signals, asks the right questions (materiality, market pricing, expected magnitude). Supports Anthropic, DeepSeek, OpenAI
- Ensemble runner -- parallel multi-model consensus, skips on disagreement
- Telegram notifier -- signal detection, position open/close, error alerts
- Main scan loop -- async, configurable interval, full pipeline: scan -> resolve -> analyze -> trade

**Stubs for later phases:**
- SEC EDGAR feed (Phase 2)
- Federal Register feed (Phase 3)
- Congress.gov feed (Phase 3)
- News fetcher (Phase 2)

**Next steps:**
- Get SAM.gov API key from api.data.gov
- Get Alpaca paper trading API keys
- Wire up .env and test the scan loop end-to-end
- Test company resolver against real SAM.gov awardee names
