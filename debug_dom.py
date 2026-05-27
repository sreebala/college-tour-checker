"""
One-shot DOM inspector — run this once to identify the exact CSS classes and
HTML structure Slate uses for its calendar. Output is used to fix scraper.py selectors.

  python debug_dom.py
"""
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async


async def inspect(url: str, label: str) -> None:
    print(f"\n{'#'*60}")
    print(f"# {label}")
    print(f"# {url}")
    print(f"{'#'*60}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await stealth_async(page)

        await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
        await page.wait_for_timeout(4_000)

        # Trigger lazy loading
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1_500)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)

        # ── 1. All clickable buttons ──────────────────────────────────────────
        buttons = await page.evaluate("""
            () => [...document.querySelectorAll('button, a, input[type="button"], input[type="submit"]')]
                .map(el => ({
                    tag:  el.tagName,
                    text: (el.innerText || el.value || '').trim().slice(0, 60),
                    cls:  (el.className || '').slice(0, 100),
                    href: el.href || '',
                    aria: el.getAttribute('aria-label') || '',
                }))
                .filter(el => el.text || el.aria)
                .slice(0, 60)
        """)
        print("\n── BUTTONS / LINKS ──────────────────────────────────────────")
        for b in buttons:
            print(f"  [{b['tag']}] text={b['text']!r:40s}  aria={b['aria']!r:30s}  cls={b['cls']!r}")

        # ── 2. Elements whose class contains calendar-related keywords ────────
        cal_els = await page.evaluate("""
            () => {
                const kw = ['cal', 'month', 'day', 'date', 'visit', 'tour',
                            'schedule', 'slot', 'time', 'pick', 'avail', 'book'];
                const out = [];
                document.querySelectorAll('[class]').forEach(el => {
                    const c = el.className.toLowerCase();
                    if (kw.some(k => c.includes(k))) {
                        out.push({
                            tag:   el.tagName,
                            id:    el.id || '',
                            cls:   el.className.slice(0, 120),
                            text:  (el.innerText || '').trim().slice(0, 80),
                            datas: Object.fromEntries(
                                [...el.attributes]
                                    .filter(a => a.name.startsWith('data-'))
                                    .map(a => [a.name, a.value.slice(0, 40)])
                            ),
                        });
                    }
                });
                return out.slice(0, 80);
            }
        """)
        print("\n── CALENDAR-RELATED ELEMENTS ────────────────────────────────")
        for el in cal_els:
            print(f"  [{el['tag']}] id={el['id']!r}  cls={el['cls']!r}")
            if el["datas"]:
                print(f"    data: {el['datas']}")
            if el["text"]:
                print(f"    text: {el['text']!r}")

        # ── 3. All <table> elements (calendars are often tables) ──────────────
        tables = await page.evaluate("""
            () => [...document.querySelectorAll('table')].map(t => ({
                id:   t.id,
                cls:  t.className,
                html: t.outerHTML.slice(0, 800),
            }))
        """)
        print("\n── TABLES ───────────────────────────────────────────────────")
        for t in tables:
            print(f"\n  id={t['id']!r}  cls={t['cls']!r}")
            print(f"  {t['html']}")

        # ── 4. Raw text that looks like a month+year heading ──────────────────
        month_text = await page.evaluate("""
            () => {
                const months = ['January','February','March','April','May','June',
                                'July','August','September','October','November','December'];
                const re = new RegExp('(' + months.join('|') + ')\\\\s+\\\\d{4}', 'i');
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const hits = [];
                let node;
                while ((node = walker.nextNode())) {
                    if (re.test(node.textContent)) {
                        const parent = node.parentElement;
                        hits.push({
                            text: node.textContent.trim(),
                            parentTag: parent.tagName,
                            parentCls: (parent.className || '').slice(0, 100),
                            parentId:  parent.id || '',
                        });
                    }
                }
                return hits;
            }
        """)
        print("\n── MONTH/YEAR TEXT NODES ────────────────────────────────────")
        for m in month_text:
            print(f"  text={m['text']!r}  in [{m['parentTag']}] id={m['parentId']!r}  cls={m['parentCls']!r}")

        await context.close()
        await browser.close()


async def main():
    await inspect(
        "https://apply.admissions.uci.edu/portal/uci_uga_tours_prospect?tab=prospect_guidedtours",
        "UCI",
    )
    await inspect(
        "https://connect.admission.ucla.edu/portal/tours",
        "UCLA",
    )


if __name__ == "__main__":
    asyncio.run(main())
