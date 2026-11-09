#!/usr/bin/env bash
# Download the latest public holdings files listed in config/sources.csv.
# Each run saves into data/raw/<YYYY-MM-DD>/ (the download date) so daily
# snapshots can be compared later. The as-of date inside each file is what the
# pipeline uses; the folder date only records when the file was fetched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DAY="${1:-$(date +%F)}"
OUT="$ROOT/data/raw/$DAY"
mkdir -p "$OUT"

UA="Mozilla/5.0 (X11; Linux x86_64) fund-holdings-exceptions"
fail=0

# config/sources.csv columns: source_id,provider,kind,url  (header skipped)
while IFS=, read -r source_id provider kind url; do
  [ -z "$source_id" ] && continue
  case "$kind" in
    *_zip) ext="zip" ;;
    *_xlsx) ext="xlsx" ;;
    *) ext="csv" ;;
  esac
  dest="$OUT/${source_id}.${ext}"
  if curl -sSfL -A "$UA" --retry 2 --max-time 120 -o "$dest" "$url"; then
    # Providers sometimes answer with an HTML page instead of the file.
    if head -c 512 "$dest" | grep -qi "<!doctype html\|<html"; then
      echo "WARN $source_id: got an HTML page, not a data file" >&2
      mv "$dest" "$dest.html"
      fail=1
    else
      echo "ok   $source_id -> $(basename "$dest") ($(wc -c <"$dest") bytes)"
    fi
  else
    echo "FAIL $source_id: download error" >&2
    fail=1
  fi
done < <(tail -n +2 "$ROOT/config/sources.csv" | tr -d '\r')

exit "$fail"
