from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.types import Integer, TypeDecorator

from . import db

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

ORDER_STATUSES = ["Enquiry", "Quoted", "Advance received", "In design", "In production", "Delivered", "Closed"]
PRICE_TYPES = {"per_piece": "Per piece", "lump_sum": "Lump sum", "mixed": "Lump sum + per piece"}
ALLOCATION_STATUSES = ["Pending", "In production", "Received"]
PAYMENT_MODES = ["UPI", "Cash", "Bank transfer", "Cheque"]
TRANSACTION_TYPES = {
    "advance": "Advance",
    "partial": "Partial",
    "final": "Final",
    "vendor_payment": "Vendor payment",
    "expense": "Expense",
    "refund": "Refund",
}


def to_decimal(value) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(CENT, ROUND_HALF_UP)
    raw = str(value).replace(",", "").replace("₹", "").strip()
    if not raw:
        return ZERO
    try:
        return Decimal(raw).quantize(CENT, ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a valid amount") from None


class Money(TypeDecorator):
    """Stores rupees as integer paise so amounts are exact on SQLite and PostgreSQL."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else int(to_decimal(value) * 100)

    def process_result_value(self, value, dialect):
        return None if value is None else (Decimal(value) / 100).quantize(CENT)


class Admin(db.Model):
    __tablename__ = "admin"
    id = db.Column(db.Integer, primary_key=True)
    password_hash = db.Column(db.String(256), nullable=False)


class Setting(db.Model):
    __tablename__ = "setting"
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")


def get_setting(key: str, default: str = "") -> str:
    setting = db.session.get(Setting, key)
    return setting.value if setting else default


def set_setting(key: str, value: str) -> None:
    setting = db.session.get(Setting, key) or Setting(key=key)
    setting.value = value
    db.session.add(setting)


class Customer(db.Model):
    __tablename__ = "customer"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), nullable=False, default="")
    email = db.Column(db.String(200), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    orders = db.relationship("Order", back_populates="customer", lazy="selectin", order_by="Order.id")
    transactions = db.relationship("Transaction", back_populates="customer", lazy="selectin")


class Vendor(db.Model):
    __tablename__ = "vendor"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), nullable=False, default="")
    credit_days = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    allocations = db.relationship("VendorAllocation", back_populates="vendor", lazy="selectin")
    transactions = db.relationship("Transaction", back_populates="vendor", lazy="selectin")


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Enquiry")
    order_date = db.Column(db.Date, nullable=False, default=date.today)
    delivery_date = db.Column(db.Date)
    payment_due_date = db.Column(db.Date)
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    customer = db.relationship("Customer", back_populates="orders")
    items = db.relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan", lazy="selectin",
        order_by="[OrderItem.position, OrderItem.id]",
    )
    adjustments = db.relationship(
        "Adjustment", back_populates="order", cascade="all, delete-orphan", lazy="selectin", order_by="Adjustment.id"
    )
    quotations = db.relationship(
        "Quotation", back_populates="order", cascade="all, delete-orphan", lazy="selectin", order_by="Quotation.version"
    )
    transactions = db.relationship(
        "Transaction", back_populates="order", cascade="all, delete-orphan", lazy="selectin",
        order_by="[Transaction.date, Transaction.id]",
    )
    files = db.relationship(
        "OrderFile", back_populates="order", cascade="all, delete-orphan", lazy="selectin", order_by="OrderFile.id"
    )

    @property
    def number(self) -> str:
        return f"TUR-{self.id:04d}"

    @property
    def due_date(self) -> date | None:
        return self.payment_due_date or self.delivery_date

    @property
    def approved_quotation(self) -> Quotation | None:
        return next((q for q in self.quotations if q.approved), None)


class OrderItem(db.Model):
    __tablename__ = "order_item"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    attributes = db.Column(db.String(300), nullable=False, default="")
    price_type = db.Column(db.String(20), nullable=False, default="per_piece")
    unit_price = db.Column(Money, nullable=False, default=ZERO)
    lump_amount = db.Column(Money, nullable=False, default=ZERO)
    manual_qty = db.Column(db.Integer, nullable=False, default=0)
    position = db.Column(db.Integer, nullable=False, default=0)

    order = db.relationship("Order", back_populates="items")
    sizes = db.relationship(
        "SizeRow", back_populates="item", cascade="all, delete-orphan", lazy="selectin",
        order_by="[SizeRow.position, SizeRow.id]",
    )
    allocations = db.relationship(
        "VendorAllocation", back_populates="item", cascade="all, delete-orphan", lazy="selectin",
        order_by="VendorAllocation.id",
    )

    @property
    def qty(self) -> int:
        return sum(s.qty or 0 for s in self.sizes) if self.sizes else (self.manual_qty or 0)

    @property
    def line_total(self) -> Decimal:
        total = ZERO
        if self.price_type in ("per_piece", "mixed"):
            total += (self.unit_price or ZERO) * self.qty
        if self.price_type in ("lump_sum", "mixed"):
            total += self.lump_amount or ZERO
        return total.quantize(CENT)


class SizeRow(db.Model):
    __tablename__ = "size_row"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("order_item.id"), nullable=False)
    player_name = db.Column(db.String(100), nullable=False, default="")
    number = db.Column(db.String(20), nullable=False, default="")
    size = db.Column(db.String(20), nullable=False, default="")
    qty = db.Column(db.Integer, nullable=False, default=1)
    position = db.Column(db.Integer, nullable=False, default=0)

    item = db.relationship("OrderItem", back_populates="sizes")


class VendorAllocation(db.Model):
    __tablename__ = "vendor_allocation"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("order_item.id"), nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"), nullable=False)
    agreed_cost = db.Column(Money, nullable=False, default=ZERO)
    actual_cost = db.Column(Money)
    status = db.Column(db.String(20), nullable=False, default="Pending")
    received_date = db.Column(db.Date)
    due_date = db.Column(db.Date)

    item = db.relationship("OrderItem", back_populates="allocations")
    vendor = db.relationship("Vendor", back_populates="allocations", lazy="joined")
    transactions = db.relationship("Transaction", back_populates="allocation", lazy="selectin")
    lines = db.relationship(
        "AllocationLine", back_populates="allocation", cascade="all, delete-orphan", lazy="selectin",
        order_by="AllocationLine.id",
    )

    @property
    def agreed_total(self) -> Decimal:
        """Sum of the priced options (half sleeve, track pant, ...) or the single lump agreed cost."""
        if self.lines:
            return sum((line.amount for line in self.lines), ZERO)
        return self.agreed_cost or ZERO

    @property
    def lines_qty(self) -> int:
        return sum(line.qty or 0 for line in self.lines)

    @property
    def cost(self) -> Decimal:
        return self.actual_cost if self.actual_cost is not None else self.agreed_total

    @property
    def effective_due_date(self) -> date | None:
        if self.due_date:
            return self.due_date
        if self.received_date:
            return self.received_date + timedelta(days=self.vendor.credit_days or 0)
        return None


class AllocationLine(db.Model):
    """One priced option within a vendor's agreed cost, e.g. 'Full sleeve' 30 x ₹280."""

    __tablename__ = "allocation_line"
    id = db.Column(db.Integer, primary_key=True)
    allocation_id = db.Column(db.Integer, db.ForeignKey("vendor_allocation.id"), nullable=False)
    label = db.Column(db.String(100), nullable=False, default="")
    qty = db.Column(db.Integer, nullable=False, default=0)
    rate = db.Column(Money, nullable=False, default=ZERO)

    allocation = db.relationship("VendorAllocation", back_populates="lines")

    @property
    def amount(self) -> Decimal:
        return ((self.rate or ZERO) * (self.qty or 0)).quantize(CENT)


class Adjustment(db.Model):
    __tablename__ = "adjustment"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    kind = db.Column(db.String(10), nullable=False)  # charge | discount
    label = db.Column(db.String(200), nullable=False, default="")
    amount = db.Column(Money, nullable=False, default=ZERO)

    order = db.relationship("Order", back_populates="adjustments")


class Quotation(db.Model):
    __tablename__ = "quotation"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    lines_json = db.Column(db.Text, nullable=False, default="[]")
    total = db.Column(Money, nullable=False, default=ZERO)
    notes = db.Column(db.Text, nullable=False, default="")
    approved = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    order = db.relationship("Order", back_populates="quotations")

    @property
    def lines(self) -> list[dict]:
        return json.loads(self.lines_json or "[]")


FILE_KINDS = {"design": "Design", "printed": "After print"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class OrderFile(db.Model):
    """An uploaded design mockup or after-print photo. The file itself lives in the uploads folder."""

    __tablename__ = "order_file"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("order_item.id"))
    kind = db.Column(db.String(10), nullable=False)  # design | printed
    stored_name = db.Column(db.String(100), nullable=False, unique=True)
    original_name = db.Column(db.String(255), nullable=False, default="")
    note = db.Column(db.String(300), nullable=False, default="")
    approved = db.Column(db.Boolean, nullable=False, default=False)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    order = db.relationship("Order", back_populates="files")
    item = db.relationship("OrderItem")

    @property
    def is_image(self) -> bool:
        return any(self.stored_name.lower().endswith(ext) for ext in IMAGE_EXTENSIONS)


class Transaction(db.Model):
    __tablename__ = "ledger"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    direction = db.Column(db.String(3), nullable=False)  # in | out
    party_type = db.Column(db.String(10), nullable=False)  # customer | vendor | other
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"))
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"))
    allocation_id = db.Column(db.Integer, db.ForeignKey("vendor_allocation.id"))
    type = db.Column(db.String(20), nullable=False)
    amount = db.Column(Money, nullable=False)
    mode = db.Column(db.String(20), nullable=False, default="")
    note = db.Column(db.String(300), nullable=False, default="")
    receipt_ref = db.Column(db.String(30), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    date = db.Column(db.Date, nullable=False, default=date.today)

    order = db.relationship("Order", back_populates="transactions")
    customer = db.relationship("Customer", back_populates="transactions")
    vendor = db.relationship("Vendor", back_populates="transactions")
    allocation = db.relationship("VendorAllocation", back_populates="transactions")

    @property
    def party_name(self) -> str:
        if self.customer:
            return self.customer.name
        if self.vendor:
            return self.vendor.name
        return "-"

    @property
    def type_label(self) -> str:
        return TRANSACTION_TYPES.get(self.type, self.type)


class OldImport(db.Model):
    """One imported spreadsheet of orders from before the app was used."""

    __tablename__ = "old_import"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False, default="")
    sheet = db.Column(db.String(100), nullable=False, default="")
    imported_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    orders = db.relationship(
        "OldOrder", back_populates="source", cascade="all, delete-orphan", lazy="selectin", order_by="OldOrder.id"
    )


class OldOrder(db.Model):
    """A historical order row. Read-only report data; never used for balances."""

    __tablename__ = "old_order"
    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(db.Integer, db.ForeignKey("old_import.id"), nullable=False)
    row_no = db.Column(db.Integer, nullable=False, default=0)
    year = db.Column(db.Integer, nullable=False)
    order_date = db.Column(db.Date)
    ref = db.Column(db.String(50), nullable=False, default="")
    name = db.Column(db.String(255), nullable=False, default="")
    customer = db.Column(db.String(255), nullable=False, default="")
    items = db.Column(db.Text, nullable=False, default="")
    billed = db.Column(Money)
    spent = db.Column(Money)
    received = db.Column(Money)
    profit = db.Column(Money)

    source = db.relationship("OldImport", back_populates="orders")


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, nullable=False, default=datetime.now)
    entity = db.Column(db.String(30), nullable=False)
    entity_id = db.Column(db.Integer)
    action = db.Column(db.String(20), nullable=False)
    detail = db.Column(db.String(500), nullable=False, default="")


def audit(entity: str, entity_id: int | None, action: str, detail: str = "") -> None:
    db.session.add(AuditLog(entity=entity, entity_id=entity_id, action=action, detail=detail[:500]))
