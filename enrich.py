"""Visit each business website and extract emails, social links, extra phone
numbers and a likely contact person."""
import re
import time
import threading
import urllib.parse
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

import db as dbm

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# common obfuscations: "name [at] domain [dot] com", "name(at)domain(dot)com"
OBFUS_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\s+at\s+|@)\s*"
    r"([A-Za-z0-9.\-]+)\s*(?:\[dot\]|\(dot\)|\s+dot\s+|\.)\s*([A-Za-z]{2,})",
    re.I,
)
# Sri Lanka phone numbers (and generic international)
PHONE_RE = re.compile(
    r"(?:\+94|0094|0)\s?(?:\d[\s\-]?){8,9}\d"
)
NAME_LABEL_RE = re.compile(
    r"(?:owner|proprietor|founder|co-?founder|managing\s+director|director|"
    r"manager|ceo|ceo\s*/?\s*founder|principal|contact\s+person)"
    r"\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    re.I,
)
NAME_STOP = {"call", "tel", "phone", "email", "contact", "address", "mobile",
             "whatsapp", "fax", "hotline", "open", "our", "the", "visit",
             "location", "we", "for", "or", "and", "today", "now", "monday",
             "company", "office", "store", "shop", "team", "is", "at", "on"}


def _clean_name(raw):
    toks = raw.split()
    while toks and toks[-1].lower() in NAME_STOP:
        toks.pop()
    while toks and toks[0].lower() in NAME_STOP:
        toks.pop(0)
    if len(toks) < 2 or len(toks) > 3:
        return None
    return " ".join(toks)


SOCIAL_DOMAINS = {
    "facebook": "facebook.com",
    "instagram": "instagram.com",
    "linkedin": "linkedin.com",
    "twitter": "twitter.com",
    "x": "x.com",
    "youtube": "youtube.com",
    "tiktok": "tiktok.com",
    "whatsapp": "wa.me",
}
JUNK_EMAIL_HINTS = ("example.com", "sentry.io", "wixpress.com", "domain.com",
                    "@2x", ".png", ".jpg", ".gif", ".webp", ".svg")

_domain_locks = {}
_locks_guard = threading.Lock()


def _domain_lock(domain):
    with _locks_guard:
        if domain not in _domain_locks:
            _domain_locks[domain] = threading.Lock()
        return _domain_locks[domain]


def _norm_url(u):
    if not u:
        return None
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    return u


def _clean_emails(found):
    out = []
    for e in found:
        e = e.strip().strip(".").lower()
        if any(h in e for h in JUNK_EMAIL_HINTS):
            continue
        if len(e) > 100:
            continue
        if e not in out:
            out.append(e)
    return out


def _pick_primary(emails):
    if not emails:
        return None
    # prefer info@/contact@/sales@/hello@, else shortest
    for pref in ("info@", "contact@", "hello@", "sales@", "enquir", "admin@"):
        for e in emails:
            if e.startswith(pref) or pref in e:
                return e
    return sorted(emails, key=len)[0]


def _extract(html, base_url):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    emails = set()
    for a in soup.select('a[href^="mailto:"]'):
        addr = a.get("href", "")[7:].split("?")[0]
        if addr:
            emails.add(addr)
    emails.update(EMAIL_RE.findall(html))
    for m in OBFUS_RE.findall(text):
        emails.add(f"{m[0]}@{m[1]}.{m[2]}")
    emails = _clean_emails(emails)

    phones = set()
    for m in PHONE_RE.findall(html):
        pass
    for m in PHONE_RE.finditer(text):
        phones.add(re.sub(r"\s+", " ", m.group(0)).strip())
    for a in soup.select('a[href^="tel:"]'):
        t = a.get("href", "")[4:]
        if t:
            phones.add(t.strip())

    socials = {}
    for a in soup.select("a[href]"):
        href = a["href"].lower()
        for name, dom in SOCIAL_DOMAINS.items():
            if dom in href and name not in socials:
                socials[name] = a["href"].split("?")[0]

    name = None
    m = NAME_LABEL_RE.search(text)
    if m:
        name = _clean_name(m.group(1))

    return {
        "emails": emails,
        "phones": sorted(phones)[:8],
        "socials": socials,
        "contact_name": name,
    }


def _robots_ok(session, base, ua, cache):
    root = urllib.parse.urlsplit(base)
    key = (root.scheme, root.netloc)
    if key in cache:
        rp = cache[key]
    else:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{root.scheme}://{root.netloc}/robots.txt")
        try:
            r = session.get(f"{root.scheme}://{root.netloc}/robots.txt", timeout=8)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp = None
        except Exception:
            rp = None
        cache[key] = rp
    if rp is None:
        return True
    return rp.can_fetch(ua, base)


def _crawl_site(website, cfg, robots_cache):
    ecfg = cfg["enrich"]
    ua = ecfg["user_agent"]
    session = requests.Session()
    session.headers.update({"User-Agent": ua})
    base = _norm_url(website)
    if not base:
        return {"status": "no_url"}
    netloc = urllib.parse.urlsplit(base).netloc
    lock = _domain_lock(netloc)

    pages = [base] + [urllib.parse.urljoin(base, p) for p in ecfg["contact_paths"]]
    agg = {"emails": [], "phones": [], "socials": {}, "contact_name": None}
    fetched_any = False
    for url in pages:
        if ecfg.get("respect_robots", True):
            try:
                if not _robots_ok(session, url, ua, robots_cache):
                    continue
            except Exception:
                pass
        with lock:
            try:
                r = session.get(url, timeout=ecfg["timeout"], allow_redirects=True)
            except Exception:
                continue
            finally:
                time.sleep(ecfg.get("per_domain_delay", 1.0))
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            continue
        fetched_any = True
        try:
            data = _extract(r.text, url)
        except Exception:
            continue
        for e in data["emails"]:
            if e not in agg["emails"]:
                agg["emails"].append(e)
        for p in data["phones"]:
            if p not in agg["phones"]:
                agg["phones"].append(p)
        for k, v in data["socials"].items():
            agg["socials"].setdefault(k, v)
        if data["contact_name"] and not agg["contact_name"]:
            agg["contact_name"] = data["contact_name"]
        # stop early if we already have an email + some socials from homepage+contact
        if agg["emails"] and len(agg["socials"]) >= 1 and url != base:
            break
    agg["email"] = _pick_primary(agg["emails"])
    agg["status"] = "ok" if fetched_any else "unreachable"
    return agg


def run(cfg, conn):
    rows = dbm.pending_enrichment(conn)
    if not rows:
        print("[enrich] nothing to enrich (no un-enriched websites)")
        return 0
    workers = cfg["enrich"].get("workers", 6)
    print(f"[enrich] crawling {len(rows)} websites with {workers} workers ...")
    robots_cache = {}
    done = 0
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_crawl_site, r["website"], cfg, robots_cache): r for r in rows}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"status": f"error:{e}"}
            results[r["id"]] = (r, res)
            done += 1
            if done % 10 == 0:
                print(f"[enrich] {done}/{len(rows)} sites processed")
    # write sequentially (sqlite single-writer)
    found = 0
    for row_id, (r, res) in results.items():
        dbm.save_enrichment(conn, row_id, res)
        if res.get("email"):
            found += 1
    conn.commit()
    print(f"[enrich] done. {found} sites yielded at least one email.")
    return found
