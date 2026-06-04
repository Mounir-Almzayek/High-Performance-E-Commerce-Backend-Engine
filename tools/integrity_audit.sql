-- ===========================================================================
-- integrity_audit.sql  —  NFR8 (ACID) post-run consistency audit
-- ===========================================================================
-- Asserts that the database is in a globally consistent state. Intended to be
-- run AFTER a load/soak run (NFR9) to prove that concurrency + transactions
-- left no partial or contradictory state behind.
--
-- CONTRACT: every row returned is an integrity VIOLATION.
--           A healthy system returns ZERO rows.
--
-- Usage:
--   docker compose exec db \
--     psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /app/tools/integrity_audit.sql
--   (or pipe the file in via stdin)
--
-- Exit-code friendly check (returns non-empty only on failure):
--   psql ... -t -A -f tools/integrity_audit.sql
-- ===========================================================================

WITH violations AS (

    -- 1. Stock can never be negative, and reserved can never exceed on_hand.
    --    (oversell / phantom-reservation detector — the headline NFR1/NFR7
    --     invariant that NFR8 atomicity is meant to preserve.)
    SELECT 'negative_or_impossible_stock' AS check_name,
           'stock_item_id=' || id::text AS ref,
           'on_hand=' || on_hand || ' reserved=' || reserved AS detail
    FROM inventory_stockitem
    WHERE on_hand < 0 OR reserved < 0 OR reserved > on_hand

    UNION ALL

    -- 2. A PAID order must have at least one CAPTURED payment.
    --    (money-not-taken-but-order-paid detector)
    SELECT 'paid_order_without_capture',
           'order_id=' || o.id::text,
           'public_id=' || o.public_id::text
    FROM orders_order o
    WHERE o.status = 'paid'
      AND NOT EXISTS (
          SELECT 1 FROM payments_paymentintent p
          WHERE p.order_id = o.id AND p.status = 'captured'
      )

    UNION ALL

    -- 3. A CAPTURED payment must belong to an order that actually advanced
    --    past PENDING (paid/shipped/delivered). A captured intent whose order
    --    is still pending or was cancelled means capture half-committed.
    SELECT 'captured_payment_on_unpaid_order',
           'intent_id=' || p.id::text,
           'order_id=' || o.id::text || ' order_status=' || o.status
    FROM payments_paymentintent p
    JOIN orders_order o ON o.id = p.order_id
    WHERE p.status = 'captured'
      AND o.status NOT IN ('paid', 'shipped', 'delivered')

    UNION ALL

    -- 4. No order may be charged twice: at most one CAPTURED intent per order.
    --    (double-charge detector)
    SELECT 'double_capture_on_order',
           'order_id=' || order_id::text,
           'captured_intents=' || COUNT(*)::text
    FROM payments_paymentintent
    WHERE status = 'captured'
    GROUP BY order_id
    HAVING COUNT(*) > 1

    UNION ALL

    -- 5. A fulfilled order (paid/shipped/delivered) must have line items.
    --    (paid-but-empty order detector — proves the Order + OrderItems
    --     composite write never half-committed.)
    SELECT 'fulfilled_order_without_items',
           'order_id=' || o.id::text,
           'status=' || o.status
    FROM orders_order o
    WHERE o.status IN ('paid', 'shipped', 'delivered')
      AND NOT EXISTS (
          SELECT 1 FROM orders_orderitem oi WHERE oi.order_id = o.id
      )

    UNION ALL

    -- 6. A REFUNDED payment must leave its order CANCELLED (refund_payment
    --    transitions both together). Anything else means the refund composite
    --    did not fully commit.
    SELECT 'refunded_payment_with_live_order',
           'intent_id=' || p.id::text,
           'order_id=' || o.id::text || ' order_status=' || o.status
    FROM payments_paymentintent p
    JOIN orders_order o ON o.id = p.order_id
    WHERE p.status = 'refunded'
      AND o.status <> 'cancelled'
)

SELECT check_name, ref, detail
FROM violations
ORDER BY check_name, ref;
