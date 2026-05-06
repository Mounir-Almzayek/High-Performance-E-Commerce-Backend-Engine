"""
Management command to run daily sales batch manually.

Usage:
    python manage.py run_daily_sales              # Run for yesterday
    python manage.py run_daily_sales --date 2026-05-01   # Run for specific date
    python manage.py run_daily_sales --dry-run     # Show what would be processed
"""
from datetime import date, datetime, timedelta, timezone

from django.core.management.base import BaseCommand
from django.utils import timezone as django_timezone

from apps.orders.models import DailySalesReport, Order
from tasks.daily_sales_batch import run_daily_sales


class Command(BaseCommand):
    help = "Run the daily sales batch job manually (NFR4)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Date to process (YYYY-MM-DD). Default: yesterday",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be processed without saving",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=1000,
            help="Chunk size for processing (default: 1000)",
        )
        parser.add_argument(
            "--max-workers",
            type=int,
            default=8,
            help="Max parallel workers (default: 8)",
        )

    def handle(self, *args, **options):
        # Determine date
        if options["date"]:
            target_date = date.fromisoformat(options["date"])
        else:
            target_date = date.today() - timedelta(days=1)

        self.stdout.write(self.style.NOTICE(f"Processing sales for: {target_date}"))

        # Show current data summary
        start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        completed_statuses = [Order.PAID, Order.SHIPPED, Order.DELIVERED]
        from apps.orders.models import OrderItem

        qs = OrderItem.objects.filter(
            order__placed_at__gte=start,
            order__placed_at__lt=end,
            order__status__in=completed_statuses,
        )

        count = qs.count()
        self.stdout.write(f"  Order items to process: {count}")

        if count == 0:
            self.stdout.write(self.style.WARNING("No data to process. Exiting."))
            return

        if options["dry_run"]:
            # Show sample of what would be processed
            self.stdout.write(self.style.NOTICE("\nDry run - sample data:"))
            for item in qs.select_related("order", "product")[:5]:
                self.stdout.write(
                    f"  Order {item.order_id}: {item.product_sku} x {item.quantity} = ${item.line_total}"
                )
            self.stdout.write(self.style.SUCCESS(f"\nWould process {count} items"))
            return

        # Run the actual task
        self.stdout.write(self.style.NOTICE("\nRunning batch job..."))

        # Override constants for this run
        from tasks import daily_sales_batch
        daily_sales_batch.CHUNK_SIZE = options["chunk_size"]
        daily_sales_batch.MAX_WORKERS = options["max_workers"]

        try:
            result = run_daily_sales()

            if result:
                self.stdout.write(self.style.SUCCESS("\n✓ Batch job completed successfully!"))
                self.stdout.write(f"  Date: {result['date']}")
                self.stdout.write(f"  Total Orders: {result['total_orders']}")
                self.stdout.write(f"  Total Revenue: ${result['total_revenue']}")
                self.stdout.write(f"  Items Sold: {result['total_items_sold']}")

                # Show saved report
                report = DailySalesReport.objects.get(date=target_date)
                self.stdout.write(self.style.SUCCESS(f"\n✓ Report saved: {report}"))
            else:
                self.stdout.write(self.style.WARNING("No report created (no data)"))

        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"\n✗ Batch job failed: {exc}"))
            raise
