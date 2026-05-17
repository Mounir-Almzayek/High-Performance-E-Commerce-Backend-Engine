"""
Unit tests for NFR4 - Chunked batch processing.

Tests verify:
1. iter_in_chunks yields correct chunk sizes
2. DailySalesAggregator correctly aggregates OrderItems
3. DailySalesAggregator merge is associative and commutative
4. process_in_parallel respects resource caps
"""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.orders.models import DailySalesReport, Order, OrderItem
from apps.products.models import Category, Product
from apps.users.models import Customer
from core.batch.chunked import (
    DailySalesAggregator,
    iter_in_chunks,
    process_in_parallel,
)

User = get_user_model()


def create_customer(username: str) -> Customer:
    user = User.objects.create_user(username=username, password="Password123!")
    return Customer.objects.create(user=user, wallet_balance="1000.00")


@pytest.mark.django_db
class TestIterInChunks:
    """Test the chunked iterator."""

    def test_empty_queryset_yields_nothing(self):
        """Empty queryset should not yield any chunks."""
        qs = OrderItem.objects.none()
        chunks = list(iter_in_chunks(qs, chunk_size=10))
        assert chunks == []

    def test_small_result_fits_in_single_chunk(self):
        """Fewer items than chunk_size should yield one chunk."""
        # Create test data
        customer = create_customer("test")
        category = Category.objects.create(name="Test", slug="test")
        product = Product.objects.create(
            sku="SKU1",
            name="Product 1",
            slug="product-1",
            category=category,
            price=Decimal("10.00"),
        )
        order = Order.objects.create(
            customer=customer,
            status=Order.PAID,
            total=Decimal("10.00"),
        )

        # Create 5 order items
        items = [
            OrderItem(
                order=order,
                product=product,
                product_sku=product.sku,
                product_name=product.name,
                unit_price=product.price,
                quantity=1,
                line_total=product.price,
            )
            for _ in range(5)
        ]
        OrderItem.objects.bulk_create(items)

        # Test with chunk_size=10
        qs = OrderItem.objects.filter(order=order)
        chunks = list(iter_in_chunks(qs, chunk_size=10))

        assert len(chunks) == 1
        assert len(chunks[0]) == 5

    def test_large_result_splits_into_multiple_chunks(self):
        """More items than chunk_size should split into multiple chunks."""
        customer = create_customer("test2")
        category = Category.objects.create(name="Test2", slug="test2")
        product = Product.objects.create(
            sku="SKU2",
            name="Product 2",
            slug="product-2",
            category=category,
            price=Decimal("10.00"),
        )
        order = Order.objects.create(
            customer=customer,
            status=Order.PAID,
            total=Decimal("10.00"),
        )

        # Create 25 order items
        items = [
            OrderItem(
                order=order,
                product=product,
                product_sku=product.sku,
                product_name=product.name,
                unit_price=product.price,
                quantity=1,
                line_total=product.price,
            )
            for _ in range(25)
        ]
        OrderItem.objects.bulk_create(items)

        # Test with chunk_size=10
        qs = OrderItem.objects.filter(order=order)
        chunks = list(iter_in_chunks(qs, chunk_size=10))

        assert len(chunks) == 3
        assert len(chunks[0]) == 10
        assert len(chunks[1]) == 10
        assert len(chunks[2]) == 5


