#!/usr/bin/env python3
"""
Entry point for the tour-slot checker.

Usage
-----
  python main.py               # check slots + send instant alerts if new slots found
  python main.py --daily-report # same, but ALSO force-send the daily health report

The daily-report flag is set by the separate GitHub Actions daily workflow so
the two notification flows stay completely independent.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("tour_checker.log", mode="a"),
    ],
)
logger = logging.getLogger("main")

from scraper import check_all
from notifications import send_slot_alert, send_daily_report
import state_manager as sm


async def main() -> None:
    force_report = "--daily-report" in sys.argv
    logger.info("=== Tour checker start (force_report=%s) ===", force_report)

    state = sm.load()
    uci, ucla = await check_all()

    logger.info("UCI  → connected=%s  morning_slots=%d", uci.connected,  len(uci.morning_slots))
    logger.info("UCLA → connected=%s  morning_slots=%d", ucla.connected, len(ucla.morning_slots))

    # ── Instant alert flow ────────────────────────────────────────────────────
    # Send ONE alert per university covering all newly-discovered morning slots.
    # Subsequent checks won't re-alert for the same slots (guarded by state file).

    def _process_alerts(result) -> None:
        if not result.morning_slots:
            return
        new_slots = [
            s for s in result.morning_slots
            if sm.is_new_slot(state, sm.slot_key(result.university, s.time_str, result.target_date))
        ]
        if not new_slots:
            logger.info("[%s] Morning slots exist but already alerted — skipping", result.university)
            return
        logger.info("[%s] NEW morning slots — sending alert: %s", result.university, new_slots)
        send_slot_alert(result.university, result.target_date, result.morning_slots)
        for s in result.morning_slots:  # mark all morning slots as alerted
            sm.mark_alerted(state, sm.slot_key(result.university, s.time_str, result.target_date))

    _process_alerts(uci)
    _process_alerts(ucla)

    # ── Persist scraped snapshot ──────────────────────────────────────────────
    sm.record_check(state, uci, ucla)

    # ── Daily report flow ─────────────────────────────────────────────────────
    if force_report:
        logger.info("Sending daily report (forced by --daily-report flag)")
        send_daily_report(uci, ucla, state.get("last_check", "N/A"))
        state["last_daily_report"] = datetime.now(timezone.utc).isoformat()

    sm.save(state)
    logger.info("=== Tour checker done ===")


if __name__ == "__main__":
    asyncio.run(main())
