"""All balance and profit calculations. Nothing here is ever typed in by the user."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import ZERO


def _sum(values) -> Decimal:
    return sum(values, ZERO)


@dataclass
class OrderMoney:
    items_total: Decimal
    quoted_total: Decimal
    has_approved: bool
    charges: Decimal
    discounts: Decimal
    billed: Decimal
    received: Decimal
    refunds: Decimal
    agreed_vendor_cost: Decimal
    vendor_cost: Decimal
    vendor_paid: Decimal
    expenses: Decimal

    @property
    def received_net(self) -> Decimal:
        return self.received - self.refunds

    @property
    def outstanding(self) -> Decimal:
        return self.billed - self.received + self.refunds

    @property
    def vendor_due(self) -> Decimal:
        return self.vendor_cost - self.vendor_paid

    @property
    def expected_profit(self) -> Decimal:
        base = self.quoted_total if self.has_approved else self.items_total
        return base - self.agreed_vendor_cost

    @property
    def actual_profit(self) -> Decimal:
        return self.billed - self.vendor_cost - self.expenses


def order_money(order) -> OrderMoney:
    approved = order.approved_quotation
    allocations = [a for item in order.items for a in item.allocations]
    txns = order.transactions

    def total(predicate) -> Decimal:
        return _sum(t.amount for t in txns if predicate(t))

    quoted = approved.total if approved else ZERO
    charges = _sum(a.amount for a in order.adjustments if a.kind == "charge")
    discounts = _sum(a.amount for a in order.adjustments if a.kind == "discount")
    return OrderMoney(
        items_total=_sum(i.line_total for i in order.items),
        quoted_total=quoted,
        has_approved=approved is not None,
        charges=charges,
        discounts=discounts,
        billed=quoted + charges - discounts,
        received=total(lambda t: t.party_type == "customer" and t.direction == "in"),
        refunds=total(lambda t: t.party_type == "customer" and t.direction == "out"),
        agreed_vendor_cost=_sum(a.agreed_total for a in allocations),
        vendor_cost=_sum(a.cost for a in allocations),
        vendor_paid=total(lambda t: t.party_type == "vendor" and t.direction == "out"),
        expenses=total(lambda t: t.type == "expense"),
    )


def alloc_paid(allocation) -> Decimal:
    return _sum(t.amount for t in allocation.transactions if t.direction == "out")


def alloc_due(allocation) -> Decimal:
    return allocation.cost - alloc_paid(allocation)


def customer_payment_type(money: OrderMoney, amount: Decimal) -> str:
    if amount >= money.outstanding:
        return "final"
    return "advance" if money.received == 0 else "partial"


def auto_split(amount: Decimal, balances: list[Decimal]) -> list[Decimal]:
    """Fill the oldest balances first; anything left over goes on the last open balance."""
    remaining, parts = amount, []
    for balance in balances:
        take = min(max(balance, ZERO), remaining)
        parts.append(take)
        remaining -= take
    if parts and remaining > 0:
        target = max((i for i, b in enumerate(balances) if b > 0), default=0)
        parts[target] += remaining
    return parts


AGEING = [
    ("not_due", "Not due"),
    ("0-15", "1-15 days overdue"),
    ("16-30", "16-30 days"),
    ("31-60", "31-60 days"),
    ("60+", "60+ days"),
]
FILTERS = [("overdue", "All overdue"), ("soon", "Due in 7 days")] + AGEING


def ageing_bucket(due: date | None, today: date) -> str:
    if due is None or due >= today:
        return "not_due"
    days = (today - due).days
    if days <= 15:
        return "0-15"
    if days <= 30:
        return "16-30"
    return "31-60" if days <= 60 else "60+"


@dataclass
class PartyBalance:
    party: object
    open_count: int
    total: Decimal
    paid: Decimal
    balance: Decimal
    oldest_due: date | None
    last_payment: date | None

    def matches(self, key: str, today: date) -> bool:
        bucket = ageing_bucket(self.oldest_due, today)
        if key == "overdue":
            return bucket != "not_due"
        if key == "soon":
            return self.oldest_due is not None and today <= self.oldest_due <= today + timedelta(days=7)
        return bucket == key


def _sorted(rows: list[PartyBalance]) -> list[PartyBalance]:
    return sorted(rows, key=lambda r: (r.oldest_due or date.max, -r.balance, r.party.name.lower()))


def customer_balances(customers) -> list[PartyBalance]:
    rows = []
    for customer in customers:
        open_orders = [(o, m) for o in customer.orders for m in [order_money(o)] if m.outstanding != 0]
        dues = [o.due_date for o, m in open_orders if m.outstanding > 0 and o.due_date]
        last = max((t.date for t in customer.transactions if t.direction == "in"), default=None)
        rows.append(PartyBalance(
            customer, len(open_orders),
            _sum(m.billed for _, m in open_orders),
            _sum(m.received_net for _, m in open_orders),
            _sum(m.outstanding for _, m in open_orders),
            min(dues, default=None), last,
        ))
    return _sorted(rows)


def vendor_balances(vendors) -> list[PartyBalance]:
    rows = []
    for vendor in vendors:
        open_allocs = [(a, p) for a in vendor.allocations for p in [alloc_paid(a)] if a.cost - p != 0]
        dues = [a.effective_due_date for a, p in open_allocs if a.cost - p > 0 and a.effective_due_date]
        last = max((t.date for t in vendor.transactions if t.direction == "out"), default=None)
        rows.append(PartyBalance(
            vendor, len(open_allocs),
            _sum(a.cost for a, _ in open_allocs),
            _sum(p for _, p in open_allocs),
            _sum(a.cost - p for a, p in open_allocs),
            min(dues, default=None), last,
        ))
    return _sorted(rows)
