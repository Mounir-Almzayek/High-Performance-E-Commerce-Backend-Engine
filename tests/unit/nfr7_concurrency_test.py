import sys
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

from apps.inventory import services
from apps.inventory.models import StockItem, StockMovement
from apps.products.models import Category, Product


def run_test(
    output_path="tools/locust/results/nfr7-concurrency-test.txt",
    initial_stock=5,
    concurrent_requests=20,
):
    logging.getLogger("core.aop").disabled = True

    accepted = []
    rejected = []
    unexpected_errors = []
    reference_prefix = f"nfr7-{int(time.time())}"

    with open(output_path, "w", encoding="utf8") as out:
        def log(*args, **kwargs):
            txt = " ".join(str(a) for a in args)
            print(txt, **kwargs)
            out.write(txt + "\n")

        log("Starting NFR7 same-product concurrency test")

        category, _ = Category.objects.get_or_create(
            slug="nfr7-test",
            defaults={"name": "NFR7 Test Category"},
        )
        product, _ = Product.objects.get_or_create(
            sku="NFR7-HOT-PRODUCT",
            defaults={
                "name": "NFR7 Hot Product",
                "slug": "nfr7-hot-product",
                "category": category,
                "price": "10.00",
            },
        )
        stock_item, _ = StockItem.objects.get_or_create(product=product)

        StockMovement.objects.filter(stock_item=stock_item).delete()
        StockItem.objects.filter(pk=stock_item.pk).update(
            on_hand=initial_stock,
            reserved=0,
            reorder_threshold=0,
            version=0,
        )
        stock_item.refresh_from_db()

        log(f"Product id: {product.id}")
        log(f"Product name: {product.name}")
        log(f"Initial stock on_hand: {stock_item.on_hand}")
        log(f"Initial reserved: {stock_item.reserved}")
        log(f"Initial available: {stock_item.available}")
        log(f"Concurrent reservation requests on same product: {concurrent_requests}")
        log("Each request attempts to reserve qty=1")

        barrier = threading.Barrier(concurrent_requests)
        results_lock = threading.Lock()

        def reserve_once(i):
            reference = f"{reference_prefix}-req-{i}"
            try:
                barrier.wait()
                services.reserve_stock(
                    product_id=product.id,
                    qty=1,
                    reference=reference,
                )
                with results_lock:
                    accepted.append(reference)
            except services.NotEnoughStock as exc:
                with results_lock:
                    rejected.append((reference, str(exc)))
            except Exception as exc:  # noqa: BLE001
                with results_lock:
                    unexpected_errors.append((reference, repr(exc)))
            finally:
                close_old_connections()

        started_at = time.time()
        with ThreadPoolExecutor(max_workers=concurrent_requests) as executor:
            list(executor.map(reserve_once, range(concurrent_requests)))
        elapsed = time.time() - started_at

        stock_item.refresh_from_db()
        movement_count = StockMovement.objects.filter(
            stock_item=stock_item,
            kind=StockMovement.RESERVE,
            reference__startswith=reference_prefix,
        ).count()

        log("--- Result ---")
        log(f"Total concurrent requests: {concurrent_requests}")
        log(f"Accepted reservations: {len(accepted)}")
        log(f"Rejected reservations: {len(rejected)}")
        log(f"Unexpected errors: {len(unexpected_errors)}")
        log(f"Final on_hand: {stock_item.on_hand}")
        log(f"Final reserved: {stock_item.reserved}")
        log(f"Final available: {stock_item.available}")
        log(f"Reserve movement rows written: {movement_count}")
        log(f"Elapsed time(s): {round(elapsed, 3)}")

        log("--- Expected ---")
        log(f"Expected accepted reservations: {initial_stock}")
        log(f"Expected rejected reservations: {concurrent_requests - initial_stock}")
        log("Expected oversell: 0")

        passed = (
            len(accepted) == initial_stock
            and len(rejected) == concurrent_requests - initial_stock
            and len(unexpected_errors) == 0
            and stock_item.on_hand == initial_stock
            and stock_item.reserved == initial_stock
            and stock_item.available == 0
            and movement_count == initial_stock
        )

        log("--- Verdict ---")
        if passed:
            log("PASS: Pessimistic locking serialized concurrent reservations correctly.")
        else:
            log("FAIL: Concurrency result did not match expected locking behavior.")
            if unexpected_errors:
                log("Unexpected errors:")
                for reference, error in unexpected_errors:
                    log(reference, error)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(run_test())
