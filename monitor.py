# -*- coding: utf-8 -*-
import base64
import hashlib
import html
import importlib
import io
import json
import os
import re
import sys
import time
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse, unquote, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

import pdfplumber
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _try_import(*names):
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


fitz = _try_import("pymupdf", "fitz")

_rapidocr_mod = _try_import("rapidocr_onnxruntime", "rapidocr")
RapidOCR = getattr(_rapidocr_mod, "RapidOCR", None) if _rapidocr_mod is not None else None
_RAPIDOCR_AVAILABLE = RapidOCR is not None

try:
    from paddleocr import PaddleOCR
    _PADDLEOCR_AVAILABLE = True
except ImportError:
    PaddleOCR = None
    _PADDLEOCR_AVAILABLE = False

pytesseract = _try_import("pytesseract")
_PIL_Image = _try_import("PIL.Image")
_TESSERACT_AVAILABLE = pytesseract is not None and _PIL_Image is not None

try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    PlaywrightError = Exception
    _PLAYWRIGHT_AVAILABLE = False

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "websites.json"
STATE_FILE = BASE / "state.json"

FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
FUZZY_DUPLICATE_THRESHOLD = 94
FUZZY_CROSS_PATH_THRESHOLD = 96
TELEGRAM_MAX_ATTEMPTS = 100
TELEGRAM_RETRY_WINDOW_HOURS = 168
TELEGRAM_RETRY_BASE_MINUTES = 20
TELEGRAM_RETRY_MAX_MINUTES = 360

MAX_PDF_SEND_BYTES = 45 * 1024 * 1024
MAX_GEMINI_PDF_BYTES = 18 * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "8"))
MAX_PDF_TEXT_CHARS = 4000
MAX_PDF_RAW_TEXT_CHARS = 8000
MAX_PDF_ATTEMPTS = 3
TELEGRAM_SAFE_LIMIT = 4000
TELEGRAM_CAPTION_LIMIT = 1024

_RUNTIME_PDF_CACHE_MAX_ITEMS = 20
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "16384"))

OCR_ENABLED = os.getenv("OCR_ENABLED", "true").strip().lower() == "true"
OCR_DPI = int(os.getenv("OCR_DPI", "250"))
OCR_MIN_TEXT_CHARS = int(os.getenv("OCR_MIN_TEXT_CHARS", "200"))
OCR_MAX_WORKERS = int(os.getenv("OCR_MAX_WORKERS", "2"))
_OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "4"))
MAX_PDF_OCR_SECONDS = int(os.getenv("MAX_PDF_OCR_SECONDS", "120"))

TESSERACT_ENABLED = os.getenv("TESSERACT_ENABLED", "true").strip().lower() == "true"
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "hin+eng").strip() or "hin+eng"
TESSERACT_PSM = os.getenv("TESSERACT_PSM", "6").strip()

PADDLEOCR_ENABLED = os.getenv("PADDLEOCR_ENABLED", "true").strip().lower() == "true"

GEMINI_PDF_OCR_ENABLED = os.getenv("GEMINI_PDF_OCR_ENABLED", "true").strip().lower() == "true"
SEND_PDF_ENABLED = os.getenv("SEND_PDF_ENABLED", "true").strip().lower() == "true"

JS_RENDER_ENABLED = os.getenv("JS_RENDER_ENABLED", "true").strip().lower() == "true"
JS_RENDER_TIMEOUT_MS = int(os.getenv("JS_RENDER_TIMEOUT_MS", "25000"))
JS_WAIT_UNTIL = os.getenv("JS_WAIT_UNTIL", "domcontentloaded").strip() or "domcontentloaded"
JS_THIN_TEXT_THRESHOLD = int(os.getenv("JS_THIN_TEXT_THRESHOLD", "200"))
JS_MAX_RENDER_PER_SITE = int(os.getenv("JS_MAX_RENDER_PER_SITE", "40"))
JS_WAIT_AFTER_MS = max(0, min(60000, int(os.getenv("JS_WAIT_AFTER_MS", "2500"))))
JS_SCROLL_TO_BOTTOM = os.getenv("JS_SCROLL_TO_BOTTOM", "false").strip().lower() == "true"
JS_WAIT_FOR_SELECTOR_DEFAULT = os.getenv("JS_WAIT_FOR_SELECTOR_DEFAULT", "").strip()

NOTIFY_LANGUAGE = os.getenv("NOTIFY_LANGUAGE", "both").strip().lower()
if NOTIFY_LANGUAGE not in {"both", "hi", "en"}:
    NOTIFY_LANGUAGE = "both"

STALE_NOTICE_DAYS = int(os.getenv("STALE_NOTICE_DAYS", "15"))
OLD_YEAR_HINT_YEARS_BACK = int(os.getenv("OLD_YEAR_HINT_YEARS_BACK", "2"))
ETAG_CHECK_MAX_AGE_DAYS = int(os.getenv("ETAG_CHECK_MAX_AGE_DAYS", "14"))

