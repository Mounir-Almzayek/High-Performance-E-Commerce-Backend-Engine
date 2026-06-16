"""Compare inventory reservation concurrency-control strategies for NFR7.

Run from the repository root, for example:

    python tools/benchmarks/nfr7_locking_benchmark.py \
        --product-id 1 --stock 100 --workers 50 --requests 200 --mode all

The no-lock and optimistic implementations in this file are benchmark-only.
Production inventory writes must continue to use apps.inventory.services.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from django.db import OperationalError, connections, transaction  # noqa: E402
from django.db.models import F  # noqa: E402

from apps.inventory import services  # noqa: E402
from apps.inventory.models import StockItem, StockMovement  # noqa: E402

RESULT_PATH = REPO_ROOT / "results" / "nfr7_locking_results.json"
MODES = ("nolock", "pessimistic", "optimistic")


class BenchmarkRejected(Exception):
    """The request could not reserve stock."""

    def __init__(self, retries: int = 0):
        super().__init__("stock reservation rejected")
        self.retries = retries


@dataclass
class OperationResult:
    success: bool
    latency_ms: float
    retries: int = 0
    deadlock: bool = False


def _is_deadlock(exc: OperationalError) -> bool:
    return "deadlock" in str(exc).lower()


def _reset_stock(product_id: int, stock: int) -> StockItem:
    stock_item = StockItem.objects.get(product_id=product_id)
    StockMovement.objects.filter(stock_item=stock_item).delete()
    StockItem.objects.filter(pk=stock_item.pk).update(
        on_hand=stock,
        reserved=0,
        reorder_threshold=0,
        version=0,
    )
    stock_item.refresh_from_db()
    return stock_item


def _nolock_reserve(product_id: int, reference: str) -> int:
    """Unsafe read-modify-write baseline that intentionally permits oversell."""
    stock_item = StockItem.objects.get(product_id=product_id)
    if stock_item.available < 1:
        raise BenchmarkRejected

    # Widen the interleaving window so the lost-update race is observable.
    time.sleep(0.001)
    stock_item.reserved += 1
    stock_item.version += 1
    stock_item.save(update_fields=["reserved", "version", "updated_at"])
    StockMovement.objects.create(
        stock_item=stock_item,
        kind=StockMovement.RESERVE,
        quantity=1,
        reference=reference,
    )
    return 0


def _pessimistic_reserve(product_id: int, reference: str) -> int:
    try:
        services.reserve_stock(product_id=product_id, qty=1, reference=reference)
    except services.NotEnoughStock as exc:
        raise BenchmarkRejected from exc
    return 0


def _optimistic_reserve(product_id: int, reference: str) -> int:
    """Benchmark-only optimistic CAS reservation with unbounded conflict retry."""
    retries = 0
    while True:
        stock_item = StockItem.objects.get(product_id=product_id)
        if stock_item.available < 1:
            raise BenchmarkRejected(retries)

        with transaction.atomic():
            rows = (
                StockItem.objects
                .filter(
                    pk=stock_item.pk,
                    version=stock_item.version,
                    reserved__lt=F("on_hand"),
                )
                .update(
                    reserved=F("reserved") + 1,
                    version=F("version") + 1,
                )
            )
            if rows == 1:
                StockMovement.objects.create(
                    stock_item_id=stock_item.pk,
                    kind=StockMovement.RESERVE,
                    quantity=1,
                    reference=reference,
                )
                return retries
        retries += 1


def _run_operation(
    reserve: Callable[[int, str], int],
    product_id: int,
    request_id: int,
    mode: str,
) -> OperationResult:
    connections.close_all()
    started = time.perf_counter()
    try:
        retries = reserve(product_id, f"nfr7-bench-{mode}-{request_id}")
        return OperationResult(
            success=True,
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=retries,
        )
    except BenchmarkRejected as exc:
        return OperationResult(
            success=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            retries=exc.retries,
        )
    except OperationalError as exc:
        return OperationResult(
            success=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            deadlock=_is_deadlock(exc),
        )
    finally:
        # Thread pools outlive individual calls and CONN_MAX_AGE keeps healthy
        # connections open. Close explicitly so one mode cannot exhaust
        # PostgreSQL connection slots before the next mode starts.
        connections.close_all()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summarize(
    mode: str,
    results: list[OperationResult],
    duration: float,
    stock_item: StockItem,
    stock: int,
) -> dict:
    latencies = [result.latency_ms for result in results]
    successes = sum(result.success for result in results)
    rejections = len(results) - successes
    return {
        "mode": mode,
        "successes": successes,
        "rejections": rejections,
        "oversell": max(0, successes - stock, stock_item.reserved - stock),
        "duration_seconds": round(duration, 6),
        "throughput_ops_sec": round(len(results) / duration, 2) if duration else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 3),
        "p50_latency_ms": round(_percentile(latencies, 0.50), 3),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        "p99_latency_ms": round(_percentile(latencies, 0.99), 3),
        "retry_count": sum(result.retries for result in results),
        "deadlocks": sum(result.deadlock for result in results),
        "final_reserved": stock_item.reserved,
        "stock_movements": StockMovement.objects.filter(stock_item=stock_item).count(),
    }


def run_mode(
    mode: str,
    *,
    product_id: int,
    stock: int,
    workers: int,
    requests: int,
) -> dict:
    stock_item = _reset_stock(product_id, stock)
    reserve = {
        "nolock": _nolock_reserve,
        "pessimistic": _pessimistic_reserve,
        "optimistic": _optimistic_reserve,
    }[mode]

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(
                lambda request_id: _run_operation(
                    reserve, product_id, request_id, mode
                ),
                range(requests),
            )
        )
    duration = time.perf_counter() - started

    stock_item.refresh_from_db()
    return _summarize(mode, results, duration, stock_item, stock)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-id", type=int, required=True)
    parser.add_argument("--stock", type=int, default=100)
    parser.add_argument("--workers", type=int, default=50)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument(
        "--mode",
        choices=("all", *MODES),
        default="all",
    )
    args = parser.parse_args()
    for name in ("product_id", "stock", "workers", "requests"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")
    return args


def main() -> None:
    args = parse_args()
    StockItem.objects.get(product_id=args.product_id)

    # Keep the benchmark focused on locking and independent of broker health.
    from apps.tasks.notifications import send_low_stock_alert

    send_low_stock_alert.delay = lambda *_args, **_kwargs: None

    modes = MODES if args.mode == "all" else (args.mode,)
    payload = {
        "configuration": {
            "product_id": args.product_id,
            "stock": args.stock,
            "workers": args.workers,
            "requests": args.requests,
            "mode": args.mode,
        },
        "results": [
            run_mode(
                mode,
                product_id=args.product_id,
                stock=args.stock,
                workers=args.workers,
                requests=args.requests,
            )
            for mode in modes
        ],
    }

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {RESULT_PATH}")


if __name__ == "__main__":
    main()
