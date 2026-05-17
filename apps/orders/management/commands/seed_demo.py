"""
Demo seeder.

Produces a medium-sized realistic dataset that is heavy enough to make the
NFR demos meaningful (especially NFR4 batch processing and NFR9 stress test)
without being so heavy it slows down the dev loop.

Approximate row counts:

    Categories            ~20
    Products              ~500
    Users + Customers     ~300
    Addresses             ~600
    StockItem             ~500   (one per product)
    StockMovement         ~5,000 (audit trail spread over 60 days)
    Cart                  ~200
    CartItem              ~700
    Order                 ~2,000 (spread over the last 90 days)
    OrderItem             ~6,000
    PaymentIntent         ~2,000

    Total                 ~17,000 rows

Run:

    docker-compose exec web1 python manage.py seed_demo
    docker-compose exec web1 python manage.py seed_demo --fresh   # wipe + reseed
    docker-compose exec web1 python manage.py seed_demo --orders 5000

Idempotency: pass `--fresh` to truncate the seeded tables first. Without
the flag, the command appends to whatever already exists (useful for
incremental experiments).
"""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from apps.cart.models import Cart, CartItem
from apps.inventory.models import StockItem, StockMovement
from apps.orders.models import Order, OrderItem
from apps.payments.models import PaymentIntent
from apps.products.models import Category, Product, ProductImage
from apps.users.models import Address, Customer

User = get_user_model()

# ---------------------------------------------------------------------------
# Static name pools (kept inline so the seeder has zero new dependencies).
# ---------------------------------------------------------------------------
ROOT_CATEGORIES = [
    "Electronics", "Computers", "Mobile", "Home", "Kitchen",
    "Books", "Clothing", "Sports",
]
SUBCATEGORIES = {
    "Electronics": ["Audio", "Cameras", "TVs"],
    "Computers": ["Laptops", "Desktops", "Accessories"],
    "Mobile": ["Smartphones", "Tablets"],
    "Home": ["Furniture", "Lighting"],
    "Kitchen": ["Cookware", "Small Appliances"],
}
ADJECTIVES = [
    "Pro", "Ultra", "Lite", "Max", "Mini", "Plus", "Air", "Studio",
    "Edge", "Neo", "Prime", "Core", "Nova", "Zen", "Flex",
]
NOUNS = [
    "Headphones", "Speaker", "Laptop", "Monitor", "Keyboard", "Mouse",
    "Phone", "Tablet", "Camera", "Drone", "Charger", "Router",
    "Lamp", "Chair", "Desk", "Pan", "Knife Set", "Blender",
    "Backpack", "Sneakers", "Jacket", "T-Shirt", "Watch", "Sunglasses",
]
COLORS = ["Black", "White", "Silver", "Blue", "Red", "Green", "Gray"]
FIRST_NAMES = [
    "Omar", "Layla", "Karim", "Sara", "Ali", "Nour", "Hadi", "Yasmin",
    "Tarek", "Rana", "Zaid", "Maya", "Ramy", "Lina", "Fadi", "Dana",
    "Khaled", "Sama", "Bashar", "Reem",
]
LAST_NAMES = [
    "Hadid", "Khoury", "Saleh", "Najjar", "Hariri", "Mansour", "Sayegh",
    "Awad", "Atallah", "Rahim", "Bishara", "Daher", "Touma", "Salim",
]
CITIES = ["Damascus", "Aleppo", "Homs", "Latakia", "Tartus", "Hama"]
COUNTRIES = ["SY", "LB", "JO", "EG", "AE", "SA"]


def _money(low: float, high: float) -> Decimal:
    return Decimal(f"{random.uniform(low, high):.2f}")


