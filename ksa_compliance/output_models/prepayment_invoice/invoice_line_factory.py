import frappe
from dataclasses import asdict
from datetime import timedelta

from frappe.utils import flt

from .models import DocumentReference, Item, InvoiceLine, TaxSubtotal, TaxTotal
from ksa_compliance.standard_doctypes.tax_category import map_tax_category
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry
from erpnext.accounts.doctype.sales_invoice_payment.sales_invoice_payment import SalesInvoicePayment

from ksa_compliance import PREPAYMENT_INVOICE_CODE
from typing import cast


def invoice_line_create(doc: SalesInvoice) -> list[dict]:
    """Create invoice lines from document advances"""
    return [
        asdict(_create_invoice_line(advance, doc))
        for advance in doc.advances
        if frappe.db.get_value('Payment Entry', advance.reference_name, 'custom_prepayment_invoice')
    ]


def prorated_tax_split(payment_entry, allocated_amount) -> tuple[float, float]:
    """`payment_entry.paid_amount` is the NET amount (VAT excluded) and `total_taxes_and_charges` is
    its own real VAT, stored positive - the prepayment invoice's VAT row is a real, correctly-signed
    credit to the actual VAT account, never folded into `paid_amount` (see the EGC accounting app's
    own `milestone_billing_adapter._flag_as_zatca_prepayment`). A single prepayment can be split
    across several invoices via ERPNext's own "Get Advances", so the taxable/tax amount that belongs
    on THIS invoice's own line is whatever fraction of the whole payment `allocated_amount` is - not
    the payment's own full VAT every time, and never backed out of `allocated_amount` by dividing it
    (that only works if `allocated_amount` is itself VAT-inclusive, which it is not here).
    """
    paid_amount = abs(flt(payment_entry.paid_amount))
    taxable_amount = abs(flt(allocated_amount))
    if not paid_amount:
        return taxable_amount, 0.0
    fraction = taxable_amount / paid_amount
    tax_amount = abs(flt(payment_entry.total_taxes_and_charges)) * fraction
    return taxable_amount, tax_amount


def _create_invoice_line(advance: SalesInvoicePayment, doc: SalesInvoice) -> InvoiceLine:
    ref_doc = cast(PaymentEntry, frappe.get_cached_doc(advance.reference_type, advance.reference_name))

    tax_percent, tax_category_id = _get_tax_info(ref_doc)
    tax_category = map_tax_category(tax_category_id=tax_category_id)
    taxable_amount, tax_amount = prorated_tax_split(ref_doc, advance.allocated_amount)
    return InvoiceLine(
        idx=advance.idx
        + len(
            doc.items
        ),  # Ensure unique ID by offsetting with number of items also zatca refused advance.reference_name as id here
        invoice_quantity=0.0,
        line_extension_amount=0.0,
        uuid=_get_uuid(ref_doc),
        document_reference=DocumentReference(
            id=advance.reference_name,
            document_type_code=PREPAYMENT_INVOICE_CODE,
            issue_date=ref_doc.posting_date.strftime('%Y-%m-%d'),
            issue_time=_format_time(ref_doc.custom_posting_time),
        ),
        tax_total=TaxTotal(
            tax_amount=0.0,
            rounding_amount=0.0,
            tax_subtotal=TaxSubtotal(
                taxable_amount=taxable_amount,
                tax_amount=tax_amount,
                tax_category_id=tax_category.tax_category_code,
                tax_percent=tax_percent,
            ),
        ),
        item=Item(
            name=ref_doc.custom_prepayment_invoice_description,
            tax_category=tax_category.tax_category_code,
            tax_percent=tax_percent,
            tax_scheme='VAT',
        ),
        tax_category=tax_category,
        price=0.0,
    )


def _get_uuid(document_reference: PaymentEntry) -> str:
    return frappe.db.get_value(
        'Sales Invoice Additional Fields',
        {'invoice_doctype': document_reference.doctype, 'sales_invoice': document_reference.name},
        'uuid',
    )


def _format_time(time_delta: timedelta) -> str:
    """Convert timedelta to HH:MM:SS format"""
    total_seconds = time_delta.total_seconds()
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    return f'{hours:02d}:{minutes:02d}:00'


def _get_tax_info(document_reference: PaymentEntry) -> tuple[float, str]:
    """Get tax percent and category from document"""
    tax_percent = frappe.db.get_value(
        'Sales Taxes and Charges',
        {
            'parent': document_reference.sales_taxes_and_charges_template,
            'parenttype': 'Sales Taxes and Charges Template',
        },
        'rate',
    )
    tax_id = frappe.db.get_value(
        'Sales Taxes and Charges Template', document_reference.sales_taxes_and_charges_template, 'tax_category'
    )
    return tax_percent, tax_id
