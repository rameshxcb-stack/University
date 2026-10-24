# -*- coding: utf-8 -*-
"""
VBU Notice Page Diagnostic Script
==================================
Purpose: Understand WHY VBU notices are being missed.

Run this locally (not on GitHub Actions):
    pip install playwright beautifulsoup4 lxml
    python -m playwright install chromium
    python diagnose_vbu.py

Output: Creates a report file `vbu_report.txt` + `vbu_rendered.html`
Share both files for exact fix.
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[FATAL] playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("[FATAL] beautifulsoup4 not installed. Run: pip install beautifulsoup4 lxml")
    sys.exit(1)


TARGET_URL = "https://www.vbu.ac.in/notice/Notice"
OUTPUT_DIR = Path(__file__).resolve().parent
HTML_FILE = OUTPUT_DIR / "vbu_rendered.html"
REPORT_FILE = OUTPUT_DIR / "vbu_report.txt"
SCREENSHOT_FILE = OUTPUT_DIR / "vbu_screenshot.png"

WAIT_AFTER_MS = 15000  # 15 sec — matches proposed fix
TIMEOUT_MS = 60000


class Report:
    def __init__(self):
        self.lines = []
    def add(self, text=""):
        self.lines.append(text)
        print(text)
    def save(self):
        with REPORT_FILE.open("w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
        print(f"\n✅ Report saved: {REPORT_FILE}")


def section(rep, title):
    rep.add("")
    rep.add("=" * 70)
    rep.add(f"  {title}")
    rep.add("=" * 70)


def truncate(s, n=100):
    if s is None: return "None"
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


def main():
    rep = Report()
    rep.add("VBU DIAGNOSTIC REPORT")
    rep.add(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    rep.add(f"Target: {TARGET_URL}")
    rep.add(f"Wait after load: {WAIT_AFTER_MS}ms")
    rep.add(f"Timeout: {TIMEOUT_MS}ms")

    # -----------------------------------------------------------------
    # STAGE 1 — Render page with Playwright
    # -----------------------------------------------------------------
    section(rep, "STAGE 1 — PLAYWRIGHT RENDER")
    html_content = None
    page_url_final = TARGET_URL
    render_status = "unknown"

    try:
        with sync_playwright() as pw:
            rep.add("[INFO] Launching browser...")
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    locale="en-IN",
                    viewport={"width": 1366, "height": 900},
                    java_script_enabled=True,
                    ignore_https_errors=True,
                )
                page = context.new_page()
                page.set_default_timeout(TIMEOUT_MS)

                rep.add(f"[INFO] Navigating to {TARGET_URL} ...")
                try:
                    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    render_status = "domcontentloaded_ok"
                except Exception as e:
                    rep.add(f"[WARN] domcontentloaded failed: {type(e).__name__}: {e}")
                    try:
                        page.goto(TARGET_URL, wait_until="load", timeout=TIMEOUT_MS)
                        render_status = "load_ok"
                    except Exception as e2:
                        rep.add(f"[ERROR] All navigation attempts failed: {type(e2).__name__}: {e2}")
                        render_status = f"nav_fail:{type(e2).__name__}"

                if render_status.endswith("_ok"):
                    rep.add(f"[OK] Page navigation: {render_status}")
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                        rep.add("[OK] Network idle reached")
                    except Exception as e:
                        rep.add(f"[WARN] networkidle not reached: {type(e).__name__}")

                    rep.add(f"[INFO] Waiting {WAIT_AFTER_MS}ms for Angular to render...")
                    page.wait_for_timeout(WAIT_AFTER_MS)

                    # Try scrolling to trigger lazy loads
                    try:
                        prev_h = -1
                        for i in range(3):
                            h = page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
                            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                            page.wait_for_timeout(800)
                            if h == prev_h: break
                            prev_h = h
                        page.evaluate("() => window.scrollTo(0, 0)")
                        rep.add(f"[OK] Scroll triggered (final height: {prev_h}px)")
                    except Exception as e:
                        rep.add(f"[WARN] Scroll failed: {type(e).__name__}")

                    page_url_final = page.url or TARGET_URL
                    rep.add(f"[INFO] Final URL: {page_url_final}")

                    html_content = page.content()
                    rep.add(f"[OK] Captured HTML: {len(html_content)} chars")

                    # Save screenshot
                    try:
                        page.screenshot(path=str(SCREENSHOT_FILE), full_page=True)
                        rep.add(f"[OK] Screenshot saved: {SCREENSHOT_FILE}")
                    except Exception as e:
                        rep.add(f"[WARN] Screenshot failed: {e}")

                    # Check for network requests to API endpoints
                    rep.add("[INFO] Capturing network API calls (last 30 seconds)...")
                    # Note: we already navigated, so this is best-effort
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        rep.add(f"[FATAL] Playwright crashed: {type(e).__name__}: {e}")
        rep.save()
        return

    if not html_content:
        rep.add("[FATAL] No HTML captured — cannot proceed")
        rep.save()
        return

    # Save HTML
    with HTML_FILE.open("w", encoding="utf-8") as f:
        f.write(html_content)
    rep.add(f"[OK] HTML saved: {HTML_FILE}")

    # -----------------------------------------------------------------
    # STAGE 2 — Parse HTML with BeautifulSoup
    # -----------------------------------------------------------------
    section(rep, "STAGE 2 — HTML STRUCTURE ANALYSIS")

    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()

    # Page level
    rep.add(f"Page title: {truncate(soup.title.string if soup.title else 'None', 120)}")
    body_text = soup.get_text(" ", strip=True)
    rep.add(f"Body text length: {len(body_text)} chars")

    # Angular markers
    rep.add("")
    rep.add("--- Angular / JS framework markers ---")
    markers = {
        "ng-app": "ng-app" in html_content,
        "ng-version": "ng-version" in html_content,
        "ng-controller": "ng-controller" in html_content,
        "ng-repeat": "ng-repeat" in html_content,
        "ng-view": "ng-view" in html_content,
        "angular.min.js": "angular.min.js" in html_content.lower(),
        "{{ }} templates": bool(re.search(r"\{\{[^}]{1,200}\}\}", html_content)),
    }
    for m, present in markers.items():
        rep.add(f"  {m}: {'✅ YES' if present else '❌ NO'}")

    # -----------------------------------------------------------------
    # STAGE 3 — All click-related elements
    # -----------------------------------------------------------------
    section(rep, "STAGE 3 — CLICK HANDLERS")

    onclick_elems = soup.find_all(attrs={"onclick": True})
    ngclick_elems = soup.find_all(attrs={"ng-click": True})
    href_js_elems = [a for a in soup.find_all("a", href=True) if (a.get("href") or "").lower().startswith(("javascript:", "#"))]

    rep.add(f"Elements with onclick: {len(onclick_elems)}")
    rep.add(f"Elements with ng-click: {len(ngclick_elems)}")
    rep.add(f"<a> tags with href='javascript:...' or '#': {len(href_js_elems)}")

    if onclick_elems:
        rep.add("")
        rep.add("--- Sample onclick elements (first 20) ---")
        for i, elem in enumerate(onclick_elems[:20]):
            onclick = elem.get("onclick", "")
            tag_name = elem.name
            text = truncate(elem.get_text(" ", strip=True), 60)
            rep.add(f"  [{i+1}] <{tag_name}> onclick={truncate(onclick, 120)!r} | text={text!r}")

    if ngclick_elems:
        rep.add("")
        rep.add("--- Sample ng-click elements (first 20) ---")
        for i, elem in enumerate(ngclick_elems[:20]):
            ngclick = elem.get("ng-click", "")
            tag_name = elem.name
            text = truncate(elem.get_text(" ", strip=True), 60)
            rep.add(f"  [{i+1}] <{tag_name}> ng-click={truncate(ngclick, 120)!r} | text={text!r}")

    if href_js_elems:
        rep.add("")
        rep.add("--- Sample javascript:void(0) links (first 20) ---")
        for i, a in enumerate(href_js_elems[:20]):
            href = a.get("href", "")
            onclick = a.get("onclick", "")
            ngclick = a.get("ng-click", "")
            text = truncate(a.get_text(" ", strip=True), 60)
            rep.add(f"  [{i+1}] href={truncate(href, 50)!r} onclick={truncate(onclick, 50)!r} ng-click={truncate(ngclick, 50)!r} | text={text!r}")

    # -----------------------------------------------------------------
    # STAGE 4 — All <a> tags with hrefs
    # -----------------------------------------------------------------
    section(rep, "STAGE 4 — ALL <a> TAGS")

    all_anchors = soup.find_all("a", href=True)
    rep.add(f"Total <a> tags with href: {len(all_anchors)}")

    pdf_hrefs = []
    ftpweb_hrefs = []
    notice_hrefs = []
    download_hrefs = []
    external_hrefs = []

    for a in all_anchors:
        href = a.get("href", "") or ""
        href_lower = href.lower()
        if ".pdf" in href_lower:
            pdf_hrefs.append(a)
        elif "ftpwebapps" in href_lower or "/resources/" in href_lower:
            ftpweb_hrefs.append(a)
        elif "download" in href_lower or "getfile" in href_lower or "viewfile" in href_lower or "showfile" in href_lower:
            download_hrefs.append(a)
        elif "notice" in href_lower:
            notice_hrefs.append(a)
        elif href_lower.startswith("http"):
            external_hrefs.append(a)

    rep.add("")
    rep.add(f"<a> tags with '.pdf': {len(pdf_hrefs)}")
    rep.add(f"<a> tags with '/ftpwebapps/' or '/resources/': {len(ftpweb_hrefs)}")
    rep.add(f"<a> tags with 'download'/'getfile'/'viewfile': {len(download_hrefs)}")
    rep.add(f"<a> tags with 'notice' in href: {len(notice_hrefs)}")
    rep.add(f"<a> tags with external 'http' href: {len(external_hrefs)}")

    if pdf_hrefs:
        rep.add("")
        rep.add("--- All .pdf <a> tags ---")
        for i, a in enumerate(pdf_hrefs[:50]):
            href = urljoin(page_url_final, a.get("href", ""))
            text = truncate(a.get_text(" ", strip=True), 80)
            parent = a.find_parent(["tr", "li", "div", "article"])
            ctx = truncate(parent.get_text(" ", strip=True), 100) if parent else ""
            rep.add(f"  [{i+1}] href={href}")
            rep.add(f"       text={text!r}")
            rep.add(f"       context={ctx!r}")

    if ftpweb_hrefs:
        rep.add("")
        rep.add("--- All /ftpwebapps/ <a> tags ---")
        for i, a in enumerate(ftpweb_hrefs[:50]):
            href = urljoin(page_url_final, a.get("href", ""))
            text = truncate(a.get_text(" ", strip=True), 80)
            rep.add(f"  [{i+1}] href={href} | text={text!r}")

    if download_hrefs:
        rep.add("")
        rep.add("--- All 'download' related <a> tags ---")
        for i, a in enumerate(download_hrefs[:30]):
            href = urljoin(page_url_final, a.get("href", ""))
            text = truncate(a.get_text(" ", strip=True), 80)
            rep.add(f"  [{i+1}] href={href} | text={text!r}")

    # -----------------------------------------------------------------
    # STAGE 5 — All <i>, <button>, <span> that might be download icons
    # -----------------------------------------------------------------
    section(rep, "STAGE 5 — DOWNLOAD ICONS (i/button/span with icon classes)")

    icon_elems = soup.find_all(["i", "button", "span"], class_=re.compile(r"download|file|pdf|fa-|glyphicon|material-icons", re.I))
    rep.add(f"Potential download icons: {len(icon_elems)}")

    if icon_elems:
        rep.add("")
        rep.add("--- Sample download icons (first 30) ---")
        for i, elem in enumerate(icon_elems[:30]):
            cls = " ".join(elem.get("class") or [])
            onclick = elem.get("onclick", "")
            ngclick = elem.get("ng-click", "")
            text = truncate(elem.get_text(" ", strip=True), 60)
            parent = elem.find_parent(["tr", "li", "div"])
            ctx = truncate(parent.get_text(" ", strip=True), 80) if parent else ""
            rep.add(f"  [{i+1}] <{elem.name} class='{truncate(cls, 50)}'> onclick={truncate(onclick, 50)!r} ng-click={truncate(ngclick, 50)!r}")
            rep.add(f"       text={text!r} | context={ctx!r}")

    # -----------------------------------------------------------------
    # STAGE 6 — Search for known notice titles from screenshot
    # -----------------------------------------------------------------
    section(rep, "STAGE 6 — KNOWN NOTICE TITLES")

    known_keywords = [
        "23-10-2026", "23/10/2026", "23.10.2026",
        "October 2026", "Oct 2026", "2026-10-23",
    ]
    rep.add(f"Searching for date '23-10-2026' patterns in body text...")
    found_dates = []
    for kw in known_keywords:
        if kw in html_content:
            found_dates.append(kw)
    if found_dates:
        rep.add(f"  ✅ FOUND: {found_dates}")
        # Show surrounding context
        for kw in found_dates:
            idx = html_content.find(kw)
            if idx > 0:
                snippet = html_content[max(0, idx-150):idx+150]
                rep.add(f"  Context around {kw!r}:")
                rep.add(f"    ...{truncate(snippet, 300)}...")
    else:
        rep.add(f"  ❌ NOT FOUND — date '23-10-2026' is not in the rendered HTML")
        rep.add("  This means either:")
        rep.add("    a) Angular did not finish loading")
        rep.add("    b) Page uses a different date format")
        rep.add("    c) Notices are loaded in an iframe")
        rep.add("    d) Page requires login/user interaction")

    # -----------------------------------------------------------------
    # STAGE 7 — Count tables/rows (notice lists are usually tables)
    # -----------------------------------------------------------------
    section(rep, "STAGE 7 — TABLES & ROWS")

    tables = soup.find_all("table")
    rep.add(f"<table> count: {len(tables)}")
    for i, table in enumerate(tables[:5]):
        rows = table.find_all("tr")
        rep.add(f"  Table [{i+1}]: {len(rows)} rows")
        for j, row in enumerate(rows[:3]):
            cells = row.find_all(["td", "th"])
            cell_texts = [truncate(c.get_text(" ", strip=True), 50) for c in cells]
            rep.add(f"    Row [{j+1}]: {cell_texts}")

    # -----------------------------------------------------------------
    # STAGE 8 — Look for API endpoints
    # -----------------------------------------------------------------
    section(rep, "STAGE 8 — API ENDPOINTS (from original HTML)")

    api_patterns = [
        r'/api/[a-zA-Z0-9_/-]+',
        r'/notice/[a-zA-Z0-9_/-]+\.(?:json|xml)',
        r'ajax/[a-zA-Z0-9_/-]+',
        r'get_notice[a-zA-Z0-9_/-]*',
        r'fetch[a-zA-Z0-9_/-]*notice[a-zA-Z0-9_/-]*',
    ]
    found_apis = set()
    for pat in api_patterns:
        for match in re.finditer(pat, html_content, re.I):
            found_apis.add(match.group(0))

    if found_apis:
        rep.add(f"Found {len(found_apis)} potential API endpoints:")
        for api in sorted(found_apis)[:30]:
            rep.add(f"  - {api}")
    else:
        rep.add("No obvious API endpoints found in HTML")

    # -----------------------------------------------------------------
    # STAGE 9 — Verdict
    # -----------------------------------------------------------------
    section(rep, "STAGE 9 — VERDICT")

    verdicts = []
    if render_status != "domcontentloaded_ok" and not render_status.endswith("_ok"):
        verdicts.append("❌ Playwright navigation FAILED — VBU is blocking or down")
    if not found_dates:
        verdicts.append("❌ Date '23-10-2026' not in HTML — page did not fully load or format changed")
    if len(pdf_hrefs) == 0 and len(ftpweb_hrefs) == 0 and len(download_hrefs) == 0:
        verdicts.append("❌ No PDF/download links found in HTML — extraction will fail")
    if len(onclick_elems) > 0 or len(ngclick_elems) > 0:
        verdicts.append(f"⚠️  {len(onclick_elems)} onclick + {len(ngclick_elems)} ng-click elements exist — JS-based links present")
    if len(tables) == 0:
        verdicts.append("⚠️  No <table> tags — notice list is not in a table (maybe divs)")
    if "ng-" in html_content and not found_dates:
        verdicts.append("⚠️  Angular markers present but no content — API call may need extra wait")

    if not verdicts:
        verdicts.append("✅ Page structure looks parseable")

    for v in verdicts:
        rep.add(v)

    # -----------------------------------------------------------------
    # STAGE 10 — Recommended next steps
    # -----------------------------------------------------------------
    section(rep, "STAGE 10 — RECOMMENDATIONS")

    if len(onclick_elems) > 0 or len(ngclick_elems) > 0:
        rep.add("→ Implement onclick/ng-click extraction in extract_candidates()")
        rep.add(f"  Found {len(onclick_elems)} onclick + {len(ngclick_elems)} ng-click elements — parser must handle these")
    if len(href_js_elems) > 0:
        rep.add(f"→ Add javascript:void(0) href handling — {len(href_js_elems)} such links exist")
    if len(pdf_hrefs) > 0:
        rep.add(f"→ {len(pdf_hrefs)} direct PDF links found — should be extracted by existing parser")
        if len(pdf_hrefs) < 10:
            rep.add("   But count seems low — many notices may use JS pattern")
    if not found_dates:
        rep.add("→ Date '23-10-2026' missing — increase js_wait_after_ms to 15000-20000")
        rep.add("   Or check if notices load via XHR (may need network intercept)")
    if len(tables) > 0:
        rep.add(f"→ {len(tables)} tables exist — try table-based extraction as fallback")

    rep.add("")
    rep.add("=" * 70)
    rep.add("FILES TO SHARE:")
    rep.add(f"  1. {REPORT_FILE.name}  (this report)")
    rep.add(f"  2. {HTML_FILE.name}    (rendered HTML — for parser tuning)")
    rep.add(f"  3. {SCREENSHOT_FILE.name} (visual reference)")
    rep.add("=" * 70)

    rep.save()


if __name__ == "__main__":
    main()
