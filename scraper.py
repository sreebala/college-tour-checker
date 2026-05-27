"""
Headless Playwright scraper for UCI and UCLA Slate tour portals.

Both portals render calendars via JavaScript; this module navigates from the
current month to July 2026 and extracts available morning slots on the target
dates (July 8 for UCI, July 9 for UCLA).

Selector strategy: multiple CSS selector patterns are tried in order so the
script degrades gracefully if Slate updates its markup. Screenshots are saved
at each key step and uploaded as CI artifacts for debugging.
"""

import asyncio
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
    TimeoutError as PWTimeout,
)
from playwright_stealth import stealth_async

logger = logging.getLogger(__name__)

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class TimeSlot:
    time_str: str
    is_morning: bool  # True if hour < 12

    def __hash__(self):
        return hash(self.time_str)

    def __eq__(self, other):
        return isinstance(other, TimeSlot) and self.time_str == other.time_str


@dataclass
class ScrapeResult:
    university: str
    target_date: str
    connected: bool = False
    slots: list[TimeSlot] = field(default_factory=list)
    morning_slots: list[TimeSlot] = field(default_factory=list)
    error: Optional[str] = None
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    screenshot_july: Optional[str] = None  # viewport-only shot of the July calendar


# ── Configuration ─────────────────────────────────────────────────────────────

UCI_URL = (
    "https://apply.admissions.uci.edu/portal/uci_uga_tours_prospect"
    "?tab=prospect_guidedtours"
)
UCLA_URL = "https://connect.admission.ucla.edu/portal/tours"

TARGET_MONTH = "July"
TARGET_YEAR  = 2026

# ── Browser factory ───────────────────────────────────────────────────────────

async def _make_context(playwright) -> tuple:
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--window-size=1280,800",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/Los_Angeles",
    )
    return browser, context


# ── Time parsing helpers ──────────────────────────────────────────────────────

_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)", re.IGNORECASE)


def _parse_hour(text: str) -> Optional[int]:
    m = _TIME_RE.search(text)
    if not m:
        return None
    hour, _, period = int(m.group(1)), m.group(2), m.group(3).upper()
    if period == "PM" and hour != 12:
        hour += 12
    elif period == "AM" and hour == 12:
        hour = 0
    return hour


def _is_morning(text: str) -> bool:
    hour = _parse_hour(text)
    return hour is not None and hour < 12


# ── Calendar navigation ───────────────────────────────────────────────────────

# Ordered from most- to least-specific so we stop at the first working selector.
_NEXT_SELECTORS = [
    # jQuery UI Datepicker (confirmed on UCI; likely UCLA too)
    "a.ui-datepicker-next",
    ".ui-datepicker-next",
    # FullCalendar
    ".fc-next-button",
    "button.fc-next-button",
    # Generic aria / data patterns
    "button[aria-label='Next month']",
    "button[aria-label='next month']",
    "button[aria-label='Next']",
    "[data-action='next']",
    "[class*='next-month']",
    # Text-based fallbacks
    "button:has-text('›')",
    "button:has-text('>')",
    "button:has-text('→')",
    "a:has-text('Next')",
    "button:has-text('Next')",
]

_HEADER_SELECTORS = [
    # jQuery UI Datepicker
    ".ui-datepicker-title",
    "span.ui-datepicker-month",
    # FullCalendar
    ".fc-toolbar-title",
    ".fc-toolbar h2",
    # Generic
    "[class*='calendar-title']",
    "[class*='month-header']",
    "[class*='calendar-header'] h2",
    "[class*='calendar-header'] span",
    "h2[class*='month']",
    "[class*='month-year']",
]

_CALENDAR_SELECTORS = [
    # jQuery UI Datepicker
    ".ui-datepicker",
    "#ui-datepicker-div",
    "table.ui-datepicker-calendar",
    # FullCalendar
    ".fc-view",
    "[class*='fc-']",
    # Generic
    "[class*='calendar']",
    "[id*='calendar']",
    "[class*='visit']",
    "[class*='tour']",
    "table[class*='cal']",
]


