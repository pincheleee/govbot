# GovBot — Project Prompt

## What This Is

An AI-assisted equity/options trading bot that exploits the information lag between government data publications and stock price reactions. Federal agencies announce slowly and loudly — a SAM.gov contract award, an SEC filing, a Federal Register rule — and the market takes hours to days to fully price it in. This bot reads those feeds, identifies affected publicly traded companies, and trades the lag.

This is a direct descendant of [polybot](../polybot), an AI-assisted prediction market bot. The entire async pipeline, AI ensemble, risk management, position manager, Telegram integration, and deployment infrastructure carry over. The domain changes from binary prediction markets to equities/options.

---

## Architecture (Inherited from Polybot)

### Three-Agent Pipeline

The scan loop runs on a configurable interval (default 15 minutes). Three async agents pass work through queues:

```
Agent Alpha (Scout)     → filters data feeds for actionable signals
Agent Beta  (Analyst)   → enriches signals with context (financials, news, filings)
Agent Gamma (Judge)     → runs AI analysis, outputs BUY/SHORT/SKIP with confidence + edge
```

Each agent runs as an `asyncio.Task` consuming from and producing to `asyncio.Queue` instances. Alpha is fast (filtering), Beta is I/O-bound (parallel API fetches via `asyncio.gather`), Gamma is LLM-bound (one call per signal).

### Core Loop (main.py)

```python
async def run_scan_cycle():
    # 1. Alpha: pull latest from all data feeds
    signals = await scanner.scan_feeds()

    # 2. Beta: enrich top-N signals with company data + context
    enriched = await asyncio.gather(*[
        enrich_signal(s) for s in signals[:MAX_CONCURRENT]
    ])

    # 3. Gamma: AI analysis on each enriched signal
    analyses = await asyncio.gather(*[
        analyzer.analyze(e) for e in enriched
    ])

    # 4. Filter to tradeable, execute or log
    for a in analyses:
        if a.action != "SKIP" and a.confidence >= MIN_CONFIDENCE and a.edge >= MIN_EDGE:
            await trader.execute(a)
```

### Position Manager (reuse from polybot)

```python
@dataclass
class Position:
    ticker: str
    side: str           # "LONG" or "SHORT"
    entry_price: float
    shares: int
    size_usd: float
    opened_at: datetime
    edge_at_entry: float
    confidence_at_entry: int
    catalyst: str       # "SAM_CONTRACT" | "SEC_8K" | "FED_REGISTER" | "CONGRESS"
    reasoning: str
    status: str         # "OPEN" | "CLOSED"
    exit_price: float = None
    exit_reason: str = None  # "STOP_LOSS" | "TAKE_PROFIT" | "TRAILING_STOP" | "TIME_LIMIT" | "THESIS_BREAK"
    pnl: float = 0.0
```

