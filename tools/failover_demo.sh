#!/usr/bin/env bash
# tools/failover_demo.sh
# ----------------------
# Demonstrates Nginx health-check failover for NFR5.
# Stops web1 mid-burst, verifies traffic continues on web2/web3,
# then restarts web1 and confirms it re-enters rotation.
#
# Requires: docker-compose, curl
# Usage: bash tools/failover_demo.sh

set -euo pipefail

TARGET="http://localhost/healthz"
BURST=150
FAIL_TIMEOUT=12   # Nginx fail_timeout=10s + 2s margin

run_burst() {
  local label=$1
  local n=$2
  local tmpfile; tmpfile=$(mktemp)

  echo
  echo "[FAILOVER DEMO] ${label} (${n} requests)..."
  for i in $(seq 1 "$n"); do
    curl -s -o /dev/null -w "%{http_code} %header{x-served-by}\n" "$TARGET" >> "$tmpfile" 2>/dev/null || echo "000 error" >> "$tmpfile"
  done

  grep -v "^000" "$tmpfile" | awk '{print $2}' | sort | uniq -c | sort -rn | while read count host; do
    printf "    %-20s : %4d requests\n" "$host" "$count"
  done

  errors=$(grep "^000" "$tmpfile" | wc -l || echo 0)
  printf "  Errors: %d\n" "$errors"
  rm -f "$tmpfile"
}

echo "======================================================"
echo "  NFR5 — Failover Demo"
echo "======================================================"

# Baseline — all backends up
run_burst "Baseline (all backends UP)" "$BURST"

# Kill web1
echo
echo "[FAILOVER DEMO] Stopping web1 ..."
docker-compose stop web1 2>/dev/null || docker compose stop web1

sleep 1

# Burst with web1 down
run_burst "Burst with web1 DOWN (web2+web3 only)" "$BURST"

# Restart web1
echo
echo "[FAILOVER DEMO] Starting web1 ..."
docker-compose start web1 2>/dev/null || docker compose start web1

echo "[FAILOVER DEMO] Waiting ${FAIL_TIMEOUT}s for Nginx fail_timeout to expire ..."
sleep "$FAIL_TIMEOUT"

# Burst after recovery
run_burst "Burst after web1 RECOVERY (all backends)" "$BURST"

echo
echo "[FAILOVER DEMO] Done."
echo "  Expected: ≤ 5 errors during the kill moment, 0 errors otherwise."
echo "  Nginx re-adds web1 automatically after fail_timeout=10s."