USER_AGENT = os.getenv(
    "MONITOR_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

JS_RENDER_USER_AGENT = os.getenv(
    "JS_RENDER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

STRONG_KEYWORDS = [
    "admission", "result", "admit card", "answer key", "examination",
    "exam", "semester", "revaluation", "migration", "certificate",
    "scholarship", "fee", "syllabus", "timetable", "counselling",
    "recruitment", "vacancy", "tender", "notice", "notification",
    "placement", "hostel", "convocation", "registration", "enrollment",
    "merit", "marksheet", "transcript", "bonafide", "grading", "cgpa", "gpa",
    "phd", "ph.d", "research", "internship", "challan", "demand draft",
    "eligibility", "qualification", "provisional", "backlog", "reappear",
    "supplementary", "marks", "score",
    "प्रवेश", "परिणाम", "प्रवेश पत्र", "उत्तर कुंजी", "परीक्षा",
    "सेमेस्टर", "पुनर्मूल्यांकन", "माइग्रेशन", "प्रमाण पत्र",
    "छात्रवृत्ति", "शुल्क", "पाठ्यक्रम", "समय सारिणी", "काउंसलिंग",
    "भर्ती", "निविदा", "सूचना", "अधिसूचना", "प्लेसमेंट", "छात्रावास",
    "दीक्षांत समारोह", "पंजीकरण", "नामांकन",
    "मेरिट", "अंकतालिका", "अनुसंधान"
]

_DEVA_DIGIT_TRANS = str.maketrans("०१२३४५६७८९", "0123456789")
_RUNTIME_PDF_CACHE: Dict[str, bytes] = {}
_RUNTIME_PDF_CACHE_LOCK = threading.Lock()
_OCR_CONTENT_CACHE: Dict[str, Tuple[str, str, str]] = {}
_OCR_CONTENT_CACHE_MAX_ITEMS = 30
_OCR_CONTENT_LOCK = threading.Lock()

_GEMINI_API_KEY = ""
_GEMINI_KEYS_POOL: List[str] = []
_GEMINI_KEY_INDEX: int = 0
_GEMINI_DEAD_KEYS: set = set()
_GEMINI_KEY_LOCK = threading.Lock()

_MODELS_DISCOVERED: bool = False
_DYNAMIC_MODELS: List[str] = []
_DEAD_MODELS: set = set()
_GEMINI_QUOTA_EXHAUSTED: bool = False

_RAPIDOCR_INSTANCE = None
_PADDLEOCR_INSTANCE = None
_OCR_INIT_LOCK = threading.Lock()

_ROBOTS_CACHE = {}
_TESSERACT_WARNED: bool = False
_JS_RENDER_COUNT = 0
_JS_RENDER_COUNT_LOCK = threading.Lock()

_NAV_SIGNALS: Tuple[str, ...] = (
    "home", "homepage", "about us", "about", "contact us", "contact",
    "feedback", "site map", "sitemap", "skip to content",
    "accessibility", "screen reader",
    "whats new", "what's new", "whatsnew", "latest news", "latest update",
    "latest updates", "photo gallery", "video gallery", "gallery",
    "facebook", "twitter", "instagram", "youtube", "whatsapp", "linkedin",
    "social media", "follow us", "share",
    "login", "sign in", "sign up", "register", "logout",
    "main menu", "menu", "navigation",
    "मुख्य पृष्ठ", "होम", "हमारे बारे में", "संपर्क करें", "संपर्क",
    "फीडबैक", "साइट मैप", "स्क्रीन रीडर", "महत्वपूर्ण लिंक", "त्वरित लिंक",
    "नई खबरें", "ताज़ा खबर", "अंतिम अपडेट", "गैलरी", "फोटो गैलरी",
)

_NOTICE_SIGNALS: Tuple[str, ...] = (
    "admission", "result", "admit card", "answer key", "examination",
    "exam", "revaluation", "migration", "scholarship", "fee", "syllabus",
    "timetable", "counselling", "recruitment", "vacancy", "tender",
    "notice", "notification", "placement", "convocation", "registration",
    "प्रवेश", "परिणाम", "प्रवेश पत्र", "उत्तर कुंजी", "परीक्षा",
    "पुनर्मूल्यांकन", "छात्रवृत्ति", "शुल्क", "भर्ती", "निविदा", "सूचना",
)

_EXPLICIT_DUMP_SIGNALS: Tuple[str, ...] = (
    "skip to content", "site map", "sitemap",
    "मुख्य पृष्ठ", "महत्वपूर्ण लिंक", "त्वरित लिंक",
)

_STRICT_EARLY_NAV_SIGNALS: Tuple[str, ...] = (
    "home", "homepage", "whats new", "what's new", "whatsnew",
    "facebook", "twitter", "instagram", "youtube", "whatsapp",
    "linkedin", "skip to content", "sitemap", "site map",
    "मुख्य पृष्ठ", "होम", "साइट मैप",
)

_DEVANAGARI_DELIMS = r"[\s\|•\-–—,:;()\[\]{}/\\?!\"'`]"
_LATIN_LEFT_BOUNDARY = r"(?<![A-Za-z0-9_])"
_LATIN_RIGHT_BOUNDARY = r"(?![A-Za-z0-9_])"

_TITLE_STRIP_SIGNALS: Tuple[str, ...] = tuple(sorted(_STRICT_EARLY_NAV_SIGNALS, key=len, reverse=True))

_JS_REQUIRED_MARKERS = (
    '<div id="root"', '<div id="app"', '<div id="__next"', '<div id="__nuxt"',
    'ng-app', 'ng-version', 'data-reactroot', 'data-v-app',
    'please enable javascript', 'javascript is disabled',
    'you need to enable javascript', 'enable javascript and cookies',
    'enable javascript to run', 'loading...', 'loading…',
    'जावास्क्रिप्ट सक्षम', 'ng-cloak', 'ng-controller', 'ng-repeat', 'ng-bind', 'ng-view',
    'ng-include', 'ng-scope', 'ng-binding', 'angular.js', 'angular.min.js',
    'angular-route', 'react.production', 'react-dom.production',
    'vue.min.js', 'vue.runtime', 'data-v-', '__next_data__', '_next/static',
    '__nuxt__', '_nuxt/',
)

_JS_PDF_VIEWER_MARKERS = (
    "pdfjs", "pdf.js", "pdfobject", "viewer.html",
    "docs.google.com/viewer", "drive.google.com/file",
    "googleusercontent.com/viewer", "pdfjs-viewer", "web/viewer.html",
)

_NOTICE_KEYWORDS = [
    "notice", "notification", "announcement", "circular", "advertisement", "publicity",
    "exam", "examination", "result", "admit", "form",
    "tender", "quotation", "bid", "rfp", "eoi", "nit",
    "recruitment", "vacancy", "appointment", "interview", "apprentice",
    "syllabus", "curriculum",
    "date sheet", "date extended", "last date", "due date", "exam date",
    "schedule", "timetable", "time-table", "time table",
    "calendar",
    "fee", "payment", "challan",
    "merit", "marksheet", "score", "grade", "cgpa", "gpa",
    "scholarship",
    "counselling", "counseling",
    "migration", "certificate", "transcript", "bonafide", "provisional",
    "registration", "enrollment", "enrolment",
    "convocation", "placement", "hostel",
    "prospectus",
    "semester", "revaluation", "rechecking",
    "internship",
    "eligibility", "qualification",
    "सूचना", "अधिसूचना", "विज्ञापन", "नोटिस",
    "परीक्षा", "परीक्षा कार्यक्रम", "परीक्षा केंद्र",
    "परिणाम", "रिजल्ट",
    "प्रवेश", "प्रवेश पत्र",
    "भर्ती", "नियुक्ति", "साक्षात्कार", "रिक्त पद",
    "निविदा", "कोटेशन",
    "छात्रवृत्ति", "शुल्क",
    "पाठ्यक्रम", "समय सारिणी", "समय", "कैलेंडर",
    "मेरिट", "अंकतालिका",
    "काउंसलिंग",
    "माइग्रेशन", "प्रमाण पत्र",
    "पंजीकरण", "नामांकन",
    "दीक्षांत",
    "पुनर्मूल्यांकन",
    "सेमेस्टर",
    "प्लेसमेंट",
    "छात्रावास",
]


def _page_requires_js(html_text):
    if not html_text: return True
    lower = html_text.lower()
    for marker in _JS_REQUIRED_MARKERS:
        if marker in lower: return True
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style", "noscript", "template", "svg"]): tag.decompose()
        body = soup.body or soup
        text = body.get_text(" ", strip=True)
        if len(text) < JS_THIN_TEXT_THRESHOLD: return True
        anchors = soup.find_all("a", href=True)
        if len(anchors) < 3 and len(text) < 400: return True
    except Exception: return True
    return False


def _has_unrendered_angular_templates(html_text):
    return bool(re.search(r"\{\{[^}]{1,200}\}\}", html_text or ""))


def _should_render_js(site, html_text, page_url):
    mode = str((site or {}).get("js_render", "auto")).strip().lower()
    if mode == "never": return False
    if mode == "always": return True
    fragment = (urlparse(page_url).fragment or "").strip()
    if fragment.startswith("/") or fragment.startswith("!/"): return True
    return _page_requires_js(html_text)


def _html_looks_like_js_pdf_viewer(html_text):
    if not html_text: return False
    lower = html_text.lower()
    return any(m in lower for m in _JS_PDF_VIEWER_MARKERS)


def _render_with_playwright(url, timeout_ms=None, wait_until=None, wait_for_selector=None, wait_after_ms=None, scroll_bottom=False, selector_timeout_ms=None):
    if not _PLAYWRIGHT_AVAILABLE or not JS_RENDER_ENABLED: return None, url, "playwright_disabled"
    timeout_ms = max(1000, int(timeout_ms or JS_RENDER_TIMEOUT_MS))
    wait_until = wait_until or JS_WAIT_UNTIL
    if wait_until == "networkidle": wait_until = "domcontentloaded"
    wait_after_ms = JS_WAIT_AFTER_MS if wait_after_ms is None else wait_after_ms
    try: wait_after_ms = max(0, min(60000, int(wait_after_ms)))
    except (TypeError, ValueError): wait_after_ms = JS_WAIT_AFTER_MS
    selector = str(wait_for_selector or JS_WAIT_FOR_SELECTOR_DEFAULT or "").strip()
    try: selector_timeout_ms = max(1000, min(90000, int(selector_timeout_ms or 10000)))
    except (TypeError, ValueError, OverflowError): selector_timeout_ms = 10000

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-blink-features=AutomationControlled"])
            try:
                context = browser.new_context(user_agent=JS_RENDER_USER_AGENT, locale="en-IN", viewport={"width": 1366, "height": 900}, java_script_enabled=True, ignore_https_errors=True, extra_http_headers={"Accept-Language": "en-IN,en;q=0.9,hi;q=0.8"})
                # FIX #1: Removed context.route(...) — VBU AngularJS needs images/fonts/media to load
                # for window.load event to fire, which triggers fetch_notice API call.
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                try: page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                except Exception:
                    try: page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    except Exception as exc: return None, url, f"playwright_nav_error:{type(exc).__name__}"
                try: page.wait_for_load_state("networkidle", timeout=min(8000, timeout_ms))
                except Exception: pass
                if selector:
                    try: page.wait_for_selector(selector, state="attached", timeout=min(selector_timeout_ms, timeout_ms))
                    except Exception: pass
                if wait_after_ms:
                    try: page.wait_for_timeout(wait_after_ms)
                    except Exception: pass
                if scroll_bottom:
                    try:
                        previous_height = -1
                        for _ in range(4):
                            height = page.evaluate("() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
                            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                            page.wait_for_timeout(600)
                            if height == previous_height: break
                            previous_height = height
                        page.evaluate("() => window.scrollTo(0, 0)")
                    except Exception: pass
                try:
                    html_content = page.content()
                    if _has_unrendered_angular_templates(html_content):
                        page.wait_for_timeout(min(3000, max(500, wait_after_ms)))
                        html_content = page.content()
                except Exception as exc: return None, url, f"playwright_content_error:{type(exc).__name__}"
                if html_content:
                    global _JS_RENDER_COUNT
                    with _JS_RENDER_COUNT_LOCK: _JS_RENDER_COUNT += 1
                return html_content, (page.url or url), "playwright_ok"
            finally:
                try: browser.close()
                except Exception: pass
    except Exception as exc:
        print(f"[WARN] Playwright render failed for {url[:80]}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None, url, f"playwright_error:{type(exc).__name__}"


def _is_devanagari_str(text: str) -> bool:
    return any("\u0900" <= c <= "\u097F" and c != "\u0964" for c in (text or ""))


def _match_signal(text: str, signal: str) -> bool:
    if not text or not signal: return False
    sig = signal.strip().lower()
    if not sig: return False
    lower = text.lower()
    if _is_devanagari_str(sig): pattern = (r"(?:^|" + _DEVANAGARI_DELIMS + r")" + re.escape(sig) + r"(?:$|" + _DEVANAGARI_DELIMS + r")")
    else: pattern = _LATIN_LEFT_BOUNDARY + re.escape(sig) + _LATIN_RIGHT_BOUNDARY
    return bool(re.search(pattern, lower))


def _count_nav_signals(text: str) -> int:
    if not text: return 0
    remaining = text.lower()
    matched = 0
    for sig in sorted(_NAV_SIGNALS, key=len, reverse=True):
        sig_lower = sig.strip().lower()
        if not sig_lower: continue
        if _is_devanagari_str(sig_lower): pattern = (r"(?:^|" + _DEVANAGARI_DELIMS + r")" + re.escape(sig_lower) + r"(?:$|" + _DEVANAGARI_DELIMS + r")")
        else: pattern = _LATIN_LEFT_BOUNDARY + re.escape(sig_lower) + _LATIN_RIGHT_BOUNDARY
        new_remaining, n = re.subn(pattern, " ", remaining)
        if n > 0:
            matched += 1
            remaining = new_remaining
    return matched


_NOTICE_DOC_URL_HINTS = (
    "/writereaddata/", "/uploadfile/", "/uploads/", "/downloadfile", "/download_file", "/getfile",
    "/showfile", "/viewfile", "/filedownload", "/attachment", "/documents/", "/docs/",
    "/notice/", "/notice_", "/download/",
)


def _is_notice_document_url(url):
    if not url: return False
    u = url.lower()
    if u.endswith(".pdf") or ".pdf?" in u: return True
    if _looks_like_pdf_url(url): return True
    try: path = (urlparse(url).path or "").lower()
    except Exception: return False
    return any(hint in path for hint in _NOTICE_DOC_URL_HINTS)


def _looks_like_menu_dump(title):
    if not title: return False
    words = title.split()
    if len(words) < 4: return False
    first_words = " ".join(words[:8]).lower()
    count = sum(1 for sig in _STRICT_EARLY_NAV_SIGNALS if _match_signal(first_words, sig))
    return count >= 2


def _is_garbage_title(title):
    if not title: return True
    t = title.strip()
    if not t: return True
    if re.search(r"\{\{[^}]{0,200}\}\}", t): return True
    if re.match(r"^\[(?:ng|data-ng|x-ng)[\w-]*\]", t, re.I) or re.match(r"^\((?:click|change|input|submit|keyup|keydown|mouseover|mouseout)\)", t, re.I) or re.match(r"^\*ng[A-Z][\w-]*", t) or re.match(r"^(?:ng-|data-ng-|x-ng-)[\w-]+", t, re.I): return True
    if t.casefold() in {"undefined", "null", "nan", "{}", "[]", "{{}}"}: return True
    stripped = re.sub(r"^[\s\|•\-–—,:;.]+|[\s\|•\-–—,:;.]+$", "", t)
    if not stripped or len(stripped) < 4: return True
    if re.match(r"^[\d\s/\-.:()\[\]|,]+$", stripped): return True
    if _is_generic_title(stripped): return True
    if _is_language_selector(stripped): return True
    return False


def _filename_to_title(url):
    try:
        path = unquote(urlparse(url).path or "")
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if not name: return ""
        if "." in name: name = name.rsplit(".", 1)[0]
        name = re.sub(r"[_\-]+", " ", name)
        name = re.sub(r"^(pdf|doc|docx|file|download|notice|document|scan|img|image)\s+", "", name, flags=re.I)
        name = re.sub(r"\s+", " ", name).strip()
        if len(name) < 10 or _is_generic_title(name): return ""
        if re.match(r"^[\d\s/\-.:()\[\]]+$", name): return ""
        return clean_text(name, 300)
    except Exception: return ""


def _strip_nav_prefix(title):
    if not title: return title
    scan_region = title[:400]
    last_end = 0
    for sig in _TITLE_STRIP_SIGNALS:
        sig_lower = sig.lower()
        if _is_devanagari_str(sig_lower): pattern = re.compile(r"(?:^|" + _DEVANAGARI_DELIMS + r")" + re.escape(sig_lower) + r"(?:$|" + _DEVANAGARI_DELIMS + r")")
        else: pattern = re.compile(_LATIN_LEFT_BOUNDARY + re.escape(sig_lower) + _LATIN_RIGHT_BOUNDARY)
        for m in pattern.finditer(scan_region.lower()):
            if m.end() > last_end: last_end = m.end()
    if last_end <= 0: return title
    rest = title[last_end:].lstrip(" \t:-–—|•,;")
    rest = re.sub(r"^\d{1,2}[./\-]\d{1,2}[./\-]\d{4}[\s:;\-–—]+", "", rest)
    for marker in (r"\bPublished\s+on\s*:", r"\bPosted\s+on\s*:", r"\bUploaded\s+on\s*:", r"\bDate\s+of\s+Publication\s*:", r"\bप्रकाशित\s*:"):
        m = re.search(marker, rest, re.I)
        if m and m.start() >= 15: rest = rest[:m.start()].rstrip(" .;,-")
    rest = rest.strip()
    return rest if len(rest) >= 15 else title


def _cleanup_menu_dump_title(title, url="", explicit=""):
    if explicit and not _is_generic_title(explicit) and not _is_language_selector(explicit) and not _looks_like_menu_dump(explicit) and not _is_garbage_title(explicit): return clean_text(explicit, 300)
    if url and _is_notice_document_url(url):
        name = _filename_to_title(url)
        if name: return name
    if title:
        stripped = _strip_nav_prefix(title)
        if stripped and stripped != title and len(stripped) >= 15: return clean_text(stripped, 300)
    if url:
        slug = _title_from_url(url, min_len=8)
        if slug: return slug
    return title


def _resolve_title(item):
    explicit = clean_text(item.get("explicit_title") or "", 300)
    anchor = clean_text(item.get("title") or "", 300)
    context = clean_text(item.get("context") or "", 200)
    url = item.get("url", "")

    def _ok(v):
        if not v: return False
        if v.startswith("http://") or v.startswith("https://"): return False
        return (not _is_generic_title(v) and not _is_language_selector(v)
                and not _looks_like_menu_dump(v) and not _is_garbage_title(v))
    if _ok(explicit): return explicit
    if _ok(anchor): return anchor
    for candidate in (explicit, anchor):
        if candidate and (_looks_like_menu_dump(candidate) or _is_garbage_title(candidate)):
            cleaned = _cleanup_menu_dump_title(candidate, url=url, explicit="")
            if cleaned and cleaned != candidate and _ok(cleaned): return cleaned
    if context and not _is_generic_title(context) and not _is_language_selector(context) and not _looks_like_menu_dump(context):
        if not (context.startswith("http://") or context.startswith("https://")):
            return context[:200]
    slug = _title_from_url(url, min_len=5)
    if slug: return slug
    return "Notice"


def _get_rapidocr():
    global _RAPIDOCR_INSTANCE
    if _RAPIDOCR_INSTANCE is None and _RAPIDOCR_AVAILABLE:
        with _OCR_INIT_LOCK:
            if _RAPIDOCR_INSTANCE is not None: return _RAPIDOCR_INSTANCE
            try:
                _RAPIDOCR_INSTANCE = RapidOCR(params={"Rec.lang_type": "devanagari"})
                print("[INFO] RapidOCR initialized with Devanagari model.", file=sys.stderr)
            except Exception as exc:
                print(f"[WARN] RapidOCR Devanagari init failed: {exc}", file=sys.stderr)
                try:
                    _RAPIDOCR_INSTANCE = RapidOCR()
                    print("[WARN] Falling back to default RapidOCR.", file=sys.stderr)
                except Exception as exc2:
                    print(f"[WARN] RapidOCR default init failed: {exc2}", file=sys.stderr)
                    return None
    return _RAPIDOCR_INSTANCE


def _get_paddleocr():
    global _PADDLEOCR_INSTANCE
    if _PADDLEOCR_INSTANCE is None and _PADDLEOCR_AVAILABLE:
        with _OCR_INIT_LOCK:
            if _PADDLEOCR_INSTANCE is not None: return _PADDLEOCR_INSTANCE
            try:
                _PADDLEOCR_INSTANCE = PaddleOCR(use_angle_cls=True, lang="hi", show_log=False, use_gpu=False)
                print("[INFO] PaddleOCR (Devanagari) initialized.", file=sys.stderr)
            except Exception as exc:
                print(f"[WARN] PaddleOCR init failed: {exc}", file=sys.stderr)
                return None
    return _PADDLEOCR_INSTANCE


def _extract_pdf_text_paddleocr(content):
    if not PADDLEOCR_ENABLED or not _PADDLEOCR_AVAILABLE: return ""
    ocr = _get_paddleocr()
    if ocr is None: return ""
    parts = []
    try:
        import numpy as np
        try:
            import cv2
            has_cv2 = True
        except ImportError:
            has_cv2 = False
        doc = fitz.open(stream=content, filetype="pdf")
        pages_to_process = min(len(doc), MAX_PDF_PAGES, _OCR_MAX_PAGES)
        for page_num in range(pages_to_process):
            try:
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=OCR_DPI)
                img_bytes = pix.tobytes("png")
                if has_cv2:
                    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
                else:
                    from PIL import Image
                    img = np.array(Image.open(io.BytesIO(img_bytes)).convert("RGB"))
                    img = img[:, :, ::-1]
                result = ocr.ocr(img, cls=True)
                if result and result[0]:
                    for line in result[0]:
                        try: parts.append(line[1][0])
                        except (IndexError, TypeError): continue
            except Exception as exc:
                print(f"[WARN] PaddleOCR page {page_num+1} failed: {exc}", file=sys.stderr)
        doc.close()
    except Exception as exc:
        print(f"[WARN] PaddleOCR PDF processing failed: {exc}", file=sys.stderr)
    return "\n\n".join(parts)


def _cache_put(url, content):
    with _RUNTIME_PDF_CACHE_LOCK:
        if url in _RUNTIME_PDF_CACHE: return
        if len(_RUNTIME_PDF_CACHE) >= _RUNTIME_PDF_CACHE_MAX_ITEMS:
            try:
                first_key = next(iter(_RUNTIME_PDF_CACHE))
                del _RUNTIME_PDF_CACHE[first_key]
            except (StopIteration, KeyError): pass
        _RUNTIME_PDF_CACHE[url] = content


def _get_ocr_cache(content_hash):
    with _OCR_CONTENT_LOCK:
        return _OCR_CONTENT_CACHE.get(content_hash)


def _put_ocr_cache(content_hash, cleaned, raw_for_title, method):
    with _OCR_CONTENT_LOCK:
        if len(_OCR_CONTENT_CACHE) >= _OCR_CONTENT_CACHE_MAX_ITEMS:
            try: _OCR_CONTENT_CACHE.pop(next(iter(_OCR_CONTENT_CACHE)))
            except (StopIteration, KeyError): pass
        _OCR_CONTENT_CACHE[content_hash] = (cleaned, raw_for_title, method)


def _load_gemini_keys():
    keys = []
    seen = set()
    def add(raw):
        for part in (raw or "").split(","):
            key = part.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    add(os.getenv("GEMINI_API_KEYS", ""))
    for i in range(1, 11): add(os.getenv(f"GEMINI_API_KEY_{i}", ""))
    add(os.getenv("GEMINI_API_KEY", ""))
    return keys


def _get_next_gemini_key():
    global _GEMINI_KEY_INDEX
    with _GEMINI_KEY_LOCK:
        total = len(_GEMINI_KEYS_POOL)
        if not total: return None
        for _ in range(total):
            idx = _GEMINI_KEY_INDEX % total
            _GEMINI_KEY_INDEX = (idx + 1) % total
            key = _GEMINI_KEYS_POOL[idx]
            if key not in _GEMINI_DEAD_KEYS: return key
    return None


def _has_live_gemini_keys():
    with _GEMINI_KEY_LOCK: return any(key not in _GEMINI_DEAD_KEYS for key in _GEMINI_KEYS_POOL)


def _mark_gemini_key_dead(key, reason="quota exhausted"):
    if not key: return
    with _GEMINI_KEY_LOCK:
        if key in _GEMINI_DEAD_KEYS: return
        _GEMINI_DEAD_KEYS.add(key)
        remaining = max(0, len(_GEMINI_KEYS_POOL) - len(_GEMINI_DEAD_KEYS))
    try: index = _GEMINI_KEYS_POOL.index(key) + 1
    except ValueError: index = 0
    print(f"[GEMINI] Key #{index} skipped ({reason}); {remaining} remain.", file=sys.stderr)


def _gemini_post_with_rotation(endpoint, payload, timeout):
    if not _GEMINI_KEYS_POOL: return None, None, "no_keys"
    attempted = set()
    last_reason = "all_keys_failed"
    for _ in range(len(_GEMINI_KEYS_POOL)):
        key = _get_next_gemini_key()
        if key is None: return None, None, "all_keys_quota_exhausted"
        if key in attempted: continue
        attempted.add(key)
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        try: response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_reason = "network_error"
            print(f"[WARN] Gemini network error: {type(exc).__name__}", file=sys.stderr)
            continue
        if response.status_code == 429:
            try:
                err = response.json().get("error", {})
                msg = str(err.get("message", "")).lower()
                status = str(err.get("status", "")).upper()
            except Exception: msg, status = response.text.lower(), ""
            quota = status == "RESOURCE_EXHAUSTED" or any(term in msg for term in ("quota", "daily limit", "per day", "limit: 0"))
            if quota:
                _mark_gemini_key_dead(key, "quota exhausted")
                last_reason = "quota_exhausted"
                continue
            last_reason = "rate_limited"
            continue
        if response.status_code in (401, 403):
            _mark_gemini_key_dead(key, f"HTTP {response.status_code}")
            last_reason = "key_rejected"
            continue
        return response, key, None
    return None, None, last_reason


def _discover_models():
    global _MODELS_DISCOVERED, _DYNAMIC_MODELS
    if _MODELS_DISCOVERED: return
    print("[INFO] Discovering available Gemini models...", file=sys.stderr)
    try:
        data = None
        for _ in range(max(1, len(_GEMINI_KEYS_POOL))):
            key = _get_next_gemini_key()
            if key is None: break
            try:
                response = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": key}, timeout=15)
                if response.status_code == 429:
                    _mark_gemini_key_dead(key, "model-discovery quota")
                    continue
                if response.status_code in (401, 403):
                    _mark_gemini_key_dead(key, f"model-discovery {response.status_code}")
                    continue
                response.raise_for_status()
                data = response.json()
                break
            except Exception: continue
        if data is None: raise RuntimeError("No configured Gemini key succeeded")
        valid_models = []
        for model in data.get("models", []):
            methods = model.get("supportedGenerationMethods", [])
            if "generateContent" not in methods: continue
            model_id = model["name"].replace("models/", "")
            if any(skip in model_id.lower() for skip in ("embedding", "aqa", "imagen", "veo")): continue
            valid_models.append(model_id)
        def _priority(mid):
            m = mid.lower()
            if "flash-lite" in m: return 0
            if "flash" in m: return 1
            if "pro" in m: return 2
            return 3
        _DYNAMIC_MODELS = sorted(valid_models, key=lambda m: (_priority(m), m))
        if not _DYNAMIC_MODELS: _DYNAMIC_MODELS = list(FALLBACK_MODELS)
        print(f"[INFO] Discovered {len(_DYNAMIC_MODELS)} models.", file=sys.stderr)
    except Exception as exc:
        print(f"[WARN] Model discovery failed: {exc}. Using fallback.", file=sys.stderr)
        _DYNAMIC_MODELS = list(FALLBACK_MODELS)
    _MODELS_DISCOVERED = True


def _pdf_content_fingerprint(content):
    if not content: return ""
    if fitz is not None:
        try:
            digest = hashlib.sha256()
            with fitz.open(stream=content, filetype="pdf") as doc:
                for page in doc[:MAX_PDF_PAGES]:
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.75, 0.75), colorspace=fitz.csGRAY, alpha=False)
                    digest.update(f"{pix.width}x{pix.height}:".encode("ascii"))
                    digest.update(pix.samples)
            return "visual-v1:" + digest.hexdigest()
        except Exception as exc: print(f"[WARN] PDF visual fingerprint failed: {exc}", file=sys.stderr)
    try:
        text = _extract_pdf_text_plumber(content)
        normalized = re.sub(r"\s+", " ", text or "").strip().casefold()
        if len(normalized) >= 30: return "text-v1:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    except Exception: pass
    return "bytes-v1:" + hashlib.sha256(content).hexdigest()


def _parse_utc_timestamp(value):
    if not value: return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError): return None