class Command(BaseCommand):
    help = "Seed a medium-sized realistic dataset for the e-commerce engine."

    # ---- options -----------------------------------------------------------
    def add_arguments(self, parser):
        parser.add_argument("--fresh", action="store_true",
                            help="Truncate seeded tables first (destructive).")
        parser.add_argument("--products", type=int, default=500)
        parser.add_argument("--customers", type=int, default=300)
        parser.add_argument("--orders", type=int, default=2000)
        parser.add_argument("--seed", type=int, default=42,
                            help="Random seed for reproducible data.")

    # ---- entry point -------------------------------------------------------
    def handle(self, *args, **opts):
        random.seed(opts["seed"])
        if opts["fresh"]:
            self._truncate()

        with transaction.atomic():
            self.stdout.write(self.style.NOTICE("→ categories"))
            categories = self._seed_categories()

            self.stdout.write(self.style.NOTICE(f"→ products ({opts['products']})"))
            products = self._seed_products(categories, n=opts["products"])

            self.stdout.write(self.style.NOTICE(f"→ users + customers ({opts['customers']})"))
            customers = self._seed_customers(n=opts["customers"])

            self.stdout.write(self.style.NOTICE("→ addresses"))
            self._seed_addresses(customers)

            self.stdout.write(self.style.NOTICE("→ stock items"))
            stock_items = self._seed_stock(products)

            self.stdout.write(self.style.NOTICE("→ stock movements (audit trail)"))
            self._seed_stock_movements(stock_items)

            self.stdout.write(self.style.NOTICE("→ carts + items"))
            self._seed_carts(customers, products)

            self.stdout.write(self.style.NOTICE(f"→ orders ({opts['orders']})"))
            orders = self._seed_orders(customers, products, n=opts["orders"])

            self.stdout.write(self.style.NOTICE("→ payment intents"))
            self._seed_payments(orders)

        self._print_summary()

    # ---- truncate ----------------------------------------------------------
    def _truncate(self) -> None:
        self.stdout.write(self.style.WARNING("Truncating seeded tables..."))
        # Order matters: respect FK constraints.
        PaymentIntent.objects.all().delete()
        OrderItem.objects.all().delete()
        Order.objects.all().delete()
        CartItem.objects.all().delete()
        Cart.objects.all().delete()
        StockMovement.objects.all().delete()
        StockItem.objects.all().delete()
        ProductImage.objects.all().delete()
        Product.objects.all().delete()
        Category.objects.all().delete()
        Address.objects.all().delete()
        Customer.objects.all().delete()
        Token.objects.exclude(user__is_superuser=True).delete()
        User.objects.filter(is_superuser=False).delete()

    # ---- seed steps --------------------------------------------------------
    def _seed_categories(self) -> list[Category]:
        roots = [Category(name=n, slug=n.lower().replace(" ", "-")) for n in ROOT_CATEGORIES]
        Category.objects.bulk_create(roots)
        roots = list(Category.objects.filter(parent__isnull=True))
        root_by_name = {c.name: c for c in roots}

        subs: list[Category] = []
        for parent_name, kids in SUBCATEGORIES.items():
            parent = root_by_name[parent_name]
            for k in kids:
                subs.append(Category(name=k, slug=k.lower().replace(" ", "-"), parent=parent))
        Category.objects.bulk_create(subs)
        return list(Category.objects.all())

    def _seed_products(self, categories: list[Category], n: int) -> list[Product]:
        leafs = [c for c in categories if c.parent_id is not None]
        products: list[Product] = []
        for i in range(n):
            adj = random.choice(ADJECTIVES)
            noun = random.choice(NOUNS)
            color = random.choice(COLORS)
            name = f"{adj} {noun} {color}"
            sku = f"SKU-{i+1:05d}"
            slug = f"{adj.lower()}-{noun.lower().replace(' ', '-')}-{color.lower()}-{i+1}"
            products.append(Product(
                sku=sku, name=name, slug=slug,
                description=f"{name} — premium build with reliable performance.",
                category=random.choice(leafs),
                price=_money(9.99, 1499.99),
                currency="USD",
                status=Product.ACTIVE if random.random() > 0.05 else Product.ARCHIVED,
            ))
        Product.objects.bulk_create(products, batch_size=200)
        products = list(Product.objects.all())

        images: list[ProductImage] = []
        for p in products:
            count = random.randint(1, 3)
            for pos in range(count):
                images.append(ProductImage(
                    product=p,
                    url=f"https://cdn.example.com/p/{p.sku}/{pos}.jpg",
                    alt=f"{p.name} view {pos + 1}",
                    position=pos,
                ))
        ProductImage.objects.bulk_create(images, batch_size=500)
        return products

    def _seed_customers(self, n: int) -> list[Customer]:
        users: list[User] = []
        existing = set(User.objects.values_list("username", flat=True))
        i = 0
        while len(users) < n:
            i += 1
            username = f"user{i:04d}"
            if username in existing:
                continue
            existing.add(username)
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            u = User(
                username=username,
                email=f"{username}@example.com",
                first_name=first,
                last_name=last,
                is_active=True,
            )
            u.set_password("Password123!")
            users.append(u)

        User.objects.bulk_create(users, batch_size=200)
        users = list(User.objects.filter(is_superuser=False).order_by("-id")[:n])

        customers = [
            Customer(
                user=u,
                phone=f"+963{random.randint(900000000, 999999999)}",
                wallet_balance=random.randint(500, 5000),
                loyalty_points=random.randint(0, 5000),
            )
            for u in users
        ]
        Customer.objects.bulk_create(customers, batch_size=200)

        # Tokens for the first 50 users so external testing has something
        # to use without manually issuing tokens.
        Token.objects.bulk_create(
            [Token(user=u, key=Token.generate_key()) for u in users[:50]],
            ignore_conflicts=True,
        )
        return list(Customer.objects.all())

    def _seed_addresses(self, customers: list[Customer]) -> None:
        addrs: list[Address] = []
        for c in customers:
            n_addr = random.choice([1, 1, 2])  # most have 1, some have 2
            for j in range(n_addr):
                kind = random.choice([Address.SHIPPING, Address.BILLING])
                addrs.append(Address(
                    customer=c,
                    kind=kind,
                    line1=f"{random.randint(1, 999)} Main St",
                    line2=f"Apt {random.randint(1, 50)}" if random.random() < 0.3 else "",
                    city=random.choice(CITIES),
                    region="",
                    postal_code=f"{random.randint(10000, 99999)}",
                    country=random.choice(COUNTRIES),
                    is_default=(j == 0),
                ))
        Address.objects.bulk_create(addrs, batch_size=500)

    def _seed_stock(self, products: list[Product]) -> list[StockItem]:
        items = [
            StockItem(
                product=p,
                on_hand=random.randint(0, 200),
                reserved=0,
                reorder_threshold=random.randint(5, 25),
            )
            for p in products
        ]
        StockItem.objects.bulk_create(items, batch_size=200)
        return list(StockItem.objects.select_related("product").all())

    def _seed_stock_movements(self, stock_items: list[StockItem]) -> None:
        """Inbound + adjust audit entries spread over the last 60 days.

        The reserve / consume / release entries that mirror order flow are
        produced by `_seed_orders` so they stay consistent with the orders
        themselves.
        """
        now = timezone.now()
        movements: list[StockMovement] = []
        for si in stock_items:
            # Initial inbound entry per stock item.
            movements.append(StockMovement(
                stock_item=si, kind=StockMovement.INBOUND,
                quantity=si.on_hand,
                reference="initial-stock",
                created_at=now - timedelta(days=60),
            ))
            # 5-15 random adjustment entries.
            for _ in range(random.randint(5, 15)):
                day = random.randint(1, 59)
                kind = random.choice([StockMovement.INBOUND, StockMovement.ADJUST])
                qty = random.randint(1, 30) * (1 if kind == StockMovement.INBOUND else random.choice([-1, 1]))
                movements.append(StockMovement(
                    stock_item=si, kind=kind, quantity=qty,
                    reference=f"audit-{random.randint(1000, 9999)}",
                    created_at=now - timedelta(days=day, hours=random.randint(0, 23)),
                ))
        # bulk_create cannot honor auto_now_add for created_at, so use the
        # default-managed value above (Django still applies it on bulk_create
        # only if the field has a default; auto_now_add is satisfied on
        # bulk_create as of Django 5).
        StockMovement.objects.bulk_create(movements, batch_size=1000)

    def _seed_carts(self, customers: list[Customer], products: list[Product]) -> None:
        sample = random.sample(customers, k=min(200, len(customers)))
        carts = []
        for c in sample:
            status_choice = random.choices(
                [Cart.OPEN, Cart.CHECKED_OUT, Cart.ABANDONED],
                weights=[0.6, 0.25, 0.15], k=1,
            )[0]
            carts.append(Cart(customer=c, status=status_choice))
        Cart.objects.bulk_create(carts, batch_size=200)
        carts = list(Cart.objects.select_related("customer").all())

        items: list[CartItem] = []
        for cart in carts:
            for product in random.sample(products, k=random.randint(1, 5)):
                items.append(CartItem(
                    cart=cart,
                    product=product,
                    quantity=random.randint(1, 4),
                    unit_price=product.price,
                ))
        # unique_together(cart, product) guaranteed by sample without replacement.
        CartItem.objects.bulk_create(items, batch_size=500, ignore_conflicts=True)

    def _seed_orders(
        self, customers: list[Customer], products: list[Product], n: int
    ) -> list[Order]:
        now = timezone.now()
        statuses = [Order.PAID, Order.SHIPPED, Order.DELIVERED, Order.PENDING, Order.CANCELLED]
        weights = [0.20, 0.20, 0.40, 0.10, 0.10]

        addresses_by_customer: dict[int, list[Address]] = {}
        for a in Address.objects.all():
            addresses_by_customer.setdefault(a.customer_id, []).append(a)

        orders: list[Order] = []
        for _ in range(n):
            cust = random.choice(customers)
            addrs = addresses_by_customer.get(cust.id) or []
            if not addrs:
                continue
            placed = now - timedelta(days=random.randint(0, 89), hours=random.randint(0, 23))
            orders.append(Order(
                customer=cust,
                status=random.choices(statuses, weights, k=1)[0],
                shipping_address=random.choice(addrs),
                billing_address=random.choice(addrs),
                currency="USD",
                # totals will be filled once items are known
                subtotal=Decimal("0"),
                tax=Decimal("0"),
                shipping_fee=Decimal("0"),
                total=Decimal("0"),
                placed_at=placed,
            ))
        Order.objects.bulk_create(orders, batch_size=500)
        orders = list(Order.objects.all())

        items: list[OrderItem] = []
        order_totals: dict[int, Decimal] = {}
        for o in orders:
            picks = random.sample(products, k=random.randint(1, 5))
            subtotal = Decimal("0")
            for p in picks:
                qty = random.randint(1, 3)
                line_total = (p.price * qty).quantize(Decimal("0.01"))
                subtotal += line_total
                items.append(OrderItem(
                    order=o,
                    product=p,
                    product_sku=p.sku,
                    product_name=p.name,
                    unit_price=p.price,
                    quantity=qty,
                    line_total=line_total,
                ))
            order_totals[o.id] = subtotal
        OrderItem.objects.bulk_create(items, batch_size=1000)

        # Backfill totals on the orders.
        to_update: list[Order] = []
        for o in orders:
            subtotal = order_totals.get(o.id, Decimal("0"))
            tax = (subtotal * Decimal("0.15")).quantize(Decimal("0.01"))
            shipping_fee = Decimal("0") if subtotal >= Decimal("100") else Decimal("5.00")
            o.subtotal = subtotal
            o.tax = tax
            o.shipping_fee = shipping_fee
            o.total = subtotal + tax + shipping_fee
            to_update.append(o)
        Order.objects.bulk_update(
            to_update, ["subtotal", "tax", "shipping_fee", "total"], batch_size=500,
        )
        return orders

    def _seed_payments(self, orders: list[Order]) -> None:
        intents: list[PaymentIntent] = []
        status_map = {
            Order.PAID:      PaymentIntent.CAPTURED,
            Order.SHIPPED:   PaymentIntent.CAPTURED,
            Order.DELIVERED: PaymentIntent.CAPTURED,
            Order.PENDING:   PaymentIntent.INIT,
            Order.CANCELLED: PaymentIntent.FAILED,
        }
        for o in orders:
            intents.append(PaymentIntent(
                order=o,
                external_id=f"pg_{random.randint(10**9, 10**10 - 1)}",
                amount=o.total,
                currency=o.currency,
                status=status_map.get(o.status, PaymentIntent.INIT),
            ))
        PaymentIntent.objects.bulk_create(intents, batch_size=500, ignore_conflicts=True)

    # ---- summary -----------------------------------------------------------
    def _print_summary(self) -> None:
        rows = [
            ("Categories", Category.objects.count()),
            ("Products", Product.objects.count()),
            ("ProductImages", ProductImage.objects.count()),
            ("Users", User.objects.count()),
            ("Customers", Customer.objects.count()),
            ("Addresses", Address.objects.count()),
            ("StockItems", StockItem.objects.count()),
            ("StockMovements", StockMovement.objects.count()),
            ("Carts", Cart.objects.count()),
            ("CartItems", CartItem.objects.count()),
            ("Orders", Order.objects.count()),
            ("OrderItems", OrderItem.objects.count()),
            ("PaymentIntents", PaymentIntent.objects.count()),
            ("AuthTokens", Token.objects.count()),
        ]
        self.stdout.write(self.style.SUCCESS("\nSeed complete:"))
        for name, count in rows:
            self.stdout.write(f"  {name:<16} {count:>8}")
        self.stdout.write(self.style.SUCCESS(
            "\nDefault user password: Password123!\n"
            "Tokens were issued for users user0001..user0050.\n"
        ))
