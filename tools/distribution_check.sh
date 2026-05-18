#!/usr/bin/env bash
# tools/distribution_check.sh
# ────────────────────────────────────────────────────────────────────────────
# NFR5 — Load Distribution Verification
#
# Fires N requests through Nginx and histograms which App Instance handled
# each request (via the X-Served-By / X-Instance-Id headers).
#
# Prerequisites:
#   - docker-compose up  (nginx + web1 + web2 + web3 all running)
#   - curl, awk, sort, uniq
#
# Usage:
#   bash tools/distribution_check.sh [N] [HOST]
#
#   N     — number of requests   (default: 300)
#   HOST  — base URL              (default: http://localhost)
#
# Examples:
#   bash tools/distribution_check.sh           # 300 requests, localhost
#   bash tools/distribution_check.sh 500       # 500 requests
#   bash tools/distribution_check.sh 300 http://localhost:80
#
# NFR5 acceptance criterion:
#   Each of the 3 backends handles ≥ 23% and ≤ 43% of requests
#   (even = 33.3 %, allowed skew ±10 %).
# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

N="${1:-300}"
HOST="${2:-http://localhost}"
ENDPOINT="${HOST}/api/v1/products/products/"
DIAG_ENDPOINT="${HOST}/api/v1/instance/"
TMPFILE=$(mktemp)

echo "══════════════════════════════════════════════════════════"
echo "  NFR5 — Load Distribution Check"
echo "══════════════════════════════════════════════════════════"
echo "  Target   : ${ENDPOINT}"
echo "  Requests : ${N}"
echo ""

# ── Verify nginx is reachable ─────────────────────────────────────────────
if ! curl -sf -o /dev/null "${HOST}/api/v1/products/products/"; then
  echo "ERROR: Cannot reach ${HOST}. Is docker-compose up and nginx healthy?"
  exit 1
fi

# ── Fire requests and collect X-Served-By header ─────────────────────────
echo "Firing ${N} requests ..."

for i in $(seq 1 "$N"); do
  # -D - dumps response headers to stdout; we grep for X-Served-By
  served_by=$(curl -s -I "${ENDPOINT}" 2>/dev/null \
    | grep -i "x-served-by:" \
    | awk -F': ' '{print $2}' \
    | tr -d '\r')

  # Fallback: if no X-Served-By, try X-Instance-Id from the instance endpoint
  if [ -z "$served_by" ]; then
    served_by=$(curl -s "${DIAG_ENDPOINT}" 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('instance_id','unknown'))" \
      2>/dev/null || echo "unknown")
  fi

  echo "${served_by:-unknown}" >> "$TMPFILE"

  # Progress dot every 50 requests
  if [ $(( i % 50 )) -eq 0 ]; then
    printf "  %4d / %d done\n" "$i" "$N"
  fi
done

echo ""

# ── Build histogram ───────────────────────────────────────────────────────
echo "X-Served-By / Instance distribution:"
echo "──────────────────────────────────────────────────────────"

TOTAL=$(wc -l < "$TMPFILE")
EXPECTED=$(awk "BEGIN {printf \"%.0f\", $TOTAL / 3}")

sort "$TMPFILE" | uniq -c | sort -rn | while read count host; do
  pct=$(awk "BEGIN {printf \"%.1f\", ($count / $TOTAL) * 100}")

  # Bar chart (40 chars wide)
  bar_len=$(awk "BEGIN {printf \"%d\", int($count * 40 / $TOTAL)}")
  empty_len=$(( 40 - bar_len ))
  bar=$(python3 -c "print('█' * ${bar_len} + '░' * ${empty_len})" 2>/dev/null \
        || printf '%0.s#' $(seq 1 "$bar_len"))

  # Pass / Fail flag
  lo=$(awk "BEGIN {printf \"%.0f\", $TOTAL * 0.23}")
  hi=$(awk "BEGIN {printf \"%.0f\", $TOTAL * 0.43}")
  if [ "$count" -ge "$lo" ] && [ "$count" -le "$hi" ]; then
    flag="✓ OK"
  else
    flag="✗ SKEWED"
  fi

  printf "  %-22s │ %4d requests  (%5s%%)  %s  %s\n" \
         "$host" "$count" "$pct" "$bar" "$flag"
done

echo ""

# ── Summary ───────────────────────────────────────────────────────────────
total_backends=$(sort "$TMPFILE" | uniq | wc -l | tr -d ' ')
echo "Summary:"
echo "  Total requests   : ${TOTAL}"
echo "  Unique backends  : ${total_backends}"
echo "  Expected per backend: ~${EXPECTED} (33.3%)"
echo ""

if [ "$total_backends" -ge 3 ]; then
  echo "  NFR5 RESULT: ✓ Traffic distributed across ${total_backends} backends"
else
  echo "  NFR5 RESULT: ✗ Only ${total_backends} backend(s) seen — check nginx config"
fi

echo ""

# ── Failover hint ─────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────"
echo "To test failover (NFR5):"
echo "  docker compose stop web1"
echo "  bash tools/distribution_check.sh 100"
echo "  # Expect: 0 web1 requests, ~50/50 split between web2 and web3"
echo "  docker compose start web1"
echo "──────────────────────────────────────────────────────────"

rm -f "$TMPFILE"