def _telegram_retry_due(record, now=None):
    now = now or datetime.now(timezone.utc)
    first_failed = _parse_utc_timestamp(record.get("telegram_first_failed_at"))
    if first_failed and (now - first_failed).total_seconds() > TELEGRAM_RETRY_WINDOW_HOURS * 3600: return False
    next_attempt = _parse_utc_timestamp(record.get("telegram_next_attempt_at"))
    return next_attempt is None or next_attempt <= now


def _schedule_telegram_retry(record, now=None):
    now = now or datetime.now(timezone.utc)
    if not record.get("telegram_first_failed_at"): record["telegram_first_failed_at"] = now.isoformat()
    attempts = max(1, int(record.get("telegram_attempts", 1) or 1))
    delay_minutes = min(TELEGRAM_RETRY_BASE_MINUTES * (2 ** min(attempts - 1, 8)), TELEGRAM_RETRY_MAX_MINUTES)
    record["telegram_next_attempt_at"] = (now + timedelta(minutes=delay_minutes)).isoformat()


MEANINGFUL_FIELDS = {
    "admission_last_date": [r"(?:last date|last date to apply|apply(?:ing)? till|closing date|admission last date|अंतिम तिथि|आवेदन की अंतिम तिथि)\s*[:：-]?\s*([^\n,;]{1,50})"],
    "exam_date": [r"(?:exam(?:ination)? date|परीक्षा तिथि|परीक्षा दिनांक)\s*[:：-]?\s*([^\n,;]{1,50})"],
    "result_date": [r"(?:result date|परिणाम तिथि|रिजल्ट जारी)\s*[:：-]?\s*([^\n,;]{1,50})"],
    "revaluation_last_date": [r"(?:revaluation last date|revaluation deadline|पुनर्मूल्यांकन अंतिम तिथि)\s*[:：-]?\s*([^\n,;]{1,50})"],
    "fee_last_date": [r"(?:fee submission last date|last date to pay fee|शुल्क जमा अंतिम तिथि)\s*[:：-]?\s*([^\n,;]{1,50})"],
    "vacancy_count": [r"(?:total vacancies?|no\. of vacancies?|vacancies|पदों की संख्या|कुल पद|रिक्त पद)\s*[:：-]?\s*([\d,]+)"],
    "admit_card_date": [r"(?:admit card download|admit card available|प्रवेश पत्र डाउनलोड)\s*[:：-]?\s*([^\n,;]{1,50})"],
    "counselling_date": [r"(?:counselling date|काउंसलिंग तिथि)\s*[:：-]?\s*([^\n,;]{1,50})"],
    "age_limit": [r"(?:age limit|आयु सीमा)\s*[:：-]?\s*([^\n,;]{1,70})"],
    "eligibility": [r"(?:eligibility|educational qualification|योग्यता|शैक्षणिक योग्यता)\s*[:：-]?\s*([^\n;]{1,120})"],
}


def extract_meaningful_fields(text):
    source = clean_text(text or "", 12000).casefold()
    found = {}
    for field, patterns in MEANINGFUL_FIELDS.items():
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.I)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
                if value:
                    found[field] = value
                    break
    return found


def meaningful_change_details(old_text, new_text):
    old_fields = extract_meaningful_fields(old_text)
    new_fields = extract_meaningful_fields(new_text)
    changed = {k: {"old": old_fields.get(k), "new": new_fields.get(k)} for k in set(old_fields) | set(new_fields) if old_fields.get(k) != new_fields.get(k)}
    if not changed:
        old_norm = re.sub(r"\s+", " ", (old_text or "").casefold()).strip()
        new_norm = re.sub(r"\s+", " ", (new_text or "").casefold()).strip()
        if old_norm and new_norm and old_norm != new_norm:
            ratio = fuzz.ratio(old_norm[:2000], new_norm[:2000])
            if ratio < 78 and Levenshtein.distance(old_norm[:2000], new_norm[:2000]) >= 30:
                changed["substantive_text"] = {"old": old_norm[:160], "new": new_norm[:160]}
    return changed


def _meaningful_metadata_change(old_title, new_title, old_context, new_context):
    old_title_norm = re.sub(r"\s+", " ", (old_title or "").casefold()).strip()
    new_title_norm = re.sub(r"\s+", " ", (new_title or "").casefold()).strip()
    if old_title_norm and old_title_norm == new_title_norm:
        old_fields = extract_meaningful_fields("\n".join([old_title or "", old_context or ""]))
        new_fields = extract_meaningful_fields("\n".join([new_title or "", new_context or ""]))
        return any(old_fields.get(k) != new_fields.get(k) for k in set(old_fields) | set(new_fields))
    details = meaningful_change_details("\n".join([old_title or "", old_context or ""]), "\n".join([new_title or "", new_context or ""]))
    if details: return True
    old_t = re.sub(r"[^\w]+", " ", (old_title or "").casefold()).strip()
    new_t = re.sub(r"[^\w]+", " ", (new_title or "").casefold()).strip()
    if old_t and new_t and old_t != new_t:
        distance = Levenshtein.distance(old_t, new_t)
        ratio = fuzz.ratio(old_t, new_t)
        if distance >= 8 and ratio < 92: return True
    old_c = re.sub(r"\s+", " ", (old_context or "").casefold()).strip()
    new_c = re.sub(r"\s+", " ", (new_context or "").casefold()).strip()
    if old_c and new_c and old_c != new_c:
        distance = Levenshtein.distance(old_c, new_c)
        ratio = fuzz.ratio(old_c, new_c)
        if distance >= 30 and ratio < 78: return True
    return False


def baseline_can_complete(scan_success, errors, max_errors=3):
    if isinstance(errors, str): error_count = 1 if errors.strip() else 0
    else:
        try: error_count = len(errors or [])
        except TypeError: error_count = 1 if errors else 0
    try: limit = max(0, min(50, int(max_errors)))
    except (TypeError, ValueError, OverflowError): limit = 3
    return bool(scan_success) and error_count <= limit


def add_review_item(state, record, reason, details=None):
    queue = state.setdefault("review_queue", {})
    key = hashlib.sha256((record.get("site_id", "") + "|" + record.get("url", "")).encode("utf-8")).hexdigest()
    previous = queue.get(key, {})
    queue[key] = {
        "site_id": record.get("site_id"), "site_name": record.get("site_name"),
        "url": record.get("url"), "title": record.get("title"),
        "reason": clean_text(str(reason), 500),
        "detected_dates": extract_meaningful_fields("\n".join([record.get("title", ""), record.get("context", ""), record.get("pdf_text", "")])),
        "gemini_output": record.get("classification"),
        "diff_summary": details or record.get("meaningful_diff", {}),
        "status": "open", "retry_count": int(previous.get("retry_count", 0)) + (1 if previous else 0),
        "first_seen": previous.get("first_seen", utc_now()), "updated_at": utc_now(),
    }


