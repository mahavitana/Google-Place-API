"""SQLite storage layer for the business scraper."""
import sqlite3
import json
import re
import csv
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL,          -- 'osm' or 'google'
    source_id         TEXT NOT NULL,          -- osm element id / google place id
    name              TEXT,
    category          TEXT,
    address           TEXT,
    latitude          REAL,
    longitude         REAL,
    phone             TEXT,
    website           TEXT,
    rating            REAL,
    rating_count      INTEGER,
    -- enrichment fields
    email             TEXT,
    emails_all        TEXT,   -- json list
    phones_extra      TEXT,   -- json list
    socials           TEXT,   -- json object
    contact_name      TEXT,
    enriched          INTEGER DEFAULT 0,
    enrich_status     TEXT,
    dedup_key         TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_dedup   ON businesses(dedup_key);
CREATE INDEX IF NOT EXISTS idx_website ON businesses(website);
CREATE INDEX IF NOT EXISTS idx_enriched ON businesses(enriched);
"""


def _norm(s):
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _digits(s):
    if not s:
        return ""
    return re.sub(r"\D", "", s)


def make_dedup_key(name, phone, website):
    """A loose key so the same shop coming from OSM and Google collapses."""
    ph = _digits(phone)
    if ph:
        ph = ph[-9:]            # last 9 digits, ignores country code variance
    web = ""
    if website:
        web = re.sub(r"^https?://(www\.)?", "", website.lower()).rstrip("/").split("/")[0]
    return f"{_norm(name)}|{ph}|{web}"


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_business(conn, rec):
    """Insert a freshly-collected business, or update core fields if it exists.
    Does NOT clobber enrichment fields."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    rec["dedup_key"] = make_dedup_key(rec.get("name"), rec.get("phone"), rec.get("website"))
    cur = conn.execute(
        "SELECT id FROM businesses WHERE source=? AND source_id=?",
        (rec["source"], rec["source_id"]),
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            """UPDATE businesses SET name=?, category=?, address=?, latitude=?,
               longitude=?, phone=COALESCE(?, phone), website=COALESCE(?, website),
               rating=?, rating_count=?, dedup_key=?, updated_at=? WHERE id=?""",
            (rec.get("name"), rec.get("category"), rec.get("address"),
             rec.get("latitude"), rec.get("longitude"), rec.get("phone"),
             rec.get("website"), rec.get("rating"), rec.get("rating_count"),
             rec["dedup_key"], now, row["id"]),
        )
        return row["id"], False
    conn.execute(
        """INSERT INTO businesses
           (source, source_id, name, category, address, latitude, longitude,
            phone, website, rating, rating_count, dedup_key, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rec["source"], rec["source_id"], rec.get("name"), rec.get("category"),
         rec.get("address"), rec.get("latitude"), rec.get("longitude"),
         rec.get("phone"), rec.get("website"), rec.get("rating"),
         rec.get("rating_count"), rec["dedup_key"], now, now),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"], True


def pending_enrichment(conn):
    """Rows that have a website but haven't been enriched yet.
    De-duplicated by website so we don't crawl the same site twice."""
    cur = conn.execute(
        """SELECT id, name, website FROM businesses
           WHERE website IS NOT NULL AND website != '' AND enriched = 0
           GROUP BY lower(website) ORDER BY id"""
    )
    return cur.fetchall()


def save_enrichment(conn, row_id, result):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """UPDATE businesses SET email=?, emails_all=?, phones_extra=?, socials=?,
           contact_name=?, enriched=1, enrich_status=?, updated_at=? WHERE id=?""",
        (result.get("email"),
         json.dumps(result.get("emails", [])),
         json.dumps(result.get("phones", [])),
         json.dumps(result.get("socials", {})),
         result.get("contact_name"),
         result.get("status", "ok"), now, row_id),
    )
    # Propagate the found email to other rows that share the same website.
    if result.get("email"):
        conn.execute(
            "SELECT website FROM businesses WHERE id=?", (row_id,)
        )


def stats(conn):
    g = lambda q: conn.execute(q).fetchone()[0]
    return {
        "total": g("SELECT COUNT(*) FROM businesses"),
        "osm": g("SELECT COUNT(*) FROM businesses WHERE source='osm'"),
        "google": g("SELECT COUNT(*) FROM businesses WHERE source='google'"),
        "with_website": g("SELECT COUNT(*) FROM businesses WHERE website IS NOT NULL AND website!=''"),
        "with_email": g("SELECT COUNT(*) FROM businesses WHERE email IS NOT NULL AND email!=''"),
        "enriched": g("SELECT COUNT(*) FROM businesses WHERE enriched=1"),
        "unique_businesses": g("SELECT COUNT(DISTINCT dedup_key) FROM businesses"),
    }


def export_csv(conn, path, dedup=True):
    cols = ["name", "category", "address", "phone", "email", "website",
            "phones_extra", "socials", "contact_name", "rating", "rating_count",
            "latitude", "longitude", "source"]
    if dedup:
        # one row per dedup_key: prefer the row that has an email, then a phone.
        q = """
        SELECT * FROM businesses b
        WHERE id = (
            SELECT id FROM businesses b2 WHERE b2.dedup_key = b.dedup_key
            ORDER BY (email IS NOT NULL AND email!='') DESC,
                     (phone IS NOT NULL AND phone!='') DESC, id LIMIT 1)
        ORDER BY name
        """
    else:
        q = "SELECT * FROM businesses ORDER BY name"
    rows = conn.execute(q).fetchall()
    n = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            socials = r["socials"]
            try:
                socials = ", ".join(f"{k}:{v}" for k, v in json.loads(socials).items()) if socials else ""
            except Exception:
                socials = socials or ""
            phones_extra = r["phones_extra"]
            try:
                phones_extra = ", ".join(json.loads(phones_extra)) if phones_extra else ""
            except Exception:
                phones_extra = phones_extra or ""
            w.writerow([
                r["name"], r["category"], r["address"], r["phone"], r["email"],
                r["website"], phones_extra, socials, r["contact_name"],
                r["rating"], r["rating_count"], r["latitude"], r["longitude"], r["source"],
            ])
            n += 1
    return n