Risk rules (all configurable via .env):
- `STOP_LOSS_PCT`: default 5% (tighter than polybot's prediction markets)
- `TAKE_PROFIT_PCT`: default 8%
- `TRAILING_STOP_PCT`: activates after 3% gain, trails by 2%
- `MAX_HOLD_HOURS`: default 72 (gov catalyst trades are short-duration)
- `MAX_POSITION_PCT`: max 10% of capital per position
- `MAX_CONCURRENT_POSITIONS`: default 5
- `PAPER_MODE`: default true — simulate all orders, log what would have happened

State files (persisted in `data/`):
- `portfolio.json` — open positions + closed history + P&L stats
- `trades.csv` — append-only trade log
- `signals.csv` — all signals seen, even if skipped (for backtesting)

### AI Ensemble (reuse from polybot)

Multi-provider LLM consensus. Each signal is analyzed by 2+ models. Trade only on agreement.

```python
class EnsembleRunner:
    providers: List[str]  # ["anthropic", "deepseek", "openai"]
    policy: ConsensusPolicy  # UNANIMOUS or MAJORITY

    async def run(self, prompt: str) -> ConsensusResult:
        results = await asyncio.gather(*[
            self._call_provider(p, prompt) for p in self.providers
        ])
        return self._apply_consensus(results)
```

Models:
- Anthropic Claude (claude-sonnet-4-20250514) — primary
- DeepSeek (deepseek-chat) — secondary, cheap, fast
- OpenAI (gpt-4o) — tiebreaker

### Brokerage Interface

```python
class BrokerageBase(ABC):
    @abstractmethod
    async def get_account(self) -> dict: ...

    @abstractmethod
    async def get_positions(self) -> List[dict]: ...

    @abstractmethod
    async def place_order(
        self, ticker: str, side: str, qty: int,
        order_type: str = "market", limit_price: float = None,
        time_in_force: str = "day"
    ) -> Optional[str]: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_quote(self, ticker: str) -> dict: ...

    @abstractmethod
    async def get_bars(self, ticker: str, timeframe: str, limit: int) -> List[dict]: ...
```

Start with **Alpaca** (free, paper trading built in, clean REST API, no minimum balance). Add Tradier or IBKR later if needed.

### Telegram Bot (reuse from polybot)

Commands:
- `/status` — capital, open positions, daily P&L
- `/positions` — list open positions with entry price, current price, unrealized P&L
- `/close N` — manually close position N
- `/pause` / `/resume` — halt/restart scanning
- `/set KEY VALUE` — adjust config at runtime (e.g., `/set STOP_LOSS_PCT 0.03`)

Alerts:
- Signal detected (catalyst type, ticker, confidence)
- Position opened (ticker, side, size, entry price, reasoning)
- Position closed (exit reason, P&L)
- Stop loss / take profit triggered

---

## Data Feeds — The Edge

Government data is public, structured, and slow to be priced in. The bot monitors four primary feeds:

### 1. SAM.gov — Federal Contract Awards ("the gem")

**What:** Every federal contract award is published on SAM.gov. When Lockheed Martin wins a $2B defense contract, the award notice appears on SAM.gov before the stock moves.

**Endpoint:** `https://api.sam.gov/opportunities/v2/search` (free API key required)

**Fields that matter:**
- `awardee` — company name (must be mapped to ticker)
- `award.amount` — dollar value (filter: >$50M for signal)
- `postedDate` — when published (freshness = edge)
- `naicsCode` — industry classification
- `department` — awarding agency (DoD, HHS, DOE, etc.)

**Company mapping:** Maintain a lookup table of major government contractors to tickers:
```
"LOCKHEED MARTIN" → LMT
"RAYTHEON" → RTX
"NORTHROP GRUMMAN" → NOC
"GENERAL DYNAMICS" → GD
"BOOZ ALLEN" → BAH
"LEIDOS" → LDOS
"PALANTIR" → PLTR
```
Fuzzy match company names. Build the mapping over time.

**Signal logic:**
- Award >$100M to a company where award > 5% of market cap → strong signal
- Award in new business area (different NAICS than usual) → expansion signal
- Multiple awards in short window → contract clustering signal
- Award to small-cap (market cap <$5B) → higher impact per dollar

### 2. SEC EDGAR — Material Events

**What:** Public companies must file material events within 4 business days. These filings often contain price-moving information that takes time to propagate.

**Key form types:**
- **8-K** — Material events (CEO departure, M&A, contract wins, restatements). Most time-sensitive.
- **4** — Insider trades (director/officer buys/sells). Insider buys > $100k are strong signals.
- **13F** — Institutional holdings (quarterly). Shows what funds accumulated.
- **SC 13D/G** — Activist investor stakes (>5% ownership). Immediate signal.

**Endpoint:** EDGAR full-text search API (free, rate-limited to 10 req/sec)
- `https://efts.sec.gov/LATEST/search-index?q=...&dateRange=...&forms=8-K`

**Signal logic:**
- 8-K with "contract" or "award" in text → cross-reference with SAM.gov
- Form 4 insider buy cluster (3+ insiders buying within 2 weeks) → strong bullish
- SC 13D new filing → activist accumulation, expect price move
- 8-K "restatement" or "material weakness" → bearish signal

### 3. Federal Register — Regulations & Executive Orders

**What:** New regulations, executive orders, and proposed rules. Affects entire sectors.

**Endpoint:** `https://www.federalregister.gov/api/v1/documents.json`

**Fields:** `title`, `abstract`, `agencies`, `document_type` (rule/proposed_rule/executive_order), `publication_date`

**Signal logic:**
- Executive order mentioning specific industry → identify affected tickers
- Final rule (not proposed) with compliance costs → affects sector P&L
- Deregulation in specific sector → bullish for incumbents
- This is the broadest, lowest-precision feed. Use AI to extract affected companies.

### 4. Congress.gov — Bills & Appropriations

**What:** Defense spending bills, infrastructure packages, healthcare legislation.

**Endpoint:** `https://api.congress.gov/v3/bill` (free API key)

**Signal logic:**
- Appropriations bill passes committee with specific line items → identify beneficiaries
- Defense authorization with named programs → map to contractors
- Lower precision than SAM.gov/EDGAR. Use as confirming signal, not primary.

---

## AI Analysis Prompt

```
You are analyzing a potential equity trade triggered by a government data signal.

SIGNAL SOURCE: {catalyst_type}
{catalyst_details}

COMPANY:
Ticker: {ticker}
Company: {company_name}
Sector: {sector}
Market cap: ${market_cap}
Current price: ${current_price}
Today's change: {daily_change}%
Avg daily volume: {avg_volume}
52-week range: ${low_52w} - ${high_52w}

FINANCIAL CONTEXT:
{financial_summary}

RECENT FILINGS:
{recent_filings}

NEWS (last 48h):
{news_context}

INSIDER ACTIVITY (last 90 days):
{insider_context}

ANALYSIS TASK:
1. Is this government signal MATERIAL to this company's valuation?
   - For contract awards: what % of annual revenue does this represent?
   - For regulatory changes: what's the compliance cost or competitive impact?
   - For insider trades: is the pattern consistent (cluster buy vs routine selling)?

2. Has the market already priced this in?
   - Check if the stock moved today (already reacted)
   - Check if news outlets have covered it (widely known)
   - Check trading volume vs average (institutional already acting)

3. What is the expected magnitude and timeline?
   - Contract awards: typically 2-5% move over 1-3 days for large-caps
   - Insider clusters: 5-15% over 1-3 months
   - Regulatory: varies widely, often sector-wide rotation

4. If you have edge, estimate target price and confidence.

RESPOND (JSON only):
{{
    "action": "BUY" | "SHORT" | "SKIP",
    "confidence": 0-100,
    "target_price": float,
    "stop_loss_price": float,
    "expected_hold_days": int,
    "reasoning": "2-3 sentences: WHY has the market not priced this in yet?",
    "key_factors": ["factor1", "factor2", "factor3"]
}}
```

---

## Configuration (.env)

```bash
# --- Brokerage ---
BROKERAGE=alpaca                    # alpaca | tradier | ibkr
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_PAPER=true                   # Use Alpaca paper trading endpoint

# --- Data Feed API Keys ---
SAM_GOV_API_KEY=                    # From api.data.gov
CONGRESS_API_KEY=                   # From api.congress.gov
# SEC EDGAR: no key needed, uses User-Agent header
SEC_USER_AGENT="GovBot research@example.com"

# --- AI Providers ---
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
AI_PROVIDER=anthropic               # Primary provider
AI_ENSEMBLE_PROVIDERS=anthropic,deepseek  # Consensus providers

# --- Trading Parameters ---
PAPER_MODE=true
INITIAL_CAPITAL=10000
MAX_POSITION_PCT=0.10               # Max 10% per position
MAX_CONCURRENT_POSITIONS=5
MIN_CONFIDENCE=75
MIN_EDGE=0.05                       # 5% expected move minimum
STOP_LOSS_PCT=0.05
TAKE_PROFIT_PCT=0.08
TRAILING_STOP_PCT=0.02
MAX_HOLD_HOURS=72

# --- Scan Parameters ---
SCAN_INTERVAL_MINUTES=15
MIN_CONTRACT_VALUE=50000000         # $50M+ contract awards only
MIN_MARKET_CAP=500000000            # $500M+ companies only
MAX_MARKET_CAP=0                    # 0 = no upper limit

# --- Notifications ---
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=

# --- Logging ---
LOG_LEVEL=INFO
LOG_FILE=bot.log
```

---

## Folder Structure

```
govbot/
├── main.py                         # Entry point, scan loop, agent orchestration
├── ai/
│   ├── analyzer.py                 # AI prompts for equity analysis
│   ├── ensemble.py                 # Multi-model consensus (reuse from polybot)
│   └── news_fetcher.py             # Financial news (replace prediction market news)
├── core/
│   ├── position_manager.py         # Position tracking + risk rules (reuse from polybot)
│   ├── live_trader.py              # Order execution facade
│   ├── signal_scanner.py           # Polls SAM.gov, EDGAR, Fed Register, Congress
│   └── company_resolver.py         # Maps company names → tickers, fetches financials
├── brokerages/
│   ├── base.py                     # Abstract brokerage interface
│   └── alpaca.py                   # Alpaca REST adapter
├── feeds/
│   ├── sam_gov.py                  # SAM.gov contract awards poller
│   ├── sec_edgar.py                # EDGAR 8-K, Form 4, 13D parser
│   ├── federal_register.py         # Federal Register rules + EOs
│   └── congress.py                 # Congress.gov bills
├── utils/
│   ├── config.py                   # Config dataclass from .env (reuse pattern)
│   ├── notifier.py                 # Telegram bot (reuse from polybot)
│   └── utils.py                    # Retry, backoff, helpers (reuse from polybot)
├── data/
│   ├── portfolio.json              # Live state
│   ├── trades.csv                  # Trade history
│   ├── signals.csv                 # All signals (for backtesting)
│   └── company_map.json            # Company name → ticker mapping
├── Dockerfile
├── docker-compose.yml
├── deploy.sh                       # rsync to Pi (reuse from polybot)
├── requirements.txt
├── .env.example
└── CLAUDE.md
```

---

## Implementation Order

### Phase 1 — Scaffold + SAM.gov (week 1)
1. Set up project structure, config, logging (copy patterns from polybot)
2. Implement `brokerages/alpaca.py` with paper trading
3. Implement `feeds/sam_gov.py` — poll for contract awards
4. Implement `core/company_resolver.py` — company name → ticker mapping
5. Wire up main.py scan loop with SAM.gov only
6. Basic AI analysis prompt for contract awards
7. Paper trade: log what would have been traded

### Phase 2 — EDGAR + Enrichment (week 2)
1. Implement `feeds/sec_edgar.py` — 8-K and Form 4 parsing
2. Cross-reference SAM.gov awards with EDGAR filings
3. Add financial context to AI prompt (market cap, revenue, sector)
4. Implement position manager (reuse from polybot)
5. Telegram notifications

### Phase 3 — Full Pipeline + Risk (week 3)
1. Add Federal Register and Congress.gov feeds
2. Ensemble AI analysis (multi-model consensus)
3. Full risk management (stop loss, trailing stop, time limit)
4. Deploy to Pi via Docker
5. Run paper mode for 2+ weeks to validate

### Phase 4 — Go Live (week 4+)
1. Review paper trading results
2. If profitable: switch to live with minimum position sizes
3. Tune thresholds based on real fills and slippage
4. Add options support if equity signals prove reliable

---

## Key Differences from Polybot

| Aspect | Polybot | GovBot |
|--------|---------|--------|
| Market type | Binary prediction markets | Equities (options later) |
| Exchange | Kalshi, Polymarket | Alpaca (paper), then live |
| Edge source | Mispriced odds, whale flow | Gov data publication lag |
| Data feeds | Exchange APIs, news | SAM.gov, EDGAR, Fed Register |
| Position duration | Hours to days | 1-5 days typically |
| Signal anonymity | Kalshi trades anonymous | Gov data is public record |
| Risk profile | Binary (win/lose contract) | Continuous (stock price moves) |
| Liquidity | Often thin | Deep (equity markets) |

---

## Tech Stack

- **Language:** Python 3.11
- **Async:** asyncio + aiohttp
- **AI:** anthropic, openai (DeepSeek-compatible)
- **Brokerage:** alpaca-trade-api
- **Data:** pandas
- **Deployment:** Docker on Raspberry Pi 4/5
- **Notifications:** python-telegram-bot
- **Config:** python-dotenv

---

## CLAUDE.md (for the project)

```markdown
# GovBot

AI-assisted equity trading bot that exploits government data publication lag.

## Quick Start
- Run locally: `python -m govbot.main --paper`
- Run via Docker: `docker compose up -d`
- Deploy to Pi: `bash deploy.sh`

## Project Structure
- `feeds/` — government data feed pollers (SAM.gov, EDGAR, Fed Register, Congress)
- `brokerages/` — brokerage adapters (Alpaca first)
- `core/` — signal scanning, position management, order execution
- `ai/` — LLM analysis with multi-model ensemble consensus
- `utils/` — config, Telegram notifications, shared helpers
- `data/` — persisted state (portfolio.json, trades.csv, signals.csv)

## Rules
- PAPER_MODE=true by default. Never switch to live without explicit user instruction.
- All gov API calls must respect rate limits (SEC EDGAR: 10 req/sec, SAM.gov: varies).
- Company name → ticker mapping lives in data/company_map.json. Update it, don't hardcode.
- Always update CHANGELOG.md and DEVLOG.md at the end of every conversation.
```
