#!/usr/bin/env python3
"""Business scraper CLI.

Usage:
  python run.py collect-osm        # pull businesses from OpenStreetMap
  python run.py collect-google     # pull businesses from Google Places API
  python run.py collect            # both of the above
  python run.py enrich             # crawl websites for email/socials/phones/names
  python run.py export             # write the CSV file
  python run.py stats              # show database counts
  python run.py all                # collect -> enrich -> export
Optional:  --config path/to/config.yaml
"""
import sys
import argparse
import yaml

import db as dbm
import collect_osm
import collect_places
import enrich


def load_cfg(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def show_stats(conn):
    s = dbm.stats(conn)
    print("\n=== database ===")
    for k, v in s.items():
        print(f"  {k:18}: {v}")
    print()


def main():
    ap = argparse.ArgumentParser(description="Business scraper")
    ap.add_argument("command", choices=[
        "collect-osm", "collect-google", "collect",
        "enrich", "export", "stats", "all"])
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    conn = dbm.connect(cfg["database"])

    cmd = args.command
    if cmd in ("collect-osm", "collect", "all"):
        collect_osm.run(cfg, conn)
    if cmd in ("collect-google", "collect", "all"):
        collect_places.run(cfg, conn)
    if cmd in ("enrich", "all"):
        enrich.run(cfg, conn)
    if cmd in ("export", "all"):
        n = dbm.export_csv(conn, cfg["csv_export"], dedup=True)
        print(f"[export] wrote {n} de-duplicated rows -> {cfg['csv_export']}")
    if cmd == "stats":
        pass

    show_stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