def _check_url_changed(session, url, record, verify_ssl=True):
    try:
        head = session.head(url, timeout=8, allow_redirects=True, verify=verify_ssl)
        new_etag = head.headers.get("etag")
        new_lm = head.headers.get("last-modified")
        old_etag = record.get("etag")
        old_lm = record.get("last_modified")
        changed = False
        if new_etag and old_etag and new_etag != old_etag: changed = True
        elif new_lm and old_lm and new_lm != old_lm: changed = True
        if record.get("is_pdf"):
            try:
                with session.get(url, timeout=20, allow_redirects=True, stream=True, verify=verify_ssl) as response:
                    if response.status_code < 400:
                        chunks = []
                        total = 0
                        prefix = b""
                        too_large = False
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if not chunk: continue
                            if len(prefix) < 8: prefix += chunk[:8 - len(prefix)]
                            total += len(chunk)
                            if total > MAX_PDF_SEND_BYTES:
                                too_large = True
                                break
                            chunks.append(chunk)
                        if not too_large and prefix.startswith(b"%PDF"):
                            content = b"".join(chunks)
                            new_hash = _pdf_content_fingerprint(content)
                            old_hash = record.get("pdf_hash")
                            old_kind = record.get("pdf_hash_kind")
                            new_kind = new_hash.split(":", 1)[0]
                            if old_hash and old_kind == new_kind: changed = new_hash != old_hash
                            elif old_hash and not old_kind: changed = False
                            record["pdf_hash"] = new_hash
                            record["pdf_hash_kind"] = new_kind
            except Exception as exc: print(f"[WARN] PDF hash check failed for {url[:80]}: {exc}", file=sys.stderr)
        return changed, new_etag, new_lm
    except Exception: return False, None, None


_NON_NOTICE_URL_PATTERNS = [
    re.compile(r"/page/\d+/?$", re.I), re.compile(r"/page/?$", re.I), re.compile(r"/notice_category(/|$)", re.I),
    re.compile(r"/notice-category(/|$)", re.I), re.compile(r"/document-category(/|$)", re.I), re.compile(r"/document_category(/|$)", re.I),
    re.compile(r"/past-notices(/|$)", re.I), re.compile(r"/past_notices(/|$)", re.I), re.compile(r"/whats-new(/|$)", re.I),
    re.compile(r"/whats_new(/|$)", re.I), re.compile(r"/category/[^/]+/?$", re.I), re.compile(r"/tag/[^/]+/?$", re.I),
    re.compile(r"/archive/?$", re.I), re.compile(r"/directory/?$", re.I), re.compile(r"/about-district/?$", re.I),
    re.compile(r"/department/?$", re.I), re.compile(r"/search(/|$)", re.I), re.compile(r"[?&]page=\d+", re.I),
    re.compile(r"[?&]paged=\d+", re.I), re.compile(r"/(hi|en|hn|ur|bn|ta|te|mr|gu|kn|ml|pa|or|as)(/|$).*(past[-_]?notices|whats[-_]?new|archive|category|notice[-_]?category|document[-_]?category|search)", re.I),
    re.compile(r"/(hi|en|hn|ur|bn|ta|te|mr|gu|kn|ml|pa|or|as)/?$", re.I),
    re.compile(r"/notice/(login|adminlogin|stdlogin|admin|auth|register)(/|$)", re.I),
    re.compile(r"/(login|signin|sign-in|register|logout)(/|$)", re.I),
]
_ARCHIVE_URL_PATTERNS = [
    re.compile(r"/past-notices(/|$)", re.I), re.compile(r"/past_notices(/|$)", re.I), re.compile(r"/whats-new(/|$)", re.I),
    re.compile(r"/whats_new(/|$)", re.I), re.compile(r"/notice_category(/|$)", re.I), re.compile(r"/notice-category(/|$)", re.I),
    re.compile(r"/document-category(/|$)", re.I), re.compile(r"/document_category(/|$)", re.I), re.compile(r"/archive/?$", re.I),
    re.compile(r"/page/\d+/?$", re.I), re.compile(r"/page/?$", re.I), re.compile(r"[?&]page=\d+", re.I), re.compile(r"[?&]paged=\d+", re.I),
]
_GENERIC_TITLES = {
    "archive", "more", "more...", "more…", "more....", "»", "«", ">>", "<<", "next", "previous", "prev",
    "back", "forward", "home", "contact", "about", "about us", "contact us", "read more", "view more",
    "view all", "show more", "see more", "load more", "learn more", "continue reading", "read more chevron_right", "view all chevron_right",
    "chevron_right", "chevron_left", "arrow_forward", "arrow_back", "click here", "here", "link",
    "department name", "name of department", "department", "dept", "designation", "post name", "post", "employee name", "father name",
    "address", "email", "phone", "mobile", "date of birth", "dob", "gender", "category", "serial number", "sr no", "sr. no", "s.no",
    "sl no", "sl. no", "roll number", "roll no", "registration no", "registration number", "enrollment no", "enrollment number",
    "qualification", "status", "remarks", "pincode", "pin code", "notices & announcements", "notices and announcements", "tenders", "placement statistics", "research cluster",
}
_LANGUAGE_SELECTOR_TITLES = {
    "hindi", "english", "santali", "santhali", "urdu", "bengali", "bangla", "odia", "oriya", "tamil", "telugu", "marathi",
    "gujarati", "kannada", "malayalam", "punjabi", "assamese", "kashmiri", "konkani", "manipuri", "nepali", "sanskrit",
    "sindhi", "bodo", "dogri", "maithili", "rajasthani", "हिन्दी", "हिंदी", "हिन्दी में", "हिंदी में", "अंग्रेजी", "अंग्रेज़ी", "अंग्रेजी में",
    "संताली", "संथाली", "उर्दू", "बंगाली", "बांग्ला", "उड़िया", "ओड़िया", "तमिल", "तेलुगु", "मराठी", "गुजराती", "कन्नड़", "मलयालम", "पंजाबी", "असमिया",
    "कश्मीरी", "कोकणी", "मणिपुरी", "नेपाली", "संस्कृत", "सिंधी", "बोडो", "डोगरी", "मैथिली", "राजस्थानी",
}
_EMPTY_PAGE_PHRASES = ["sorry, no notice matched", "no notice matched this category", "no records found", "no data found", "no notices found", "no results found"]

_TITLE_LABEL_RE = re.compile(r"^\s*(?:document\s+title|title|subject|शीर्षक|विषय|दस्तावेज़\s*शीर्षक)\s*[:：–—-]\s*$", re.IGNORECASE)
_TITLE_INLINE_RE = re.compile(r"^\s*(?:document\s+title|title|subject|शीर्षक|विषय|दस्तावेज़\s*शीर्षक)\s*[:：–—-]\s*(.+?)\s*$", re.IGNORECASE)
_TITLE_LABEL_STRICT_RE = re.compile(r"^\s*(?:document\s+title|title|subject|शीर्षक|विषय|दस्तावेज़\s*शीर्षक)\s*[:：–—-]?\s*$", re.IGNORECASE)


def _extract_explicit_title(soup):
    def clean_value(value):
        value = clean_text(value or "", 500)
        if len(value) < 15 or _is_generic_title(value) or _is_language_selector(value): return ""
        if _TITLE_LABEL_RE.match(value): return ""
        if _looks_like_menu_dump(value): return ""
        return value
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            label = clean_text(cells[0].get_text(" ", strip=True), 100)
            if _TITLE_LABEL_STRICT_RE.match(label):
                value = clean_value(cells[1].get_text(" ", strip=True))
                if value: return value
    for dt in soup.find_all("dt"):
        label = clean_text(dt.get_text(" ", strip=True), 100)
        if _TITLE_LABEL_STRICT_RE.match(label):
            dd = dt.find_next_sibling("dd")
            if dd:
                value = clean_value(dd.get_text(" ", strip=True))
                if value: return value
    for label_node in soup.find_all(["b", "strong", "label"]):
        label = clean_text(label_node.get_text(" ", strip=True), 100)
        if not _TITLE_LABEL_STRICT_RE.match(label): continue
        sibling = label_node.find_next_sibling()
        if sibling:
            value = clean_value(sibling.get_text(" ", strip=True))
            if value: return value
        parent = label_node.parent
        if parent:
            combined = clean_text(parent.get_text(" ", strip=True), 700)
            m = _TITLE_INLINE_RE.match(combined)
            if m:
                value = clean_value(m.group(1))
                if value: return value
    for node in soup.find_all(["td", "th", "dd", "p", "div", "li", "span"]):
        raw = node.get_text(" ", strip=True)
        if len(raw) > 700: continue
        text_value = clean_text(raw, 700)
        m = _TITLE_INLINE_RE.match(text_value)
        if m:
            value = clean_value(m.group(1))
            if value: return value
    return ""


def _is_navigation_url(url):
    try:
        parsed = urlparse(url)
        fragment = (parsed.fragment or "").strip()
        if (fragment.startswith("/") or fragment.startswith("!/")) and len(fragment) > 2: return False
        target = (parsed.path or "") + ("?" + parsed.query if parsed.query else "")
    except Exception: return False
    for pat in _NON_NOTICE_URL_PATTERNS:
        if pat.search(target): return True
    return False


def _is_archive_url(url):
    try:
        parsed = urlparse(url)
        target = (parsed.path or "") + ("?" + parsed.query if parsed.query else "")
    except Exception: return False
    for pat in _ARCHIVE_URL_PATTERNS:
        if pat.search(target): return True
    return False


def _is_generic_title(title):
    t = re.sub(r"\s+", " ", (title or "").strip().casefold())
    if not t: return True
    if t in _GENERIC_TITLES: return True
    if len(t.split()) <= 3 and re.fullmatch(r"(?:[\w.-]+\s+)?(?:name|no|no\.|number|id|code|address|email|phone|mobile|dob|status|remarks|qualification|designation|department|dept|category|gender|pincode|serial|roll|registration|enrollment)", t, flags=re.UNICODE): return True
    if len(t) > 50 and re.match(r"^(?:national|indian)\s+institute\s+of\s+", t): return True
    if len(t) > 40 and re.match(r"^(?:address|campus address|contact address|postal address|administrative office address)\b", t) and re.search(r"(?:pincode|pin\s*code|\b(?:81|82|83|84|85)\d{4}\b)", t): return True
    ui_only = re.sub(r"\s+(?:chevron_(?:right|left|up|down)|arrow_(?:forward|back|right|left)|keyboard_arrow_(?:right|left|up|down)|east|west|north|south)+$", "", t).strip()
    if ui_only in _GENERIC_TITLES: return True
    if re.match(r"^(?:read|view|show|see|load|continue|learn)\s+(?:more|all|less|next)(?:\s+(?:chevron_\w+|arrow_\w+|east|west|north|south))*$", t): return True
    compact = re.sub(r"[^\w]+", "", t, flags=re.UNICODE)
    if not compact or len(compact) <= 2: return True
    if t.isdigit() and len(t) <= 3: return True
    return False


def _is_language_selector(title):
    t = (title or "").strip().lower()
    if not t: return False
    if t in _LANGUAGE_SELECTOR_TITLES: return True
    for sep in (" - ", " | ", " / ", "(", ")"):
        for part in t.split(sep):
            p = part.strip()
            if p and p in _LANGUAGE_SELECTOR_TITLES and len(t) <= 40: return True
    return False


def _is_empty_page_text(html_text):
    if not html_text: return False
    sample = html_text[:8000].lower()
    return any(phrase in sample for phrase in _EMPTY_PAGE_PHRASES)


def _title_from_url(url, min_len=8):
    try: path = unquote(urlparse(url).path or "")
    except Exception: return ""
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    if not slug or slug.lower().endswith(".pdf"): slug = slug.rsplit(".", 1)[0] if "." in slug else slug
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\s+", " ", slug).strip()
    slug = clean_text(slug, 200)
    if len(slug) < min_len: return ""
    if _is_generic_title(slug): return ""
    return slug


_PDF_URL_HINTS = ("downloadfile", "download_file", "downloadpdf", "download_pdf", "showpdf", "viewpdf", "getpdf", "filedownload", "getfile")


def _looks_like_pdf_url(url):
    if not url: return False
    u = url.lower()
    if ".pdf" in u: return True
    try: path = (urlparse(url).path or "").lower()
    except Exception: return False
    if any(kw in path for kw in _PDF_URL_HINTS): return True
    return False


def _looks_like_pdf_response(content, content_type, url=""):
    if not content: return False
    ct = (content_type or "").lower()
    if "application/pdf" in ct: return True
    if content[:4] == b"%PDF": return True
    if "application/octet-stream" in ct and content[:5] == b"%PDF-": return True
    if ("download" in ct or "x-download" in ct) and content[:4] == b"%PDF": return True
    if _looks_like_pdf_url(url) and content[:4] == b"%PDF": return True
    return False


