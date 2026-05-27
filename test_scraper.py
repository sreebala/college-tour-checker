"""
Quick sanity-check: runs just the scrapers and prints results.
No notifications are sent — safe to run before credentials are configured.

  python test_scraper.py           # test both
  python test_scraper.py uci       # test UCI only
  python test_scraper.py ucla      # test UCLA only
"""

import asyncio
import sys
from scraper import check_uci, check_ucla, check_all


def _print_result(r) -> None:
    print(f"\n{'='*50}")
    print(f"  {r.university} — {r.target_date}")
    print(f"{'='*50}")
    print(f"  Connected   : {r.connected}")
    print(f"  Error       : {r.error or 'none'}")
    print(f"  All slots   : {[s.time_str for s in r.slots] or 'none found'}")
    print(f"  Morning (<12): {[s.time_str for s in r.morning_slots] or 'none found'}")
    print(f"  Checked at  : {r.checked_at}")
    print()
    print("  Debug screenshots saved:")
    import glob
    prefix = r.university.lower()
    for f in sorted(glob.glob(f"debug_{prefix}_*.png")):
        print(f"    {f}")


async def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "both"

    if target == "uci":
        results = [await check_uci()]
    elif target == "ucla":
        results = [await check_ucla()]
    else:
        uci, ucla = await check_all()
        results = [uci, ucla]

    for r in results:
        _print_result(r)


if __name__ == "__main__":
    asyncio.run(main())
