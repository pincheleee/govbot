#!/usr/bin/env python3
"""Test each data feed in isolation to verify API responses and parsing.

Usage:
    python scripts/test_feeds.py                  # test all feeds
    python scripts/test_feeds.py sam              # test SAM.gov only
    python scripts/test_feeds.py edgar            # test SEC EDGAR only
    python scripts/test_feeds.py fedreg           # test Federal Register only
    python scripts/test_feeds.py congress         # test Congress.gov only
    python scripts/test_feeds.py news             # test news fetcher only
    python scripts/test_feeds.py etf              # test sector-to-ETF mapper
"""

import asyncio
import json
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_feeds")


async def test_sam_gov(config: Config):
    """Test SAM.gov feed."""
    from feeds.sam_gov import SamGovFeed

    print("\n" + "=" * 60)
    print("TESTING: SAM.gov Contract Awards")
    print("=" * 60)

    if not config.sam_gov_api_key:
        print("SKIP: SAM_GOV_API_KEY not set in .env")
        return False

    feed = SamGovFeed(
        api_key=config.sam_gov_api_key,
        min_contract_value=10_000_000,  # Lower threshold for testing
    )

    try:
        awards = await feed.poll(lookback_hours=72)  # Wider window for testing
        print(f"OK: Got {len(awards)} awards")
        for award in awards[:5]:
            print(f"  - {award.awardee}: {award.amount_formatted}")
            print(f"    Title: {award.title[:100]}")
            print(f"    Agency: {award.department}")
            print(f"    URL: {award.url}")
            print()
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


async def test_edgar(config: Config):
    """Test SEC EDGAR feed."""
    from feeds.sec_edgar import SecEdgarFeed

    print("\n" + "=" * 60)
    print("TESTING: SEC EDGAR Filings")
    print("=" * 60)

    if "example.com" in config.sec_user_agent:
        print("SKIP: SEC_USER_AGENT still has example.com -- set a real email")
        return False

    feed = SecEdgarFeed(user_agent=config.sec_user_agent)

    try:
        filings = await feed.poll(lookback_hours=48)
        print(f"OK: Got {len(filings)} filings")
        for filing in filings[:5]:
            print(f"  - [{filing.form_type}] {filing.company}")
            if filing.ticker:
                print(f"    Ticker: {filing.ticker}")
            print(f"    Filed: {filing.filed_date}")
            print(f"    {filing.description[:100]}")
            print()
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


async def test_fed_register():
    """Test Federal Register feed (no API key needed)."""
    from feeds.federal_register import FederalRegisterFeed

    print("\n" + "=" * 60)
    print("TESTING: Federal Register")
    print("=" * 60)

    feed = FederalRegisterFeed()

    try:
        docs = await feed.poll(lookback_hours=72)
        print(f"OK: Got {len(docs)} documents")
        for doc in docs[:5]:
            print(f"  - [{doc.document_type}] {doc.title[:100]}")
            print(f"    Agencies: {', '.join(doc.agencies[:3])}")
            print(f"    Sector: {doc.affected_sector or 'N/A'}")
            print(f"    Significant: {doc.significant}")
            print(f"    Published: {doc.publication_date}")
            print()
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


async def test_congress(config: Config):
    """Test Congress.gov feed."""
    from feeds.congress import CongressFeed

    print("\n" + "=" * 60)
    print("TESTING: Congress.gov Bills")
    print("=" * 60)

    if not config.congress_api_key:
        print("SKIP: CONGRESS_API_KEY not set in .env")
        return False

    feed = CongressFeed(api_key=config.congress_api_key)

    try:
        bills = await feed.poll(lookback_hours=72)
        print(f"OK: Got {len(bills)} market-relevant bills")
        for bill in bills[:5]:
            print(f"  - [{bill.bill_id}] {bill.title[:100]}")
            print(f"    Sector: {bill.sector}")
            print(f"    Latest action: {bill.latest_action[:100]}")
            print(f"    Action date: {bill.action_date}")
            print()
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


async def test_news():
    """Test news fetcher."""
    from ai.news_fetcher import NewsFetcher

    print("\n" + "=" * 60)
    print("TESTING: News Fetcher (RSS)")
    print("=" * 60)

    fetcher = NewsFetcher()

    test_tickers = ["LMT", "MSFT", "XLV"]

    for ticker in test_tickers:
        try:
            items = await fetcher.fetch(ticker)
            print(f"\n  {ticker}: {len(items)} relevant items")
            for item in items[:3]:
                score = item.get("relevance_score", 0)
                print(f"    [{score:.2f}] {item['title'][:80]}")
                print(f"           Source: {item['source']}")
        except Exception as e:
            print(f"  {ticker}: FAIL - {e}")

    print()
    return True


def test_etf_mapper():
    """Test sector-to-ETF mapper."""
    from core.sector_etf_mapper import SectorETFMapper

    print("\n" + "=" * 60)
    print("TESTING: Sector-to-ETF Mapper")
    print("=" * 60)

    mapper = SectorETFMapper()

    test_sectors = [
        "defense", "Defense", "Defense/Security",
        "healthcare", "Healthcare", "Pharma",
        "technology", "Tech/Antitrust",
        "energy", "Energy", "Energy/Utilities",
        "finance", "Financial",
        "infrastructure", "trade", "Trade",
        "Telecom", "Broad market", "appropriations",
        "unknown_sector",
    ]

    all_ok = True
    for sector in test_sectors:
        primary = mapper.resolve(sector)
        alternates = mapper.resolve_all(sector)
        is_expected_none = sector == "unknown_sector"

        if primary:
            print(f"  {sector:25s} -> {primary} (alternates: {', '.join(alternates[1:])})")
        elif is_expected_none:
            print(f"  {sector:25s} -> None (expected)")
        else:
            print(f"  {sector:25s} -> MISSING MAPPING")
            all_ok = False

    # Test macro signal detection
    print(f"\n  is_macro('FED_REGISTER') = {mapper.is_macro_signal('FED_REGISTER')}")
    print(f"  is_macro('CONGRESS')     = {mapper.is_macro_signal('CONGRESS')}")
    print(f"  is_macro('SAM_CONTRACT') = {mapper.is_macro_signal('SAM_CONTRACT')}")
    print(f"  is_macro('SEC_8K')       = {mapper.is_macro_signal('SEC_8K')}")
    print()

    return all_ok


async def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    config = Config()
    results = {}

    if target in ("all", "fedreg"):
        results["Federal Register"] = await test_fed_register()

    if target in ("all", "news"):
        results["News Fetcher"] = await test_news()

    if target in ("all", "etf"):
        results["ETF Mapper"] = test_etf_mapper()

    if target in ("all", "sam"):
        results["SAM.gov"] = await test_sam_gov(config)

    if target in ("all", "edgar"):
        results["SEC EDGAR"] = await test_edgar(config)

    if target in ("all", "congress"):
        results["Congress.gov"] = await test_congress(config)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL/SKIP"
        print(f"  {name:25s} {status}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