def _collect_all_pdf_candidates(html_text, base_url, max_candidates=15):
    if not html_text: return []
    try: soup = BeautifulSoup(html_text, "html.parser")
    except Exception: return []
    def _valid(href):
        if not href or not isinstance(href, str): return False
        h = href.strip().lower()
        if not h: return False
        if h.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "whatsapp:", "fb:", "blob:")): return False
        return True
    def _is_html_page(href):
        h = href.lower()
        for ext in (".html", ".htm", ".asp", ".aspx", ".jsp", ".php", ".cgi", ".shtml", ".css", ".js"):
            if h.endswith(ext) or ext + "?" in h: return True
        return False
    def _abs(href): return canonical_url(urljoin(base_url, href))
    candidates: Dict[str, int] = {}
    def _add(href, priority):
        if not _valid(href) or _is_html_page(href): return
        abs_url = _abs(href)
        if abs_url == canonical_url(base_url): return
        if _is_navigation_url(abs_url): return
        if candidates.get(abs_url, 0) < priority: candidates[abs_url] = priority
    for tag in soup.find_all(["iframe", "embed", "object", "source"]):
        src = tag.get("src") or tag.get("data") or ""
        if src and not src.lower().startswith(("javascript:", "#", "data:")): _add(src, 100)
    for tag in soup.find_all(onclick=True):
        onclick = tag.get("onclick", "") or ""
        for pat in (r"window\.open\(\s*['\"]([^'\"]+)['\"]", r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", r"window\.location\s*=\s*['\"]([^'\"]+)['\"]", r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]"):
            m = re.search(pat, onclick, re.I)
            if m: _add(m.group(1), 95)
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if ".pdf" in href.lower() or _looks_like_pdf_url(href): _add(href, 90)
    _PDF_PATH_HINTS = ("/writereaddata/", "/uploadfile/", "/uploads/", "/upload/", "/downloadfile/", "/download_file/", "/getfile/", "/showfile/", "/viewfile/", "/filedownload/", "/files/", "/media/", "/attachment/", "/attachments/", "/docs/", "/documents/", "/file/", "/pdf/", "/download/", "/downloads/")
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        href_lower = href.lower()
        if any(hint in href_lower for hint in _PDF_PATH_HINTS): _add(href, 80)
    _TEXT_KEYWORDS = ("view", "download", "click here", "click", "open", "get", "see file", "show", "read", "attachment", "file", "link", "देखें", "डाउनलोड", "देखे", "खोलें", "फ़ाइल", "फाइल", "विवरण", "डाउनलोड करें", "देखें यहाँ", "यहाँ देखें", "खोलिये")
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(" ", strip=True).lower()
        if not text: continue
        if any(kw in text for kw in _TEXT_KEYWORDS):
            priority = 70
            if re.search(r"\d+\s*(kb|mb|bytes)", text, re.I): priority = 75
            _add(href, priority)
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        img = a.find("img")
        if not img: continue
        img_attrs = " ".join([str(img.get("src") or ""), str(img.get("alt") or ""), " ".join(img.get("class") or []), str(img.get("title") or "")]).lower()
        if any(kw in img_attrs for kw in ("pdf", "document", "file", "download", "attachment", "doc")): _add(href, 65)
    _FILE_LABEL_RE = re.compile(r"^(file|फ़ाइल|फाइल|attachment|संलग्नक|file\s*name|फ़ाइल\s*नाम)\s*[:\-]?\s*$", re.I)
    for label in soup.find_all(["td", "th", "label", "b", "strong", "span", "div", "dt"]):
        label_text = label.get_text(" ", strip=True).strip()
        if not _FILE_LABEL_RE.match(label_text): continue
        parent = label.find_parent(["tr", "li", "div", "p", "dl"]) or label.parent
        if not parent: continue
        for a in parent.find_all("a", href=True): _add(a.get("href", ""), 60)
    for a in soup.find_all("a", href=True, target="_blank"): _add(a.get("href", ""), 50)
    for a in soup.find_all("a", href=True): _add(a.get("href", ""), 30)
    ranked = sorted(candidates.items(), key=lambda x: -x[1])
    return ranked[:max_candidates]


def _find_pdf_link_in_html(html_text, base_url):
    ranked = _collect_all_pdf_candidates(html_text, base_url, max_candidates=1)
    return ranked[0][0] if ranked else None


def _download_with_pdf_resolution(session, url, timeout, depth=0, _visited=None, verify_ssl=True):
    if _visited is None: _visited = set()
    canon = canonical_url(url)
    if canon in _visited: return None, url, "already_visited"
    _visited.add(canon)
    if depth > 3: return None, url, "max_depth_exceeded"
    try:
        head = session.head(url, timeout=min(10, timeout), allow_redirects=True, verify=verify_ssl)
        cl = head.headers.get("content-length")
        if cl:
            try:
                if int(cl) > MAX_PDF_SEND_BYTES: return None, url, "size_limit_head"
            except (TypeError, ValueError): pass
    except Exception: pass
    try: response = session.get(url, timeout=timeout, allow_redirects=True, verify=verify_ssl)
    except requests.RequestException as exc: return None, url, f"request_error:{type(exc).__name__}"
    if response.status_code >= 400: return None, url, f"http_{response.status_code}"
    content = response.content
    content_type = response.headers.get("content-type", "").lower()
    final_url = response.url
    if _looks_like_pdf_response(content, content_type, final_url): return content, final_url, "direct_pdf"
    if "html" in content_type or "xhtml" in content_type or not content_type:
        try: html_text = content.decode("utf-8", errors="ignore")
        except Exception: return None, url, "html_decode_error"
        candidates = _collect_all_pdf_candidates(html_text, final_url, max_candidates=10)
        if not candidates and _html_looks_like_js_pdf_viewer(html_text) and depth == 0:
            print(f"[JS-PDF] Detected JS PDF viewer at {final_url[:80]}, rendering...", file=sys.stderr)
            rendered_html, rendered_url, _ = _render_with_playwright(final_url, wait_after_ms=3000, scroll_bottom=False)
            if rendered_html: candidates = _collect_all_pdf_candidates(rendered_html, rendered_url or final_url, max_candidates=10)
        for cand_url, priority in candidates:
            sub_content, sub_url, sub_method = _download_with_pdf_resolution(session, cand_url, timeout, depth + 1, _visited, verify_ssl)
            if sub_content: return sub_content, sub_url, f"html_wrap(p{priority})->{sub_method}"
        return None, url, "html_no_pdf_found"
    if content[:4] == b"%PDF": return content, final_url, "magic_bytes"
    return None, url, f"unsupported:{content_type[:40]}"


_MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "जनवरी": 1, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "अक्तूबर": 10, "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12,
}
_MONTH_REGEX = (r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december|जनवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|सितम्बर|अक्टूबर|अक्तूबर|नवंबर|नवम्बर|दिसंबर|दिसम्बर")
_FULL_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"),
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+(" + _MONTH_REGEX + r")[\s\-,]+(\d{4})\b", re.I),
    re.compile(r"\b(" + _MONTH_REGEX + r")[\s\-]+(\d{1,2})(?:st|nd|rd|th)?[\s\-,]+(\d{4})\b", re.I),
]


def _extract_full_dates(text):
    if not text: return []
    dates = []
    for pat in _FULL_DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                g = m.groups()
                if len(g) != 3: continue
                a, b, c = g
                if a.isdigit() and len(a) == 4: y, mo, d = int(a), int(b), int(c)
                elif b.isalpha() or (b and not b.isdigit()): d = int(a); mo = _MONTH_NAMES.get(b.lower(), 0); y = int(c)
                elif a and not a.isdigit(): mo = _MONTH_NAMES.get(a.lower(), 0); d = int(b); y = int(c)
                else: d, mo, y = int(a), int(b), int(c)
                if not (1 <= mo <= 12 and 1 <= d <= 31): continue
                if not (1990 <= y <= 2100): continue
                dates.append(datetime(y, mo, d, tzinfo=timezone.utc))
            except (ValueError, IndexError, TypeError, AttributeError): continue
    return dates


_UPLOAD_DATE_TEXT_PATTERNS = [
    re.compile(r"(?:published|posted|uploaded|issued|dated)\s*(?:on|at|:|\-)?\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})", re.I),
    re.compile(r"(?:published|posted|uploaded|issued|dated)\s*(?:on|at|:|\-)?\s*(\d{1,2}(?:st|nd|rd|th)?\s+(?:" + _MONTH_REGEX + r")\s+\d{4})", re.I),
    re.compile(r"(?:दिनांक|प्रकाशित|जारी|अपलोड|अद्यतन)\s*[:\-]?\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})", re.I),
]


def _extract_explicit_issue_dates(text):
    if not text: return []
    text = str(text).translate(_DEVA_DIGIT_TRANS)
    dates = []
    for pattern in _UPLOAD_DATE_TEXT_PATTERNS:
        for match in pattern.finditer(text):
            dates.extend(_extract_full_dates(match.group(1)))
    if not dates:
        dates.extend(_extract_full_dates(text))
    return sorted(set(dates))


def _extract_upload_date(anchor_tag):
    try:
        search_root = (anchor_tag.find_parent("tr") or anchor_tag.find_parent("li") or anchor_tag.find_parent("article") or anchor_tag.find_parent(["div", "section"]) or anchor_tag.parent)
        if not search_root: return None
        time_tag = search_root.find("time")
        if time_tag:
            raw = time_tag.get("datetime") or time_tag.get_text(strip=True)
            if raw:
                try:
                    iso = raw.replace("Z", "+00:00").strip()
                    dt = datetime.fromisoformat(iso)
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except Exception: pass
                ds = _extract_full_dates(raw)
                if ds: return max(ds)
        text = search_root.get_text(" ", strip=True)
        for pat in _UPLOAD_DATE_TEXT_PATTERNS:
            m = pat.search(text)
            if m:
                ds = _extract_full_dates(m.group(1))
                if ds: return max(ds)
        all_dates = _extract_full_dates(text)
        if all_dates: return max(all_dates)
    except Exception: pass
    return None


def _is_stale_notice(title, context, url, upload_date=None, pdf_text=None):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_NOTICE_DAYS)

    title_text = str(title or "")
    context_text = str(context or "")
    url_text = str(url or "")
    pdf_text_str = str(pdf_text or "")
    full_text = f"{title_text} {context_text} {url_text} {pdf_text_str}".strip()
    normalized_title = title_text.translate(_DEVA_DIGIT_TRANS)
    normalized_context = context_text.translate(_DEVA_DIGIT_TRANS)
    normalized_full = full_text.translate(_DEVA_DIGIT_TRANS)
    normalized_pdf_head = pdf_text_str[:500].translate(_DEVA_DIGIT_TRANS)

    title_words = normalized_title.split()
    if len(title_words) >= 5:
        first_words = " ".join(title_words[:6]).lower()
        early_nav_count = sum(1 for sig in _STRICT_EARLY_NAV_SIGNALS if _match_signal(first_words, sig))
        if early_nav_count >= 2:
            if not _is_notice_document_url(url): return True
        short_words = [w for w in title_words if len(w.strip("|•-–—,:;")) <= 6]
        title_lower = normalized_title.lower()
        nav_count = _count_nav_signals(title_lower)
        has_notice_signal = any(_match_signal(title_lower, sig) for sig in _NOTICE_SIGNALS)
        mostly_short = len(short_words) >= len(title_words) * 0.6
        explicit_nav_dump = any(_match_signal(title_lower, sig) for sig in _EXPLICIT_DUMP_SIGNALS)
        compact_social_dump = (len(title_words) <= 8 and nav_count >= 3 and not has_notice_signal)
        if not has_notice_signal and not _is_notice_document_url(url):
            if (mostly_short and (nav_count >= 2 or explicit_nav_dump)) or compact_social_dump: return True

    title_dates = _extract_full_dates(normalized_title)
    head_dates = _extract_full_dates(normalized_pdf_head) if normalized_pdf_head else []
    body_dates = _extract_full_dates(normalized_full)
    all_dates = list(set(title_dates + head_dates + body_dates))

    if upload_date is not None:
        try:
            if upload_date.tzinfo is None: upload_date = upload_date.replace(tzinfo=timezone.utc)
            else: upload_date = upload_date.astimezone(timezone.utc)
            all_dates.append(upload_date)
        except (AttributeError, ValueError): pass

    if all_dates:
        max_date = max(all_dates)
        if max_date >= cutoff:
            return False
        print(f"[STALE] All {len(all_dates)} date(s) older than {STALE_NOTICE_DAYS} days; newest={max_date.date()}", file=sys.stderr)
        return True

    current_year = now.year
    current_academic_start = current_year if now.month >= 7 else current_year - 1
    current_year_update = bool(re.search(
        rf"(?:revised|revision|corrigendum|addendum|amended|modified|संशोधित|शुद्धिपत्र).{{0,50}}{current_year}|{current_year}.{{0,50}}(?:revised|revision|corrigendum|addendum|amended|modified|संशोधित|शुद्धिपत्र)",
        f"{normalized_title} {normalized_context}", re.I))
    session_pattern = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*[-/–—]\s*((?:19|20)\d{2}|\d{2})(?!\d)")
    admission_session_signal = bool(re.search(
        r"\b(?:admission|admissions|provisional|application|counselling|counseling|selection\s+list|merit\s+list|ph\.?d\.?|enrollment|enrolment|प्रवेश|आवेदन|चयन\s+सूची|मेरिट\s+सूची)\b",
        normalized_title, re.I))
    if admission_session_signal and not current_year_update:
        for match in session_pattern.finditer(normalized_title):
            session_start = int(match.group(1))
            end_token = match.group(2)
            session_end = int(end_token) if len(end_token) == 4 else (session_start // 100) * 100 + int(end_token)
            if session_end < session_start: session_end += 100
            if session_start < current_academic_start:
                print(f"[STALE-SESSION] Old session {session_start}-{session_end}", file=sys.stderr)
                return True

    years = [int(year) for year in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", normalized_full)]
    if years:
        max_year = max(years)
        try:
            latest_possible = datetime(max_year, 12, 31, tzinfo=timezone.utc)
            if latest_possible < cutoff:
                print(f"[STALE-YEAR] Latest possible for {max_year} ({latest_possible.date()}) is older than {STALE_NOTICE_DAYS} days", file=sys.stderr)
                return True
        except (ValueError, OverflowError): pass

    return False


def _classify_stale_reason(title, context, url, upload_date=None, pdf_text=None):
    if not _is_stale_notice(title, context, url, upload_date=upload_date, pdf_text=pdf_text): return None
    title_lower = str(title or "").lower()
    nav_count = _count_nav_signals(title_lower)
    has_notice_signal = any(_match_signal(title_lower, sig) for sig in _NOTICE_SIGNALS)
    if nav_count >= 2 and not has_notice_signal: return f"MENU-DUMP(nav={nav_count})"
    return "STALE"


def utc_now(): return datetime.now(timezone.utc).isoformat()


def clean_text(value, limit=500):
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit]


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f: return json.load(f)
    except FileNotFoundError: return default
    except json.JSONDecodeError as exc: raise RuntimeError(f"Invalid JSON in {path.name}: {exc}") from exc


def atomic_save_json(path, data, max_bytes=None):
    tmp = path.with_suffix(path.suffix + ".new")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        if max_bytes is not None:
            size = tmp.stat().st_size
            if size > max_bytes: raise RuntimeError(f"Serialized state exceeds byte cap: {size} > {max_bytes}")
        os.replace(tmp, path)
    except Exception:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass
        raise


def canonical_url(raw):
    raw = (raw or "").strip()
    p = urlparse(raw)
    if not p.scheme or not p.netloc: return raw
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", "", p.query, ""))


def _canonical_spa_url(raw):
    raw = (raw or "").strip()
    p = urlparse(raw)
    if not p.scheme or not p.netloc: return raw
    fragment = (p.fragment or "").strip()
    keep_fragment = fragment.startswith("/") or fragment.startswith("!/")
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", "", p.query, fragment if keep_fragment else ""))


def canonical_identity_url(raw):
    normalized = _canonical_spa_url(raw)
    p = urlparse(normalized)
    if not p.scheme or not p.netloc: return normalized
    tracking_exact = {"fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "ref", "referrer", "lang", "language", "_ga", "_gl", "igshid"}
    pairs = []
    for key, value in parse_qsl(p.query, keep_blank_values=True):
        key_lower = key.casefold()
        if key_lower in tracking_exact or key_lower.startswith("utm_"): continue
        pairs.append((key, value))
    pairs.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", "", urlencode(pairs, doseq=True), p.fragment))


def _normalize_host(host):
    h = (host or "").lower().strip()
    if h.startswith("www."): h = h[4:]
    return h


def same_host(a, b): return _normalize_host(urlparse(a).netloc) == _normalize_host(urlparse(b).netloc)


def is_http_url(url): return urlparse(url).scheme in {"http", "https"}


def is_pdf(url): return urlparse(url).path.lower().endswith(".pdf")


def make_session():
    session = requests.Session()
    retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET", "HEAD"}), respect_retry_after_header=True, raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-IN,en;q=0.8,hi;q=0.7", "Cache-Control": "no-cache"})
    return session


