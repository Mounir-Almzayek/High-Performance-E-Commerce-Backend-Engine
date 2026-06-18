import sys
import time
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.cache.redis_cache import cache_get_or_set, invalidate_product
from apps.products.models import Product


def run_test(output_path="tools/locust/results/nfr6-cache-test.txt"):
    with open(output_path, "w", encoding="utf8") as out:
        def log(*args, **kwargs):
            txt = " ".join(str(a) for a in args)
            print(txt, **kwargs)
            out.write(txt + "\n")

        log("Starting NFR6 cache integration test")

        p = Product.objects.first()
        if not p:
            log("No Product rows found in database. Aborting test.")
            return 2

        pid = p.id
        key = f"product:{pid}"
        N = 20

        log(f"Using Product id={pid} name={p.name}")
        old_force_debug_cursor = connection.force_debug_cursor
        connection.force_debug_cursor = True

        try:
            def build_product_payload():
                product = Product.objects.only("id", "name", "price").get(pk=pid)
                return {
                    "id": product.id,
                    "name": product.name,
                    "price": str(product.price),
                }

            # Ensure cache is clear
            invalidate_product(pid)

            # Measure DB queries when repeatedly building (cache miss each time)
            log(f"Running {N} iterations forcing cache miss (invalidate each iteration)...")
            t0 = time.time()
            with CaptureQueriesContext(connection) as cq_miss:
                for i in range(N):
                    invalidate_product(pid)
                    cache_get_or_set(key, build_product_payload, ttl=60)
            t1 = time.time()
            log("Miss run time(s):", round(t1 - t0, 3))
            log("DB queries (misses):", len(cq_miss))

            # Now populate cache once
            invalidate_product(pid)
            cache_get_or_set(key, build_product_payload, ttl=60)

            # Measure DB queries when hitting cached value
            log(f"Running {N} iterations hitting cache (should be cached)...")
            t2 = time.time()
            with CaptureQueriesContext(connection) as cq_hit:
                for i in range(N):
                    cache_get_or_set(key, build_product_payload, ttl=60)
            t3 = time.time()
            log("Hit run time(s):", round(t3 - t2, 3))
            log("DB queries (hits):", len(cq_hit))
        finally:
            connection.force_debug_cursor = old_force_debug_cursor

        # Summary
        log("--- Summary ---")
        log(f"Iterations per phase: {N}")
        log(f"Miss phase DB queries: {len(cq_miss)}")
        log(f"Hit phase DB queries: {len(cq_hit)}")
        if len(cq_miss) > 0:
            reduction = 100.0 * (1.0 - (len(cq_hit) / len(cq_miss)))
        else:
            reduction = 0.0
        log(f"Approx DB query reduction: {round(reduction,2)}%")

    return 0


if __name__ == '__main__':
    sys.exit(run_test())