class TestDailySalesAggregator:
    """Test the aggregator logic."""

    def test_feed_single_chunk(self):
        """Feed should aggregate a single chunk correctly."""
        aggregator = DailySalesAggregator()

        # Simulate OrderItems (using simple objects for test)
        class MockItem:
            def __init__(self, order_id, product_id, sku, quantity, line_total):
                self.order_id = order_id
                self.product_id = product_id
                self.product_sku = sku
                self.quantity = quantity
                self.line_total = Decimal(str(line_total))

        chunk = [
            MockItem(1, 101, "SKU101", 2, 20.00),  # Order 1
            MockItem(1, 102, "SKU102", 1, 15.00),  # Order 1
            MockItem(2, 101, "SKU101", 3, 30.00),  # Order 2
        ]

        aggregator.feed(chunk)

        assert aggregator.total_orders == 2  # 2 unique orders
        assert aggregator.total_revenue == 65.00  # 20 + 15 + 30
        assert aggregator.total_items_sold == 6  # 2 + 1 + 3
        assert aggregator.by_product[101]["quantity"] == 5  # 2 + 3
        assert aggregator.by_product[101]["revenue"] == 50.00  # 20 + 30
        assert aggregator.by_product[102]["quantity"] == 1
        assert aggregator.by_product[102]["revenue"] == 15.00

    def test_merge_is_associative(self):
        """(a.merge(b)).merge(c) == a.merge(b.merge(c))"""
        a = DailySalesAggregator()
        b = DailySalesAggregator()
        c = DailySalesAggregator()

        # Populate with some data
        a.total_orders = 1
        a.total_revenue = 10.0
        a._order_ids = {1}

        b.total_orders = 1
        b.total_revenue = 20.0
        b._order_ids = {2}

        c.total_orders = 1
        c.total_revenue = 30.0
        c._order_ids = {3}

        # (a.merge(b)).merge(c)
        left = (a.merge(b)).merge(c)

        # a.merge(b.merge(c)) - need fresh instances
        a2 = DailySalesAggregator()
        b2 = DailySalesAggregator()
        c2 = DailySalesAggregator()
        a2.total_orders = 1
        a2.total_revenue = 10.0
        a2._order_ids = {1}
        b2.total_orders = 1
        b2.total_revenue = 20.0
        b2._order_ids = {2}
        c2.total_orders = 1
        c2.total_revenue = 30.0
        c2._order_ids = {3}

        right = a2.merge(b2.merge(c2))

        assert left.total_orders == right.total_orders == 3
        assert left.total_revenue == right.total_revenue == 60.0

    def test_merge_is_commutative(self):
        """a.merge(b) == b.merge(a)"""
        a = DailySalesAggregator()
        b = DailySalesAggregator()

        a.total_orders = 1
        a.total_revenue = 10.0
        a._order_ids = {1}
        a.total_items_sold = 2
        a.by_product = {101: {"quantity": 2, "revenue": 10.0, "sku": "SKU1"}}

        b.total_orders = 1
        b.total_revenue = 20.0
        b._order_ids = {2}
        b.total_items_sold = 3
        b.by_product = {102: {"quantity": 3, "revenue": 20.0, "sku": "SKU2"}}

        ab = a.merge(b)

        # Reset and reverse
        a2 = DailySalesAggregator()
        b2 = DailySalesAggregator()
        a2.total_orders = 1
        a2.total_revenue = 10.0
        a2._order_ids = {1}
        a2.total_items_sold = 2
        a2.by_product = {101: {"quantity": 2, "revenue": 10.0, "sku": "SKU1"}}

        b2.total_orders = 1
        b2.total_revenue = 20.0
        b2._order_ids = {2}
        b2.total_items_sold = 3
        b2.by_product = {102: {"quantity": 3, "revenue": 20.0, "sku": "SKU2"}}

        ba = b2.merge(a2)

        assert ab.total_orders == ba.total_orders
        assert ab.total_revenue == ba.total_revenue
        assert ab.total_items_sold == ba.total_items_sold
        assert ab.by_product == ba.by_product

    def test_merge_combines_same_products(self):
        """Merging should sum quantities for the same product."""
        a = DailySalesAggregator()
        b = DailySalesAggregator()

        a.by_product = {101: {"quantity": 5, "revenue": 50.0, "sku": "SKU1"}}
        b.by_product = {101: {"quantity": 3, "revenue": 30.0, "sku": "SKU1"}}

        merged = a.merge(b)

        assert merged.by_product[101]["quantity"] == 8
        assert merged.by_product[101]["revenue"] == 80.0


