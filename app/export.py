"""Full-data export to a single Excel workbook (one sheet per table)."""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select

from . import db
from .models import (
    Adjustment, AllocationLine, AuditLog, Customer, Order, OrderItem, Quotation, SizeRow, Transaction, Vendor, VendorAllocation,
)
from .money import alloc_paid, order_money


def _all(model):
    return db.session.scalars(select(model).order_by(model.id)).all()


def _sheet(wb: Workbook, title: str, headers: list[str], rows) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(list(row))
    ws.freeze_panes = "A2"


def build_workbook() -> BytesIO:
    wb = Workbook()
    wb.remove(wb.active)

    orders = _all(Order)
    _sheet(wb, "Orders", [
        "Order", "Customer", "Title", "Status", "Order date", "Delivery date", "Payment due",
        "Billed", "Received", "Outstanding", "Vendor cost", "Vendor due", "Expected profit", "Actual profit",
    ], (
        [o.number, o.customer.name, o.title, o.status, o.order_date, o.delivery_date, o.due_date,
         m.billed, m.received_net, m.outstanding, m.vendor_cost, m.vendor_due, m.expected_profit, m.actual_profit]
        for o in orders for m in [order_money(o)]
    ))
    _sheet(wb, "Customers", ["ID", "Name", "Phone", "Email", "Notes"],
           ([c.id, c.name, c.phone, c.email, c.notes] for c in _all(Customer)))
    _sheet(wb, "Vendors", ["ID", "Name", "Phone", "Credit days", "Notes"],
           ([v.id, v.name, v.phone, v.credit_days, v.notes] for v in _all(Vendor)))
    _sheet(wb, "Items", ["ID", "Order", "Item", "Details", "Pricing", "Rate", "Lump sum", "Qty", "Line total"],
           ([i.id, i.order.number, i.name, i.attributes, i.price_type, i.unit_price, i.lump_amount, i.qty,
             i.line_total] for i in _all(OrderItem)))
    _sheet(wb, "Sizes", ["ID", "Order", "Item", "Player", "Number", "Size", "Qty"],
           ([s.id, s.item.order.number, s.item.name, s.player_name, s.number, s.size, s.qty] for s in _all(SizeRow)))
    _sheet(wb, "Vendor allocations", [
        "ID", "Order", "Item", "Vendor", "Agreed cost", "Actual cost", "Status", "Received", "Due date", "Paid", "Due",
    ], (
        [a.id, a.item.order.number, a.item.name, a.vendor.name, a.agreed_total, a.actual_cost, a.status,
         a.received_date, a.effective_due_date, p, a.cost - p]
        for a in _all(VendorAllocation) for p in [alloc_paid(a)]
    ))
    _sheet(wb, "Vendor cost options", ["Allocation ID", "Order", "Item", "Vendor", "Option", "Qty", "Rate", "Amount"],
           ([line.allocation_id, line.allocation.item.order.number, line.allocation.item.name,
             line.allocation.vendor.name, line.label, line.qty, line.rate, line.amount]
            for line in _all(AllocationLine)))
    _sheet(wb, "Adjustments", ["ID", "Order", "Kind", "Label", "Amount"],
           ([a.id, a.order.number, a.kind, a.label, a.amount] for a in _all(Adjustment)))
    _sheet(wb, "Quotations", ["ID", "Order", "Version", "Created", "Total", "Approved", "Notes"],
           ([q.id, q.order.number, q.version, q.created_at, q.total, q.approved, q.notes] for q in _all(Quotation)))
    _sheet(wb, "Transactions", [
        "ID", "Date", "Order", "Direction", "Party", "Type", "Amount", "Mode", "Note", "Receipt ref",
    ], (
        [t.id, t.date, t.order.number, t.direction, t.party_name, t.type_label, t.amount, t.mode, t.note,
         t.receipt_ref]
        for t in _all(Transaction)
    ))
    _sheet(wb, "Audit log", ["Time", "Entity", "Entity ID", "Action", "Detail"],
           ([a.ts, a.entity, a.entity_id, a.action, a.detail] for a in _all(AuditLog)))

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