_SITE_VERIFY_SSL_BY_ID = {}
_SITE_TIMEOUT_BY_ID = {}


def get_config():
    cfg = load_json(CONFIG_FILE, {})
    if not isinstance(cfg, dict): raise RuntimeError("websites.json root must be an object")
    scan = cfg.get("scan") or {}
    defaults = {
        "request_timeout_seconds": 45, "max_workers": 5, "max_items_per_site": 75, "max_discovery_pages_per_site": 10, "max_archive_pages_per_site": 20, "max_pagination_per_archive": 4, "max_new_items_per_run": 25, "gemini_batch_size": 3, "gemini_max_calls_per_run": 15, "retention_days": 90, "sent_retention_days": 180, "ignored_retention_days": 30, "baseline_retention_days": 90, "domain_delay_seconds": 0.4, "max_state_items": 3000, "max_state_bytes": 8000000, "max_pending_attempts": 500, "stale_notice_days": 15, "baseline_max_errors": 3, "exclude_title_signals": list(_DEFAULT_EXCLUDED_TITLE_SIGNALS), "site_alert_threshold": 3, "site_alert_cooldown_hours": 6, "keywords": [], "discovery_keywords": [], "sitemap_enabled": True,
    }
    for key, value in defaults.items(): scan.setdefault(key, value)
    scan["request_timeout_seconds"] = max(5, int(scan["request_timeout_seconds"]))
    scan["max_workers"] = max(1, int(scan["max_workers"]))
    scan["max_items_per_site"] = max(1, int(scan["max_items_per_site"]))
    scan["max_discovery_pages_per_site"] = max(1, int(scan["max_discovery_pages_per_site"]))
    scan["max_archive_pages_per_site"] = max(0, int(scan["max_archive_pages_per_site"]))
    scan["max_pagination_per_archive"] = max(0, int(scan["max_pagination_per_archive"]))
    scan["max_new_items_per_run"] = max(1, int(scan["max_new_items_per_run"]))
    scan["gemini_batch_size"] = max(1, min(20, int(scan["gemini_batch_size"])))
    scan["gemini_max_calls_per_run"] = max(1, int(scan["gemini_max_calls_per_run"]))
    scan["retention_days"] = max(7, int(scan["retention_days"]))
    scan["sent_retention_days"] = max(7, int(scan["sent_retention_days"]))
    scan["ignored_retention_days"] = max(7, int(scan["ignored_retention_days"]))
    scan["baseline_retention_days"] = max(7, int(scan["baseline_retention_days"]))
    scan["domain_delay_seconds"] = max(0.0, min(10.0, float(scan["domain_delay_seconds"])))
    scan["max_state_items"] = max(500, min(10000, int(scan["max_state_items"])))
    scan["max_state_bytes"] = max(500000, min(10000000, int(scan["max_state_bytes"])))
    scan["max_pending_attempts"] = max(500, int(scan["max_pending_attempts"]))
    scan["stale_notice_days"] = max(1, int(scan["stale_notice_days"]))
    scan["baseline_max_errors"] = max(0, min(50, int(scan.get("baseline_max_errors", 3))))
    raw_exclusions = scan.get("exclude_title_signals", list(_DEFAULT_EXCLUDED_TITLE_SIGNALS))
    if not isinstance(raw_exclusions, list): raw_exclusions = list(_DEFAULT_EXCLUDED_TITLE_SIGNALS)
    scan["exclude_title_signals"] = list(dict.fromkeys(clean_text(str(x), 100).casefold() for x in raw_exclusions if str(x).strip()))
    scan["site_alert_threshold"] = max(1, int(scan["site_alert_threshold"]))
    scan["site_alert_cooldown_hours"] = max(1, int(scan["site_alert_cooldown_hours"]))
    scan["sitemap_enabled"] = bool(scan["sitemap_enabled"])
    scan["keywords"] = [clean_text(str(x), 80).lower() for x in scan.get("keywords", []) if str(x).strip()]
    scan["discovery_keywords"] = [clean_text(str(x), 80).lower() for x in scan.get("discovery_keywords", []) if str(x).strip()]
    cfg["scan"] = scan
    sites = cfg.get("websites")
    if not isinstance(sites, list): raise RuntimeError("websites.json must contain a 'websites' array")
    enabled = []
    ids = set()
    for raw_site in sites:
        if not isinstance(raw_site, dict) or not raw_site.get("enabled", True): continue
        site = dict(raw_site)
        site["name"] = clean_text(str(site.get("name", "Unnamed Site")), 120)
        site["url"] = canonical_url(str(site.get("url", "")))
        site["id"] = clean_text(str(site.get("id", site["name"])), 100)
        if not site["name"] or not is_http_url(site["url"]):
            print(f"[WARN] Skipping invalid site: {raw_site}")
            continue
        if site["id"] in ids:
            print(f"[WARN] Duplicate site id skipped: {site['id']}")
            continue
        ids.add(site["id"])
        site["keywords"] = [clean_text(str(x), 80).lower() for x in site.get("keywords", []) if str(x).strip()]
        site["discovery_keywords"] = [clean_text(str(x), 80).lower() for x in site.get("discovery_keywords", []) if str(x).strip()]
        js_mode = str(site.get("js_render", "auto")).strip().lower()
        if js_mode not in ("auto", "always", "never"): js_mode = "auto"
        site["js_render"] = js_mode
        try: site["request_timeout_seconds"] = max(5, min(180, int(site.get("request_timeout_seconds", scan["request_timeout_seconds"]))))
        except (TypeError, ValueError, OverflowError): site["request_timeout_seconds"] = scan["request_timeout_seconds"]
        try: site["baseline_max_errors"] = max(0, min(50, int(site.get("baseline_max_errors", scan["baseline_max_errors"]))))
        except (TypeError, ValueError, OverflowError): site["baseline_max_errors"] = scan["baseline_max_errors"]
        raw_verify_ssl = site.get("verify_ssl", True)
        if isinstance(raw_verify_ssl, str): site["verify_ssl"] = raw_verify_ssl.strip().casefold() not in {"0", "false", "no", "off"}
        else: site["verify_ssl"] = bool(raw_verify_ssl)
        try: site["js_wait_after_ms"] = max(0, min(60000, int(site.get("js_wait_after_ms", JS_WAIT_AFTER_MS))))
        except (TypeError, ValueError, OverflowError): site["js_wait_after_ms"] = JS_WAIT_AFTER_MS
        raw_scroll = site.get("js_scroll_bottom", JS_SCROLL_TO_BOTTOM)
        if isinstance(raw_scroll, str): site["js_scroll_bottom"] = raw_scroll.strip().lower() in {"1", "true", "yes", "on"}
        else: site["js_scroll_bottom"] = bool(raw_scroll)
        raw_selector = site.get("js_wait_for_selector", JS_WAIT_FOR_SELECTOR_DEFAULT)
        site["js_wait_for_selector"] = clean_text(str(raw_selector or ""), 200) or None
        try: site["js_selector_timeout_ms"] = max(1000, min(90000, int(site.get("js_selector_timeout_ms", 10000))))
        except (TypeError, ValueError, OverflowError): site["js_selector_timeout_ms"] = 10000
        raw_direct = site.get("direct_urls", [])
        if not isinstance(raw_direct, list): raw_direct = []
        site["direct_urls"] = [str(u).strip() for u in raw_direct if isinstance(u, (str, bytes)) and str(u).strip()]
        enabled.append(site)
    cfg["websites"] = enabled
    global _SITE_VERIFY_SSL_BY_ID, _SITE_TIMEOUT_BY_ID
    _SITE_VERIFY_SSL_BY_ID = {site["id"]: site["verify_ssl"] for site in enabled}
    _SITE_TIMEOUT_BY_ID = {site["id"]: site["request_timeout_seconds"] for site in enabled}
    return cfg


def default_state():
    return {
        "version": 31, "initialized": False, "last_run": None, "items": {}, "sites": {}, "review_queue": {},
        "metrics": {"site_scan_success": {}, "site_scan_failure": {}, "parse_errors": 0, "sent_notifications": 0, "failed_notifications": 0, "duplicate_rejects": 0, "state_save_failures": 0, "js_renders": 0},
        "stats": {"runs": 0, "sent": 0, "ignored": 0, "pending": 0, "errors": 0},
    }


def load_state():
    state = load_json(STATE_FILE, default_state())
    if not isinstance(state, dict): state = default_state()
    state.setdefault("version", 1)
    state.setdefault("initialized", False)
    state.setdefault("last_run", None)
    state.setdefault("items", {})
    state.setdefault("sites", {})
    state.setdefault("review_queue", {})
    state.setdefault("metrics", {})
    state["metrics"].setdefault("site_scan_success", {})
    state["metrics"].setdefault("site_scan_failure", {})
    state["metrics"].setdefault("js_renders", 0)
    for _metric in ("parse_errors", "sent_notifications", "failed_notifications", "duplicate_rejects", "state_save_failures"): state["metrics"].setdefault(_metric, 0)
    state.setdefault("stats", {})
    for key in ("runs", "sent", "ignored", "pending", "errors"): state["stats"].setdefault(key, 0)
    for record in state["items"].values():
        if not isinstance(record, dict): continue
        if "status" not in record:
            if record.get("sent") is True: record["status"] = "sent"
            elif record.get("ignored") is True: record["status"] = "ignored"
            else: record["status"] = "baseline"
        record["_loaded_status"] = record.get("status")
        record.setdefault("attempts", 0); record.setdefault("last_error", None); record.setdefault("summary", ""); record.setdefault("classification", None); record.setdefault("pdf_extracted", False); record.setdefault("pdf_text", ""); record.setdefault("pdf_attempts", 0); record.setdefault("ocr_used", False); record.setdefault("pdf_method", ""); record.setdefault("telegram_attempts", 0); record.setdefault("pdf_sent", False); record.setdefault("upload_date", None); record.setdefault("explicit_title", ""); record.setdefault("first_seen", utc_now()); record.setdefault("last_seen", record["first_seen"]); record.setdefault("etag", None); record.setdefault("last_modified", None); record.setdefault("pdf_hash", None); record.setdefault("pdf_hash_kind", None); record.setdefault("last_content_check_at", None)
        if not record.get("last_content_check_at"): record["last_content_check_at"] = (record.get("last_seen") or record.get("first_seen") or utc_now())
    state["version"] = 31
    return state


def site_state(state, site_id):
    s = state.setdefault("sites", {}).setdefault(site_id, {})
    s.setdefault("baseline_complete", False); s.setdefault("consecutive_failures", 0); s.setdefault("total_failures", 0); s.setdefault("last_success", None); s.setdefault("last_error", None); s.setdefault("last_error_at", None); s.setdefault("last_item_count", 0); s.setdefault("alert_active", False); s.setdefault("last_alert_at", None); s.setdefault("last_recovery_at", None)
    return s


def mark_site_success(state, site_id, count):
    s = site_state(state, site_id)
    s["consecutive_failures"] = 0; s["last_success"] = utc_now(); s["last_error"] = None; s["last_error_at"] = None; s["last_item_count"] = count


def mark_site_failure(state, site_id, error):
    s = site_state(state, site_id)
    s["consecutive_failures"] += 1; s["total_failures"] += 1; s["last_error"] = clean_text(error, 500); s["last_error_at"] = utc_now()


