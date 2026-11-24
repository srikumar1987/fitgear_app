from datetime import date, timedelta
from decimal import Decimal

from app import db
from app.formatting import format_inr
from app.models import (
    Adjustment, AllocationLine, Customer, Order, OrderItem, Quotation, SizeRow, Transaction, Vendor, VendorAllocation,
)
from app.money import ageing_bucket, auto_split, customer_balances, order_money, vendor_balances


def D(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


def make_order():
    customer = Customer(name="Vintage Warriors")
    vendor = Vendor(name="ABC Prints", credit_days=10)
    order = Order(customer=customer, title="Jerseys", delivery_date=date.today() - timedelta(days=5))
    half = OrderItem(order=order, name="Jersey, half sleeve", price_type="per_piece", unit_price=D(400))
    full = OrderItem(order=order, name="Jersey, full sleeve", price_type="mixed", unit_price=D(450), lump_amount=D(500))
    for i in range(9):
        half.sizes.append(SizeRow(player_name=f"H{i}", number=str(i), size="M", qty=1))
    full.manual_qty = 14
    db.session.add_all([customer, vendor, order])
    db.session.flush()
    return customer, vendor, order, half, full


def test_order_money_follows_the_plan_formulas(app):
    customer, vendor, order, half, full = make_order()
    assert half.line_total == D(3600)            # 9 x 400
    assert full.line_total == D(6800)            # 14 x 450 + 500

    order.quotations.append(Quotation(version=1, total=D(10400), approved=True))
    order.adjustments.append(Adjustment(kind="charge", label="Courier", amount=D(200)))
    order.adjustments.append(Adjustment(kind="discount", label="Loyalty", amount=D(400)))
    a1 = VendorAllocation(item=half, vendor=vendor, agreed_cost=D(2000))
    a2 = VendorAllocation(item=full, vendor=vendor, agreed_cost=D(4000), actual_cost=D(4200))
    db.session.add_all([a1, a2])
    db.session.flush()
    order.transactions.extend([
        Transaction(direction="in", party_type="customer", customer=customer, type="advance", amount=D(5000)),
        Transaction(direction="out", party_type="customer", customer=customer, type="refund", amount=D(100)),
        Transaction(direction="out", party_type="vendor", vendor=vendor, allocation=a1, type="vendor_payment", amount=D(1500)),
        Transaction(direction="out", party_type="other", type="expense", amount=D(300)),
    ])
    db.session.commit()

    m = order_money(order)
    assert m.billed == D(10200)                  # 10400 + 200 - 400
    assert m.received_net == D(4900)
    assert m.outstanding == D(5300)              # 10200 - 5000 + 100
    assert m.vendor_cost == D(6200)              # 2000 agreed + 4200 actual
    assert m.vendor_due == D(4700)
    assert m.expected_profit == D(4400)          # 10400 - (2000 + 4000)
    assert m.actual_profit == D(3700)            # 10200 - 6200 - 300


def test_agreed_cost_from_options(app):
    _, vendor, order, half, _ = make_order()
    order.quotations.append(Quotation(version=1, total=D(10400), approved=True))
    alloc = VendorAllocation(item=half, vendor=vendor, agreed_cost=D(0), lines=[
        AllocationLine(label="Half sleeve", qty=20, rate=D(250)),
        AllocationLine(label="Full sleeve", qty=30, rate=D(280)),
        AllocationLine(label="Track pant", qty=8, rate=D("299.50")),
    ])
    db.session.add(alloc)
    db.session.commit()
    assert alloc.agreed_total == D(15796)        # 5000 + 8400 + 2396
    assert alloc.lines_qty == 58
    assert alloc.cost == D(15796)
    assert order_money(order).expected_profit == D(10400) - D(15796)
    alloc.actual_cost = D(16000)
    assert alloc.cost == D(16000)


def test_unapproved_quotation_bills_nothing(app):
    _, _, order, _, _ = make_order()
    order.quotations.append(Quotation(version=1, total=D(10400), approved=False))
    db.session.commit()
    m = order_money(order)
    assert m.billed == 0
    assert m.expected_profit == m.items_total


def test_balances_and_ageing(app):
    customer, vendor, order, half, _ = make_order()
    order.quotations.append(Quotation(version=1, total=D(10400), approved=True))
    alloc = VendorAllocation(item=half, vendor=vendor, agreed_cost=D(2000), received_date=date.today() - timedelta(days=20))
    db.session.add(alloc)
    db.session.commit()

    [row] = customer_balances([customer])
    assert row.balance == D(10400)
    assert row.oldest_due == order.delivery_date
    assert row.matches("overdue", date.today()) and row.matches("0-15", date.today())

    [vrow] = vendor_balances([vendor])
    assert vrow.balance == D(2000)
    assert vrow.oldest_due == date.today() - timedelta(days=10)   # received + 10 credit days

    assert ageing_bucket(None, date.today()) == "not_due"
    assert ageing_bucket(date.today() - timedelta(days=45), date.today()) == "31-60"


def test_auto_split_oldest_first_and_leftover_on_last_open():
    assert auto_split(D(7000), [D(5000), D(3000), D(0)]) == [D(5000), D(2000), D(0)]
    assert auto_split(D(9000), [D(5000), D(3000), D(0)]) == [D(5000), D(4000), D(0)]
    assert auto_split(D(500), [D(0), D(0)]) == [D(500), D(0)]


def test_indian_number_format():
    assert format_inr(D(125000)) == "₹1,25,000"
    assert format_inr(D(12345678.5)) == "₹1,23,45,678.50"
    assert format_inr(D(-999)) == "-₹999"
