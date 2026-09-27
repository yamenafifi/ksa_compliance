import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

from dataclasses import asdict

from frappe.utils import flt

from .invoice_line_factory import invoice_line_create, prorated_tax_split
from .models import PrepaymentInvoice


def prepayment_invoice_factory_create(doc: SalesInvoice) -> PrepaymentInvoice:
    prepayment_invoice = PrepaymentInvoice(
        prepaid_amount=_get_prepaid_amount(doc),
        currency=doc.currency,
        invoice_lines=invoice_line_create(doc),
    )
    return asdict(prepayment_invoice)


def _get_prepaid_amount(doc: SalesInvoice) -> float:
    prepaid_amount = 0
    for row in doc.advances:
        payment_entry = frappe.db.get_value(
            'Payment Entry',
            row.reference_name,
            ['custom_prepayment_invoice', 'paid_amount', 'total_taxes_and_charges'],
            as_dict=True,
        )
        if payment_entry and payment_entry.custom_prepayment_invoice:
            # `row.allocated_amount` is the NET portion of the payment applied to THIS invoice - its
            # own share of the payment's real VAT is prorated the same way the per-line breakdown is
            # (see `invoice_line_factory.prorated_tax_split`'s own docstring for why), never the
            # payment's full VAT regardless of how much of it is actually allocated here.
            _, tax_amount = prorated_tax_split(payment_entry, row.allocated_amount)
            prepaid_amount += abs(flt(row.allocated_amount)) + tax_amount
    return abs(prepaid_amount) if prepaid_amount else 0.0
