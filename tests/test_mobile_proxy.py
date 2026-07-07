"""
Тест мобильного прокси: последовательные запросы с паузой 12-25 сек.
Цель: понять через сколько запросов прокси начнёт сыпаться.

Usage:
    python test_mobile_proxy.py --proxy "http://user:pass@host:port"
    python test_mobile_proxy.py --proxy "http://user:pass@host:port" --min-interval 12 --max-interval 25
"""
import asyncio
import argparse
import datetime
import itertools
import logging
import random
import time

from app.scraper import GoogleTrendsScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

KEYWORDS = [
    ("bitcoin",       "US", "today 12-m"),
    ("chatgpt",       "US", "today 12-m"),
    ("trump",         "US", "today 3-m"),
    ("tiktok",        "US", "today 12-m"),
    ("netflix",       "GB", "today 12-m"),
    ("ethereum",      "US", "today 3-m"),
    ("openai",        "US", "now 7-d"),
    ("iphone",        "US", "today 5-y"),
    ("nvidia",        "US", "today 12-m"),
    ("instagram",     "US", "today 12-m"),
    ("tesla",         "US", "today 12-m"),
    ("python",        "US", "today 12-m"),
    ("docker",        "US", "today 12-m"),
    ("kubernetes",    "US", "today 12-m"),
    ("react",         "US", "today 12-m"),
    ("amazon",        "US", "today 5-y"),
    ("google",        "US", "today 5-y"),
    ("microsoft",     "US", "today 12-m"),
    ("facebook",      "US", "today 12-m"),
    ("youtube",       "BR", "today 12-m"),
    ("solana",        "US", "today 12-m"),
    ("dogecoin",      "US", "today 12-m"),
    ("inflation",     "US", "today 12-m"),
    ("ai",            "US", "today 12-m"),
    ("web3",          "US", "today 12-m"),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", type=str, required=True)
    parser.add_argument("--min-interval", type=float, default=12.0)
    parser.add_argument("--max-interval", type=float, default=25.0)
    args = parser.parse_args()

    ok = 0
    fail = 0
    req_n = 0

    log.info("Proxy: %s", args.proxy)
    log.info("Interval: %.0f–%.0fs between requests", args.min_interval, args.max_interval)
    log.info("=" * 60)

    scraper = GoogleTrendsScraper(proxy=args.proxy)
    await scraper.__aenter__()

    try:
        for keyword, geo, timeframe in itertools.cycle(KEYWORDS):
            req_n += 1
            t0 = time.monotonic()
            try:
                result = await scraper.fetch(keyword, geo, timeframe)
                pts = sum(1 for p in result.points if p.has_data)
                elapsed = time.monotonic() - t0
                ok += 1
                log.info(
                    "[req #%d]  OK    %-22s geo=%-4s  pts=%-3d  %.1fs  | total %d ok / %d fail",
                    req_n, keyword, geo, pts, elapsed, ok, fail,
                )
            except Exception as e:
                elapsed = time.monotonic() - t0
                fail += 1
                log.warning(
                    "[req #%d]  FAIL  %-22s geo=%-4s  %.1fs  | total %d ok / %d fail  ERR: %s",
                    req_n, keyword, geo, elapsed, ok, fail, e,
                )
                # При сетевой ошибке пересоздаём сессию
                if "curl:" in str(e) or "429" in str(e):
                    log.info("Recreating scraper session...")
                    try:
                        await scraper.__aexit__(None, None, None)
                    except Exception:
                        pass
                    scraper = GoogleTrendsScraper(proxy=args.proxy)
                    await scraper.__aenter__()

            delay = random.uniform(args.min_interval, args.max_interval)
            log.info("  sleeping %.1fs ...", delay)
            await asyncio.sleep(delay)

    except KeyboardInterrupt:
        pass
    finally:
        await scraper.__aexit__(None, None, None)

    total = ok + fail
    log.info("=" * 60)
    log.info(
        "DONE  requests=%d  ok=%d  fail=%d  success_rate=%.0f%%",
        total, ok, fail,
        ok / total * 100 if total else 0,
    )


if __name__ == "__main__":
    asyncio.run(main())