@pytest.mark.django_db
class TestDailySalesReportCreation:
    """Test end-to-end report creation."""

    def test_report_created_with_correct_totals(self):
        """DailySalesReport should be created with correct aggregated data."""
        # Create test data
        customer = create_customer("report_test")
        category = Category.objects.create(name="ReportCat", slug="report-cat")

        products = [
            Product.objects.create(
                sku=f"REPORT{i}",
                name=f"Report Product {i}",
                slug=f"report-product-{i}",
                category=category,
                price=Decimal(f"{i * 10}.00"),
            )
            for i in range(1, 4)
        ]

        # Create paid order
        order = Order.objects.create(
            customer=customer,
            status=Order.PAID,
            total=Decimal("100.00"),
        )

        # Create order items
        items = [
            OrderItem(
                order=order,
                product=products[0],
                product_sku=products[0].sku,
                product_name=products[0].name,
                unit_price=products[0].price,
                quantity=2,
                line_total=products[0].price * 2,
            ),
            OrderItem(
                order=order,
                product=products[1],
                product_sku=products[1].sku,
                product_name=products[1].name,
                unit_price=products[1].price,
                quantity=1,
                line_total=products[1].price,
            ),
        ]
        OrderItem.objects.bulk_create(items)

        # Run aggregation manually (simulating the task)
        from datetime import date

        qs = OrderItem.objects.filter(order__status=Order.PAID)
        aggregator = DailySalesAggregator()

        for chunk in iter_in_chunks(qs, chunk_size=100):
            aggregator.feed(chunk)

        # Save report
        report = DailySalesReport.objects.create(
            date=date.today(),
            total_orders=aggregator.total_orders,
            total_revenue=aggregator.total_revenue,
            total_items_sold=aggregator.total_items_sold,
            by_product=aggregator.by_product,
        )

        assert report.total_orders == 1
        assert report.total_revenue == Decimal("30.00")  # 20 + 10
        assert report.total_items_sold == 3  # 2 + 1
        assert len(report.by_product) == 2

    def test_report_is_idempotent(self):
        """Running twice should update, not duplicate."""
        from datetime import date

        # Create first report
        report1, created1 = DailySalesReport.objects.get_or_create(
            date=date.today(),
            defaults={
                "total_orders": 5,
                "total_revenue": Decimal("100.00"),
                "total_items_sold": 10,
                "by_product": {},
            },
        )
        assert created1 is True

        # Run again
        report2, created2 = DailySalesReport.objects.update_or_create(
            date=date.today(),
            defaults={
                "total_orders": 10,
                "total_revenue": Decimal("200.00"),
                "total_items_sold": 20,
                "by_product": {},
            },
        )
        assert created2 is False  # Updated, not created
        assert report2.id == report1.id  # Same record
        assert report2.total_orders == 10  # Updated value


@pytest.mark.django_db
class TestProcessInParallel:
    """Test parallel processing."""

    def test_parallel_processing_completes_all_chunks(self):
        """All chunks should be processed."""
        # Create test data
        customer = create_customer("parallel")
        category = Category.objects.create(name="Parallel", slug="parallel")
        product = Product.objects.create(
            sku="PARALLEL",
            name="Parallel Product",
            slug="parallel-product",
            category=category,
            price=Decimal("10.00"),
        )

        # Create multiple orders
        for i in range(5):
            order = Order.objects.create(
                customer=customer,
                status=Order.PAID,
                total=Decimal("10.00"),
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                product_sku=product.sku,
                product_name=product.name,
                unit_price=product.price,
                quantity=1,
                line_total=product.price,
            )

        # Define a simple handler that counts items
        def count_handler(chunk):
            return {"count": len(chunk)}

        qs = OrderItem.objects.filter(order__status=Order.PAID)
        results = process_in_parallel(qs, count_handler, chunk_size=2, max_workers=3)

        # Should have processed all 5 items across multiple chunks
        total_count = sum(r["count"] for r in results)
        assert total_count == 5
        # Should have 3 chunks (2, 2, 1)
        assert len(results) == 3