async def _wait_for_calendar(page: Page, timeout_ms: int = 20_000) -> bool:
    for sel in _CALENDAR_SELECTORS:
        try:
            await page.wait_for_selector(sel, timeout=timeout_ms // len(_CALENDAR_SELECTORS))
            logger.debug("Calendar element found: %s", sel)
            return True
        except PWTimeout:
            continue
    logger.warning("No calendar selector matched — continuing anyway")
    return False


async def _read_month_header(page: Page) -> Optional[str]:
    for sel in _HEADER_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            continue

    # JS fallback: grab the most prominent heading inside a calendar container
    try:
        text = await page.evaluate("""
            () => {
                const calEl = document.querySelector(
                    '[class*="calendar"], [class*="fc-"], [class*="visit"], [class*="tour"]'
                );
                if (!calEl) return null;
                const h = calEl.querySelector('h1,h2,h3,h4,span[class*="month"],span[class*="title"]');
                return h ? h.innerText.trim() : null;
            }
        """)
        return text
    except Exception:
        return None


async def _navigate_to_july(page: Page) -> bool:
    """Advance the calendar month-by-month until July TARGET_YEAR is shown."""
    for _ in range(18):  # guard: never loop more than 18 months forward
        header = await _read_month_header(page)
        if header and TARGET_MONTH.lower() in header.lower() and str(TARGET_YEAR) in header:
            logger.info("Reached %s", header)
            return True

        clicked = False
        for sel in _NEXT_SELECTORS:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(900)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            logger.warning("Could not find 'next month' button; header was: %s", header)
            return False

    logger.warning("Gave up navigating to %s %s", TARGET_MONTH, TARGET_YEAR)
    return False


# ── Date cell helpers ─────────────────────────────────────────────────────────

async def _find_day_cell(page: Page, day: int):
    """Return the element representing the target day in the displayed month."""
    day_str = f"{day:02d}"

    # ── jQuery UI Datepicker (confirmed UCI; data-month is 0-indexed, July=6) ──
    try:
        cells = await page.query_selector_all(
            "td[data-handler='selectDay'][data-month='6'][data-year='2026']"
        )
        for cell in cells:
            link = await cell.query_selector("a")
            if link and (await link.inner_text()).strip() == str(day):
                logger.info("jQuery UI day cell found for day %d", day)
                return cell
    except Exception:
        pass

    # ── FullCalendar / data-date attribute ────────────────────────────────────
    for sel in [
        f"[data-date='2026-07-{day_str}']",
        f"[data-date*='-07-{day_str}']",
        f"td.fc-daygrid-day[data-date$='-07-{day_str}']",
        f"[aria-label*='July {day},']",
        f"[aria-label='July {day}']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                return el
        except Exception:
            continue

    # ── Text-based fallback inside any calendar container ─────────────────────
    candidates = await page.query_selector_all(
        "td.fc-daygrid-day, [class*='day-cell'], [class*='calendar-day'], [class*='cal-day']"
    )
    for el in candidates:
        try:
            text = (await el.inner_text()).strip()
            if text == str(day) or text.startswith(str(day) + "\n"):
                return el
        except Exception:
            continue

    return None


async def _day_is_available(cell) -> bool:
    try:
        # jQuery UI datepicker: data-handler="selectDay" means the day is selectable;
        # unselectable days use td.ui-datepicker-unselectable and have no handler.
        handler = await cell.get_attribute("data-handler")
        if handler == "selectDay":
            return True

        cls = (await cell.get_attribute("class")) or ""
        aria_disabled = await cell.get_attribute("aria-disabled")

        if aria_disabled == "true":
            return False

        blocked = {"disabled", "unavailable", "unselectable", "past",
                   "fc-day-other", "blocked", "no-slot", "inactive"}
        if any(b in cls.lower() for b in blocked):
            return False

        available = {"available", "has-event", "selectable", "open", "active", "bookable"}
        if any(a in cls.lower() for a in available):
            return True

        return True  # optimistic — slot extraction returns empty if nothing is there
    except Exception:
        return True


# ── Slot extraction ───────────────────────────────────────────────────────────

_SLOT_SELECTORS = [
    "[class*='time-slot']",
    "[class*='timeslot']",
    "[class*='slot-time']",
    "[class*='event-time']",
    "[class*='tour-time']",
    "[class*='session-time']",
    "[class*='booking-time']",
    "input[type='radio']",
    "label[class*='slot']",
    "li[class*='time']",
    ".fc-event-time",
    "option",                   # <select> dropdowns with time options
]


async def _extract_slots_after_click(page: Page) -> list[TimeSlot]:
    await page.wait_for_timeout(1_800)  # let slot list animate in

    slots: list[TimeSlot] = []

    for sel in _SLOT_SELECTORS:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                text = (await el.inner_text()).strip()
                if not _TIME_RE.search(text):
                    # For radio buttons the label may carry the time text
                    label_text = await page.evaluate(
                        "(el) => { const id=el.id; if(!id) return ''; "
                        "const lb=document.querySelector('label[for=\"'+id+'\"]'); "
                        "return lb ? lb.innerText : ''; }",
                        el,
                    )
                    text = label_text.strip()

                if _TIME_RE.search(text):
                    slots.append(TimeSlot(time_str=text, is_morning=_is_morning(text)))
            if slots:
                break
        except Exception:
            continue

    # JS full-body fallback — pull every time-like string from the visible page
    if not slots:
        try:
            raw_times = await page.evaluate("""
                () => {
                    const re = /\\d{1,2}:\\d{2}\\s*(AM|PM)/gi;
                    return [...new Set(document.body.innerText.match(re) || [])];
                }
            """)
            for t in (raw_times or []):
                slots.append(TimeSlot(time_str=t.strip(), is_morning=_is_morning(t)))
        except Exception:
            pass

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[TimeSlot] = []
    for s in slots:
        if s.time_str not in seen:
            seen.add(s.time_str)
            unique.append(s)

    return unique


# ── Per-university scrapers ───────────────────────────────────────────────────

async def _scrape(
    url: str,
    university: str,
    target_date: str,
    target_day: int,
    screenshot_prefix: str,
) -> ScrapeResult:
    result = ScrapeResult(university=university, target_date=target_date)

    async with async_playwright() as p:
        browser, context = await _make_context(p)
        page = await context.new_page()
        await stealth_async(page)

        try:
            logger.info("[%s] Loading %s", university, url)
            await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
            await page.wait_for_timeout(3_500)     # let React finish rendering
            result.connected = True

            await page.screenshot(path=f"debug_{screenshot_prefix}_01_loaded.png", full_page=True)

            # Dismiss any overlay/cookie banner that might block clicks
            for dismiss_sel in [
                "button:has-text('Accept')",
                "button:has-text('OK')",
                "button:has-text('Close')",
                "[aria-label='Close']",
            ]:
                try:
                    btn = page.locator(dismiss_sel).first
                    if await btn.is_visible(timeout=1_500):
                        await btn.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass

            # Scroll to the bottom to trigger any lazy-loaded content, then back up
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1_500)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)

            # Dump DOM structure to help debug selector misses
            dom_summary = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('[class]');
                    const classes = new Set();
                    els.forEach(e => e.classList.forEach(c => classes.add(c)));
                    const iframes = [...document.querySelectorAll('iframe')].map(f => f.src);
                    return { classes: [...classes].slice(0, 120), iframes };
                }
            """)
            logger.info("[%s] iframes on page: %s", university, dom_summary.get("iframes"))
            logger.debug("[%s] CSS classes found: %s", university, dom_summary.get("classes"))

            await _wait_for_calendar(page)
            await page.wait_for_timeout(1_000)
            await page.screenshot(path=f"debug_{screenshot_prefix}_02_calendar.png", full_page=True)

            nav_ok = await _navigate_to_july(page)
            if not nav_ok:
                logger.warning("[%s] Month navigation incomplete", university)
            await page.screenshot(path=f"debug_{screenshot_prefix}_03_july.png", full_page=True)

            # Focused calendar screenshot for the daily report email (viewport only,
            # scrolled so the calendar widget is centred in frame)
            cal_shot = f"debug_{screenshot_prefix}_calendar_report.png"
            try:
                cal_el = await page.query_selector(
                    ".ui-datepicker, #ui-datepicker-div, "
                    "table.ui-datepicker-calendar, [class*='calendar'], .fc-view"
                )
                if cal_el:
                    await cal_el.scroll_into_view_if_needed()
                    await page.wait_for_timeout(400)
                await page.screenshot(path=cal_shot)   # viewport only — clean crop
                result.screenshot_july = cal_shot
            except Exception as exc:
                logger.warning("[%s] Could not take calendar report screenshot: %s", university, exc)

            cell = await _find_day_cell(page, target_day)
            if cell is None:
                logger.info("[%s] Day %d cell not found on page", university, target_day)
            else:
                available = await _day_is_available(cell)
                if not available:
                    logger.info("[%s] Day %d is marked unavailable", university, target_day)
                else:
                    logger.info("[%s] Day %d appears available — clicking", university, target_day)
                    await cell.click()
                    await page.screenshot(path=f"debug_{screenshot_prefix}_04_day_clicked.png", full_page=True)
                    slots = await _extract_slots_after_click(page)
                    result.slots = slots
                    result.morning_slots = [s for s in slots if s.is_morning]
                    logger.info(
                        "[%s] Found %d total slots, %d morning",
                        university, len(slots), len(result.morning_slots),
                    )

            await page.screenshot(path=f"debug_{screenshot_prefix}_05_final.png", full_page=True)

        except Exception as exc:
            result.error = str(exc)
            logger.error("[%s] Scrape failed: %s", university, exc, exc_info=True)
            try:
                await page.screenshot(path=f"debug_{screenshot_prefix}_error.png", full_page=True)
            except Exception:
                pass
        finally:
            await context.close()
            await browser.close()

    return result


async def check_uci() -> ScrapeResult:
    return await _scrape(
        url=UCI_URL,
        university="UCI",
        target_date="July 8, 2026",
        target_day=8,
        screenshot_prefix="uci",
    )


async def check_ucla() -> ScrapeResult:
    return await _scrape(
        url=UCLA_URL,
        university="UCLA",
        target_date="July 9, 2026",
        target_day=9,
        screenshot_prefix="ucla",
    )


async def check_all() -> tuple[ScrapeResult, ScrapeResult]:
    """Run both scrapers concurrently and return (uci, ucla)."""
    return await asyncio.gather(check_uci(), check_ucla())
