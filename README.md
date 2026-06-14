# Business Lead Scraper (Debian)

Collect live businesses in an area (e.g. Colombo) from **OpenStreetMap** (free) and the
**Google Places API** (most accurate), store them in a **SQLite** database with automatic
de-duplication, then **crawl each business website** to pull emails, social links, extra
phone numbers and a likely contact person. Exports a clean **CSV** for your marketing tools.

```
collect-osm ─┐
             ├─► SQLite (dedup) ─► enrich (crawl websites) ─► export CSV
collect-google ┘
```

## Files

| File | Purpose |
|------|---------|
| `run.py` | Command-line entry point |
| `collect_osm.py` | Pulls businesses from OpenStreetMap (Overpass API) |
| `collect_places.py` | Pulls businesses from Google Places API (New) |
| `enrich.py` | Crawls business websites for email / socials / phone / contact name |
| `db.py` | SQLite schema, de-duplication, CSV export |
| `config.example.yaml` | Settings template — copy to `config.yaml` |
| `setup.sh` | One-time installer |

---

## Step 1 — Put the files on your VM

SSH into your Google Cloud Debian VM, then copy this whole `biz-scraper` folder up to it
(use `scp`, `rsync`, or `git`). For example from your laptop:

```bash
scp -r biz-scraper youruser@YOUR_VM_IP:~/
```

## Step 2 — Install

```bash
cd ~/biz-scraper
bash setup.sh
```

This installs Python, creates a `venv`, installs dependencies, and creates `config.yaml`.

## Step 3 — Configure

Edit `config.yaml`:

```bash
nano config.yaml
```

- **OSM** works immediately, no key. Set your bounding box (`osm.bbox`) for the area you
  want. Get one in seconds at https://boundingbox.klokantech.com (choose the **CSV** format,
  which gives `min_lon,min_lat,max_lon,max_lat` — reorder to `[min_lat,min_lon,max_lat,max_lon]`).
- **Google** is optional but gives the best data (phone, website, rating). Paste your API
  key into `google.api_key`. Leave it empty to use OSM only.

### Getting a Google Places API key

1. Go to https://console.cloud.google.com → create/select a project.
2. **APIs & Services → Library →** enable **"Places API (New)"**.
3. **APIs & Services → Credentials → Create credentials → API key.**
4. Restrict the key (recommended): restrict to the *Places API (New)*.
5. **Billing must be enabled** on the project. Google gives a recurring free monthly credit,
   and Nearby Search is billed per call — see *Cost control* below.

## Step 4 — Run

Activate the environment first:

```bash
source venv/bin/activate
```

Then run any stage, or the whole pipeline:

```bash
python run.py collect-osm      # free, start here
python run.py collect-google   # needs API key
python run.py enrich           # crawl websites for emails etc.
python run.py export           # write businesses_export.csv
python run.py stats            # show counts

python run.py all              # collect (both) -> enrich -> export
```

Long crawls? Run it detached so it survives your SSH session closing:

```bash
nohup python run.py all > run.log 2>&1 &
tail -f run.log
```

## Step 5 — Get your data

- `businesses.db` — the full SQLite database (open with `sqlite3 businesses.db` or DB Browser).
- `businesses_export.csv` — de-duplicated, one row per business, ready for Excel / a mail tool.

Download it to your laptop:

```bash
scp youruser@YOUR_VM_IP:~/biz-scraper/businesses_export.csv .
```

---

## How de-duplication works

The same shop often appears in both OSM and Google. Each row gets a `dedup_key` built from
`normalized name + last 9 phone digits + domain`. The CSV export collapses these to one row
per business, preferring the row that has an email, then a phone.

## Cost control (Google)

Each grid cell × each place type = one billable Nearby Search call. With the defaults
(6 km half-extent, 600 m cells, ~21 types) that's roughly **289 cells × 21 types ≈ 6,000 calls**.
To spend less:

- Raise `cell_radius_m` (e.g. 1000) → far fewer cells.
- Trim `included_types` to only the categories you actually sell to.
- Shrink `half_extent_m` to the exact district you care about.
- Run **OSM first** (free) and use Google only to fill gaps.

Watch your spend in the Google Cloud billing dashboard and set a **budget alert**.

## Tuning the website crawler

In `config.yaml` under `enrich`: `workers` (parallelism), `timeout`, `per_domain_delay`
(politeness), and `contact_paths` (which sub-pages to check). It honors `robots.txt` by
default (`respect_robots: true`).

## Re-running

Everything is idempotent. Re-running `collect-*` updates existing rows without creating
duplicates. `enrich` only crawls sites not yet enriched, so you can stop and resume freely.

---

## Important note on using the data

Scraping public business data is one thing; **emailing it is separately regulated**. Many
markets (and the platforms you'll send through) treat unsolicited B2B email under anti-spam
rules — e.g. requiring a real opt-out, accurate sender identity, and sometimes prior consent.
Sending to harvested addresses can also get your domain/IP blacklisted, which quietly kills
deliverability for *all* your mail. Before a campaign, check the rules that apply to your
recipients and warm up your sending domain properly. This isn't legal advice.