def _check_and_send_site_alerts(state, cfg, token, chat_id, threshold=3, cooldown_hours=6):
    now = datetime.now(timezone.utc)
    failing_candidates = []
    recovered_candidates = []
    for site in cfg["websites"]:
        sid = site["id"]
        st = site_state(state, sid)
        failures = int(st.get("consecutive_failures", 0))
        if failures >= threshold and not st.get("alert_active", False):
            last_alert = st.get("last_alert_at")
            cooled = True
            if last_alert:
                try:
                    last_dt = datetime.fromisoformat(last_alert.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None: last_dt = last_dt.replace(tzinfo=timezone.utc)
                    cooled = (now - last_dt).total_seconds() >= cooldown_hours * 3600
                except Exception: cooled = True
            if cooled: failing_candidates.append((sid, site["name"], failures, st.get("last_error")))
        elif failures == 0 and st.get("alert_active", False): recovered_candidates.append((sid, site["name"]))
    if failing_candidates:
        lines = ["⚠️ <b>Site Failure Alert</b>", "", f"{len(failing_candidates)} site(s) failing:", ""]
        for sid, name, count, err in failing_candidates[:10]:
            lines.append(f"• <b>{html.escape(name)}</b> — {count} fails")
            if err: lines.append(f"  <i>{html.escape(str(err)[:100])}</i>")
        ok, _permanent, detail = send_telegram(token, chat_id, "\n".join(lines))
        if ok:
            for sid, _name, _count, _err in failing_candidates:
                st = site_state(state, sid); st["alert_active"] = True; st["last_alert_at"] = utc_now()
        else: print(f"[WARN] Site failure alert not delivered: {detail}", file=sys.stderr)
    if recovered_candidates:
        lines = ["✅ <b>Site Recovery</b>", "", f"{len(recovered_candidates)} site(s) recovered:", ""]
        for sid, name in recovered_candidates[:10]: lines.append(f"• {html.escape(name)}")
        ok, _permanent, detail = send_telegram(token, chat_id, "\n".join(lines))
        if ok:
            for sid, _name in recovered_candidates:
                st = site_state(state, sid); st["alert_active"] = False; st["last_recovery_at"] = utc_now()
        else: print(f"[WARN] Site recovery alert not delivered: {detail}", file=sys.stderr)


def local_score(title, url, context, keywords):
    text = f"{title} {url} {context}".lower()
    return sum(1 for kw in keywords if kw and kw in text)


def pending_priority(record, keywords, now=None):
    now = now or datetime.now(timezone.utc)
    first_seen = _parse_utc_timestamp(record.get("first_seen"))
    age_days = max(0.0, (now - first_seen).total_seconds() / 86400) if first_seen else 0.0
    title = " ".join((record.get("explicit_title") or "", record.get("title") or "", record.get("context") or "")).casefold()
    if any(x in title for x in ("admit card", "exam date", "प्रवेश पत्र", "परीक्षा तिथि", "परीक्षा कार्यक्रम")): urgency = 100
    elif any(x in title for x in ("result", "रिजल्ट", "परिणाम", "answer key", "उत्तर कुंजी", "merit list", "मेरिट सूची")): urgency = 50
    else: urgency = 0
    score = local_score(record.get("title", ""), record.get("url", ""), record.get("context", ""), keywords)
    age_bonus = min(age_days, 30.0)
    return urgency + score + age_bonus, age_days


_PRECLASS_STRONG = (
    "admission", "result", "admit card", "answer key", "examination", "exam", "revaluation", "migration", "scholarship", "fee", "syllabus", "timetable", "counselling", "recruitment", "vacancy", "tender", "notice", "notification", "placement", "convocation", "registration", "result", "notice", "notification", "examination", "exam form", "exam schedule", "exam programme", "marksheet", "merit", "advertisement", "प्रवेश", "परिणाम", "प्रवेश पत्र", "उत्तर कुंजी", "परीक्षा", "पुनर्मूल्यांकन", "छात्रवृत्ति", "शुल्क", "भर्ती", "निविदा", "सूचना", "अधिसूचना", "परीक्षा कार्यक्रम", "परीक्षा केंद्र", "परीक्षा फॉर्म",
    "dy. director", "deputy director", "principal", "associate dean", "backlog",
    "on contractual basis", "on contract basis", "for the post of",
    "अनुबंध के आधार पर", "पद के लिए",
)
_PRECLASS_NAV_ONLY = ("photo gallery", "video gallery", "site map", "web policies", "screen reader", "feedback", "anti-ragging", "grievance cell")
_DEFAULT_EXCLUDED_TITLE_SIGNALS = ("gst return", "income tax", "annual report", "balance sheet", "minutes of meeting", "right to information", "rti application")


def _matched_excluded_title_signal(title, signals=None):
    text = re.sub(r"\s+", " ", str(title or "").casefold()).strip()
    for raw in (signals if signals is not None else _DEFAULT_EXCLUDED_TITLE_SIGNALS):
        signal = re.sub(r"\s+", " ", str(raw or "").casefold()).strip()
        if not signal: continue
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in signal.split()) + r"(?!\w)"
        if re.search(pattern, text, flags=re.UNICODE): return signal
    return ""


def _keyword_pre_classify(record, excluded_signals=None):
    if _is_stale_notice(record.get("explicit_title") or record.get("title", ""), record.get("context", ""), record.get("url", ""), upload_date=_parse_upload_date(record.get("upload_date")), pdf_text=record.get("pdf_text", "")): return False

    title_for_exclusion = " ".join((record.get("explicit_title") or "", record.get("title") or "")).casefold()
    if _matched_excluded_title_signal(title_for_exclusion, excluded_signals): return False

    title = " ".join((record.get("explicit_title") or "", record.get("title") or "")).casefold()

    if _is_generic_title(title): return False
    words = title.split()
    if len(re.findall(r'(?:►|»|›|→|▸|▶)', title)) >= 1:
        print(f"[NAV-FILTER] arrow: {title[:80]}", file=sys.stderr)
        return False
    if len(words) >= 8:
        date_prefixed = bool(re.match(r'^\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b', title))
        if not date_prefixed:
            strong_notice_patterns = [
                r'\b(?:admission|result|admit card|answer key|examination|revaluation|scholarship|timetable|syllabus|recruitment|tender|counselling|registration|convocation|migration|interview|appointment|apprentice|goods|product|services|supply|purchase|installation|procurement|quotation|bid|rfp|eoi|nit|camc|amc|kva|equipment|machinery|hardware|software|panel|webinar|workshop|seminar|conference|symposium|ceremony|graduation|colloquium|conclave|principal|director|dean)\b',
                r'\b(?:notice|notification)\s+(?:for|regarding|about)\b',
                r'\b(?:apply|application|form[\s-]?fill|fee[\s-]?payment)\s+(?:date|last|deadline|online)\b',
                r'\b(?:advertisement|advt\.?)\s+for\s+the\s+post\b',
            ]
            if not any(re.search(p, title) for p in strong_notice_patterns):
                print(f"[NAV-FILTER] long-no-notice: {title[:80]}", file=sys.stderr)
                return False
    for i in range(len(words) - 1):
        if words[i] == words[i + 1] and words[i] in _NAV_SIGNALS:
            print(f"[NAV-FILTER] repeat-nav: {title[:80]}", file=sys.stderr)
            return False
    if re.match(r'^about\s+(?:us|capital\s+university|the\s+university|the\s+institute|the\s+college)\b', title):
        print(f"[NAV-FILTER] about-prefix: {title[:80]}", file=sys.stderr)
        return False
    nav_count = _count_nav_signals(title)
    if nav_count >= 3 and len(words) <= 10:
        print(f"[NAV-FILTER] dense-nav({nav_count}): {title[:80]}", file=sys.stderr)
        return False
    if re.search(r'\bcall\s+us\b|\bemail\s+[\w.+-]+@[\w.-]+\.\w{2,}|\bphone\s*[:\-]?\s*\d{6,}|\bwhatsapp\b', title, re.I):
        print(f"[NAV-FILTER] contact-page: {title[:80]}", file=sys.stderr)
        return False
    if any(sig in title for sig in _PRECLASS_STRONG): return True
    url = (record.get("url") or "").casefold()
    title_is_nav = any(sig in title for sig in _PRECLASS_NAV_ONLY)
    has_notice_signal = any(sig in title for sig in _PRECLASS_STRONG) or any(sig in title for sig in ("notice", "notification", "सूचना", "आवेदन", "advertisement"))
    if title_is_nav and not has_notice_signal and not _is_notice_document_url(url): return False
    return None


def item_id(site_id, url, title="", context=""):
    raw = f"{site_id}|{canonical_identity_url(url)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _normalize_title(title): return re.sub(r"[^\w\s]", " ", (title or "").lower()).strip()


def build_site_index(state):
    index = {}
    permanent = {}
    for iid, record in state.get("items", {}).items():
        if not isinstance(record, dict): continue
        sid = record.get("site_id")
        if not sid: continue
        row = (iid, record.get("title", ""), record.get("url", ""))
        target = permanent if record.get("status") == "permanent_error" else index
        target.setdefault(sid, []).append(row)
    for sid, rows in permanent.items(): index.setdefault(sid, []).extend(rows)
    return index


def find_url_match(site_items, url):
    target = canonical_identity_url(url)
    if not target: return None
    for iid, _title, existing_url in site_items or []:
        if canonical_identity_url(existing_url) == target: return iid
    return None


def _duplicate_alias_slug(url):
    try:
        path = unquote(urlparse(url).path or "").rstrip("/")
        slug = path.rsplit("/", 1)[-1].casefold()
        slug = re.sub(r"\.(?:pdf|html?|aspx?|php)$", "", slug)
        slug = re.sub(r"(?:[-_ ](?:copy|duplicate|duplicated|download|downloaded|final-copy))+$", "", slug)
        return re.sub(r"[^a-z0-9]+", "", slug)
    except Exception: return ""


def find_fuzzy_match(site_items, title, url):
    if not site_items or not title: return None
    new_title = _normalize_title(title)
    new_slug = _duplicate_alias_slug(url)
    if len(new_title) < 12 or len(new_slug) < 8: return None
    for iid, existing_title, existing_url in site_items:
        old_slug = _duplicate_alias_slug(existing_url)
        if not old_slug or old_slug != new_slug: continue
        old_title = _normalize_title(existing_title)
        if len(old_title) >= 12 and fuzz.token_sort_ratio(new_title, old_title) >= FUZZY_CROSS_PATH_THRESHOLD: return iid
    return None


def _is_duplicate_by_pdf_hash(state, record, exclude_iid=None):
    new_hash = record.get("pdf_hash")
    new_kind = record.get("pdf_hash_kind")
    new_site = record.get("site_id")
    if not new_hash or not new_kind or not new_site: return None
    new_title_norm = _normalize_title(record.get("title", ""))
    for other_iid, other in state.get("items", {}).items():
        if other_iid == exclude_iid or not isinstance(other, dict): continue
        if other.get("status") != "sent": continue
        if other.get("site_id") != new_site: continue
        if other.get("pdf_hash_kind") != new_kind: continue
        if other.get("pdf_hash") != new_hash: continue
        other_title_norm = _normalize_title(other.get("title", ""))
        if new_title_norm and other_title_norm:
            if fuzz.token_sort_ratio(new_title_norm, other_title_norm) < 70: continue
        return other.get("title") or other_iid
    return None


def extract_candidates(html_text, page_url, site, scan):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]): tag.decompose()
    keywords = list(dict.fromkeys(scan["keywords"] + site.get("keywords", [])))
    out = []
    seen = set()

    # AngularJS ng-href support
    for a in soup.find_all("a", attrs={"ng-href": True}):
        ng_href = (a.get("ng-href") or "").strip()
        if not ng_href or ng_href.startswith("{{"): continue
        if not a.get("href"):
            a["href"] = ng_href

    for a in soup.find_all("a", href=True):
        href = _canonical_spa_url(urljoin(page_url, a.get("href", "")))
        if not is_http_url(href) or not same_host(page_url, href): continue
        if _is_navigation_url(href): continue

        title = clean_text(a.get_text(" ", strip=True), 300)
        parent = a.find_parent("tr")
        if parent is None: parent = a.find_parent(["li", "article", "section"])
        if parent is None: parent = a.find_parent(["div", "td"])
        context = clean_text(parent.get_text(" ", strip=True) if parent else "", 700)

        if _is_generic_title(title) or len(title) < 5:
            context_parts = re.split(r"[|\n\r\t]+", context)
            context_title = ""
            for part in context_parts:
                part = clean_text(part, 200)
                if len(part) >= 8 and not _is_generic_title(part) and not _is_language_selector(part):
                    context_title = part
                    break

            if not context_title or _is_generic_title(context_title):
                walker = parent
                for _ in range(3):
                    if not walker: break
                    walker = walker.parent
                    if not walker: break
                    for htag in ("h1", "h2", "h3", "h4"):
                        h = walker.find(htag)
                        if h:
                            htext = clean_text(h.get_text(" ", strip=True), 300)
                            if (len(htext) >= 5 and not _is_generic_title(htext)
                                and not _looks_like_menu_dump(htext) and not _is_garbage_title(htext)):
                                context_title = htext
                                break
                    if context_title: break

            title = context_title or _title_from_url(href, min_len=5) or ""
            if not title or _is_generic_title(title): continue

        if _is_language_selector(title):
            parent_text = clean_text(parent.get_text(" ", strip=True) if parent else "", 300)
            for lang in _LANGUAGE_SELECTOR_TITLES: parent_text = re.compile(re.escape(lang), re.IGNORECASE).sub("", parent_text).strip()
            parent_text = clean_text(parent_text, 300)
            if (parent_text and not _is_language_selector(parent_text) and not _is_generic_title(parent_text) and len(parent_text) >= 8): title = parent_text
            else:
                url_title = _title_from_url(href, min_len=8)
                if url_title: title = url_title
                else: continue

        if len(title) < 20 and context:
            parts = re.split(r"[|\-–—:]", context)
            for p in parts:
                p = clean_text(p.strip(), 200)
                if len(p) >= 20 and not _is_generic_title(p) and not _is_language_selector(p):
                    title = p
                    break

        score = local_score(title, href, context, keywords)
        href_lower = href.lower()
        is_pdf_link = bool(is_pdf(href) or _looks_like_pdf_url(href))
        if is_pdf_link: pdf_bonus = 2
        elif any(kw in href_lower for kw in ("/notice/", "/notices/", "/document", "/writereaddata/", "/uploadfile/", "/uploads/", "/upload/", "/downloadfile/", "/download_file/", "/getfile/", "/showfile/", "/viewfile/", "/filedownload/", "/files/", "/media/", "/file/", "/attachment/", "/attachments/", "/docs/", "/download/", "/downloads/")): pdf_bonus = 1
        else: pdf_bonus = 0

        if score <= 0 and pdf_bonus == 0: continue
        if href in seen: continue
        seen.add(href)
        upload_dt = _extract_upload_date(a)

        final_title = clean_text(title, 300)
        if not final_title or _is_generic_title(final_title): final_title = clean_text(context, 180)
        if not final_title or _is_generic_title(final_title): final_title = _title_from_url(href, min_len=5)
        if not final_title: final_title = "Notice"

        out.append({
            "url": href, "title": final_title, "context": context, "source_page": page_url,
            "is_pdf": is_pdf_link, "score": score + pdf_bonus, "upload_date": upload_dt.isoformat() if upload_dt else None,
        })

    out.sort(key=lambda x: (-x["score"], x["title"].lower()))
    out = out[: scan["max_items_per_site"]]

    page_explicit_title = _extract_explicit_title(soup)
    if page_explicit_title:
        for row in out:
            if _is_notice_document_url(row["url"]): row["explicit_title"] = page_explicit_title

    for row in out:
        if _is_notice_document_url(row["url"]) and _looks_like_menu_dump(row.get("title", "")):
            cleaned = _cleanup_menu_dump_title(row["title"], url=row["url"], explicit=row.get("explicit_title", ""))
            if cleaned and cleaned != row["title"]:
                print(f"[TITLE-CLEANUP] {row['title'][:60]!r} → {cleaned[:60]!r}", file=sys.stderr)
                row["title"] = cleaned

    return out


