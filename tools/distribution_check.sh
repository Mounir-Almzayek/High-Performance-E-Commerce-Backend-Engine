#!/usr/bin/env bash
# tools/distribution_check.sh
# ----------------------------
# Fire N HTTP requests against /healthz and histogram the X-Served-By header.
# Requires: curl, awk, sort, uniq
# Usage: bash tools/distribution_check.sh [N]   (default: 300)
#
# NFR5 acceptance criterion: each backend within 10% of even.

set -euo pipefail

N=${1:-300}
TARGET="http://localhost/healthz"
TMPFILE=$(mktemp)

echo "[distribution_check] Firing ${N} requests against ${TARGET} ..."
echo

for i in $(seq 1 "$N"); do
  curl -s -o /dev/null -w "%{http_code} %header{x-served-by}\n" "$TARGET" >> "$TMPFILE" || true
done

echo "Raw X-Served-By counts:"
echo "------------------------"
grep -v "^000" "$TMPFILE" | awk '{print $2}' | sort | uniq -c | sort -rn | while read count host; do
  pct=$(awk "BEGIN {printf \"%.1f\", ($count / $N) * 100}")
  bar=$(python3 -c "print('█' * int($count * 40 / $N) + '░' * (40 - int($count * 40 / $N)))" 2>/dev/null || echo "")
  printf "  %-20s : %4d requests  (%5s%%)  %s\n" "$host" "$count" "$pct" "$bar"
done

echo
errors=$(grep "^000" "$TMPFILE" | wc -l || echo 0)
echo "Total errors (no response): ${errors}"

rm -f "$TMPFILE"
echo
echo "NFR5 criterion: each backend within 10% of even (expected ~$((N / 3)) per backend)."