def discover_from_sitemap(session, base_url, scan):
    if not scan.get("sitemap_enabled"): return []
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = urljoin(root + "/", "sitemap.xml")
    try:
        response = session.get(sitemap_url, timeout=scan["request_timeout_seconds"])
        if response.status_code >= 400: return []
        soup = BeautifulSoup(response.text, "xml")
        urls = []
        for loc in soup.find_all("loc")[:100]:
            url = canonical_url(loc.get_text(strip=True))
            if is_http_url(url) and same_host(base_url, url):
                if _is_navigation_url(url): continue
                if any(kw in url.lower() for kw in scan["discovery_keywords"] if kw): urls.append(url)
        return urls[: scan["max_discovery_pages_per_site"] * 3]
    except Exception: return []


def _robots_allowed(session, url, timeout):
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _ROBOTS_CACHE:
        parser = RobotFileParser()
        robots_url = origin + "/robots.txt"
        try:
            # FIX #3: Raised robots.txt timeout cap from 10s to 45s and added failure logging.
            # SKMU (and similar slow TLS sites) were silently falling back to None in under
            # 10 seconds, causing fetch to be skipped with no trace in the logs.
            response = session.get(robots_url, timeout=min(45, timeout), allow_redirects=True)
            if response.status_code < 400 and response.text:
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                _ROBOTS_CACHE[origin] = parser
            else: _ROBOTS_CACHE[origin] = None
        except Exception as exc:
            print(f"[ROBOTS-FAIL] {robots_url} — {type(exc).__name__}: {exc}", file=sys.stderr)
            _ROBOTS_CACHE[origin] = None
    parser = _ROBOTS_CACHE.get(origin)
    if parser is None: return True
    try: return parser.can_fetch(USER_AGENT, url)
    except Exception: return True


def discover_site(session, site, scan):
    base_url = site["url"]
    timeout = int(site.get("request_timeout_seconds", scan["request_timeout_seconds"]))
    effective_scan = dict(scan)
    effective_scan["request_timeout_seconds"] = timeout
    max_pages = scan["max_discovery_pages_per_site"]
    max_archive_pages = scan.get("max_archive_pages_per_site", 5)
    max_pagination_per_archive = scan.get("max_pagination_per_archive", 3)

    discovery_keywords = list(dict.fromkeys(scan["discovery_keywords"] + site.get("discovery_keywords", []) + scan["keywords"]))

    direct_urls = []
    for raw_url in (site.get("direct_urls") or []):
        if not raw_url or not isinstance(raw_url, str): continue
        canon = _canonical_spa_url(raw_url.strip())
        if not is_http_url(canon):
            print(f"[DIRECT-URL-SKIP] Invalid URL: {raw_url[:100]}", file=sys.stderr)
            continue
        if not same_host(base_url, canon):
            print(f"[DIRECT-URL-SKIP] Off-host URL: {raw_url[:100]}", file=sys.stderr)
            continue
        direct_urls.append(canon)
    if direct_urls:
        print(f"[DIRECT-URLS] {site['name']}: {len(direct_urls)} priority URL(s) will be scanned first", file=sys.stderr)

    queue = list(direct_urls) + [base_url]
    queue.extend(discover_from_sitemap(session, base_url, effective_scan))
    visited = set()
    archive_visited_count = 0
    pagination_tracker = {}
    candidates = {}
    errors = []
    successful_pages = 0
    total_budget = max_pages + max_archive_pages
    normal_visited_count = 0
    js_render_count = 0

    while queue and len(visited) < total_budget + len(direct_urls):
        page = _canonical_spa_url(queue.pop(0))
        if page in visited or not same_host(base_url, page): continue
        is_archive = _is_archive_url(page)
        if is_archive and archive_visited_count >= max_archive_pages: continue
        if not is_archive and normal_visited_count >= max_pages + len(direct_urls): continue
        visited.add(page)

        if is_archive:
            is_pagination = bool(re.search(r"/page/\d+|[?&](?:page|paged)=\d+", page, re.I))
            if is_pagination:
                prefix = re.sub(r"/page/\d+/?", "/", page)
                prefix = re.sub(r"[?&]page=\d+", "", prefix)
                prefix = re.sub(r"[?&]paged=\d+", "", prefix).rstrip("?&")
                current = pagination_tracker.get(prefix, 0)
                if current >= max_pagination_per_archive: continue
                pagination_tracker[prefix] = current + 1
            archive_visited_count += 1
        else: normal_visited_count += 1

        try:
            if not _robots_allowed(session, page, timeout):
                print(f"[ROBOTS-SKIP] {page}", file=sys.stderr)
                continue
            domain_delay = float(scan.get("domain_delay_seconds", 1.0) or 0.0)
            if domain_delay > 0 and visited: time.sleep(min(domain_delay, 10.0))
            verify_ssl = bool(site.get("verify_ssl", True))
            if not verify_ssl: print(f"[WARN] TLS certificate verification disabled for configured site: {page}", file=sys.stderr)

            response = None
            response_text = None
            rendered_from_waf = False
            final_url = page
            content_type = ""
            try:
                response = session.get(page, timeout=timeout, allow_redirects=True, verify=verify_ssl)
                final_url = canonical_url(response.url)
                content_type = response.headers.get("content-type", "").lower()
            except requests.RequestException as request_exc:
                can_browser_retry = (site.get("js_render", "auto") != "never" and JS_RENDER_ENABLED and _PLAYWRIGHT_AVAILABLE and js_render_count < JS_MAX_RENDER_PER_SITE)
                if can_browser_retry:
                    print(f"[NETWORK-RETRY] {type(request_exc).__name__}; trying Playwright: {page}", file=sys.stderr)
                    rendered_html, rendered_url, js_method = _render_with_playwright(page, timeout_ms=timeout * 1000, wait_for_selector=site.get("js_wait_for_selector"), wait_after_ms=site.get("js_wait_after_ms"), scroll_bottom=site.get("js_scroll_bottom", JS_SCROLL_TO_BOTTOM), selector_timeout_ms=site.get("js_selector_timeout_ms"))
                    js_render_count += 1
                    if rendered_html:
                        response_text = rendered_html
                        final_url = _canonical_spa_url(rendered_url or page)
                        content_type = "text/html"
                        rendered_from_waf = True
                        print(f"[NETWORK-RETRY-OK] {page[:80]} ({js_method}, {len(rendered_html)} chars)", file=sys.stderr)
                    else: print(f"[NETWORK-RETRY-FAIL] {page[:80]}: {type(request_exc).__name__}; {js_method}", file=sys.stderr)
                if response_text is None: raise request_exc

            if response is not None and response.status_code in (403, 503):
                if JS_RENDER_ENABLED and _PLAYWRIGHT_AVAILABLE and js_render_count < JS_MAX_RENDER_PER_SITE:
                    print(f"[WAF-RETRY] HTTP {response.status_code}; trying Playwright: {page}", file=sys.stderr)
                    rendered_html, rendered_url, js_method = _render_with_playwright(page, timeout_ms=timeout * 1000, wait_for_selector=site.get("js_wait_for_selector"), wait_after_ms=site.get("js_wait_after_ms"), scroll_bottom=site.get("js_scroll_bottom", JS_SCROLL_TO_BOTTOM), selector_timeout_ms=site.get("js_selector_timeout_ms"))
                    js_render_count += 1
                    if rendered_html:
                        response_text = rendered_html
                        final_url = _canonical_spa_url(rendered_url)
                        content_type = "text/html"
                        rendered_from_waf = True
                        print(f"[WAF-RETRY-OK] {page[:80]} ({js_method}, {len(rendered_html)} chars)", file=sys.stderr)
                    else: print(f"[WAF-RETRY-FAIL] {page[:80]}: {js_method}", file=sys.stderr)
                if response_text is None: raise RuntimeError(f"HTTP {response.status_code}; Playwright fallback unavailable or failed")
            elif response is not None and response.status_code >= 400: raise RuntimeError(f"HTTP {response.status_code}")

            if response is not None and not rendered_from_waf and content_type:
                ct_lower = content_type.lower()
                is_pdf_resp = _looks_like_pdf_response(response.content, ct_lower, final_url)
                is_office_doc = ("application/vnd.openxmlformats" in ct_lower or "application/msword" in ct_lower or "application/vnd.ms-excel" in ct_lower or "application/vnd.ms-powerpoint" in ct_lower)
                if is_pdf_resp:
                    filename = final_url.rsplit("/", 1)[-1] or "PDF Notice"
                    candidates[final_url] = {"url": final_url, "title": clean_text(filename, 300), "context": "Direct PDF notice", "source_page": page, "is_pdf": True, "score": 3, "upload_date": None}
                    successful_pages += 1
                    continue
                if is_office_doc:
                    filename = final_url.rsplit("/", 1)[-1] or "Document Notice"
                    print(f"[DOC-NOTICE] Office document detected: {filename[:80]}", file=sys.stderr)
                    candidates[final_url] = {"url": final_url, "title": clean_text(filename, 300), "context": "Direct document notice", "source_page": page, "is_pdf": True, "score": 3, "upload_date": None}
                    successful_pages += 1
                    continue
                if "html" not in ct_lower and "xhtml" not in ct_lower:
                    print(f"[SKIP] Non-HTML, non-document content-type={ct_lower[:60]!r} at {page}", file=sys.stderr)
                    continue

            js_mode = site.get("js_render", "auto")
            source_html = response.text if response is not None else (response_text or "")
            should_render_js = False if rendered_from_waf else _should_render_js(site, source_html, page)

            if (should_render_js and JS_RENDER_ENABLED and _PLAYWRIGHT_AVAILABLE and js_render_count < JS_MAX_RENDER_PER_SITE):
                print(f"[JS-RENDER] {page} (mode={js_mode})", file=sys.stderr)
                rendered_html, rendered_url, js_method = _render_with_playwright(page, timeout_ms=timeout * 1000, wait_for_selector=site.get("js_wait_for_selector"), wait_after_ms=site.get("js_wait_after_ms"), scroll_bottom=site.get("js_scroll_bottom", JS_SCROLL_TO_BOTTOM), selector_timeout_ms=site.get("js_selector_timeout_ms"))
                js_render_count += 1
                if rendered_html:
                    response_text = rendered_html
                    final_url = _canonical_spa_url(rendered_url)
                    print(f"[JS-OK] {page[:80]} -> {len(rendered_html)} chars ({js_method})", file=sys.stderr)
                else:
                    response_text = response.text
                    print(f"[JS-FAIL] {page[:80]}: {js_method}", file=sys.stderr)
            elif response_text is None: response_text = response.text if response is not None else ""

            if _is_empty_page_text(response_text):
                successful_pages += 1
                continue

            found = extract_candidates(response_text, final_url, site, effective_scan)
            for row in found:
                old = candidates.get(row["url"])
                if old is None or row["score"] > old["score"]: candidates[row["url"]] = row
            successful_pages += 1

            soup = BeautifulSoup(response_text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = _canonical_spa_url(urljoin(final_url, a.get("href", "")))
                if (not is_http_url(href) or not same_host(base_url, href) or href in visited or href in queue): continue
                # FIX #2: Login regex requires trailing slash OR end-of-URL now
                if re.search(r"/(login|logout|register|signin|sign-in|search|tag)(/|$)", href, re.I): continue
                if _is_archive_url(href):
                    if len(queue) < total_budget * 3: queue.append(href)
                    continue
                anchor = clean_text(a.get_text(" ", strip=True), 220).lower()
                parsed_href = urlparse(href)
                path = parsed_href.path.lower()
                route = (parsed_href.fragment or "").replace("!", " ").replace("/", " ")
                haystack = f"{anchor} {path} {route}"
                if any(kw in haystack for kw in discovery_keywords if kw):
                    queue.append(href)
                    if len(queue) >= total_budget * 2: break
        except Exception as exc: errors.append(f"{page}: {clean_text(str(exc), 250)}")

    values = list(candidates.values())
    values.sort(key=lambda x: (-x["score"], x["title"].lower()))
    return (site["id"], values[: scan["max_items_per_site"]], errors, successful_pages > 0)


def scan_site(site, scan): return discover_site(make_session(), site, scan)
