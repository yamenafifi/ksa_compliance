import frappe
from frappe import _

from ksa_compliance.ksa_compliance.doctype.zatca_business_settings.zatca_business_settings import ZATCABusinessSettings
from ksa_compliance.ksa_compliance.doctype.sales_invoice_additional_fields.sales_invoice_additional_fields import (
    SalesInvoiceAdditionalFields,
)
from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry


from ksa_compliance import logger
from ksa_compliance.throw import fthrow
from ksa_compliance.translation import ft

from result import is_ok


def validate_payment_entry(self: PaymentEntry, method: str = None):
    if not self.custom_prepayment_invoice:
        return

    # A submitted prepayment invoice shows "Invoice", not the generic "Submitted" - unless it has
    # since been refunded, in which case that takes over (protects against ERPNext's own set_status
    # reverting either back to "Submitted" on save).
    if self.docstatus == 1 and not self.custom_is_prepayment_credit_note:
        if frappe.db.exists('Payment Entry', {'custom_original_prepayment_invoice': self.name, 'docstatus': 1}):
            self.status = 'Refunded'
        else:
            self.status = 'Invoice'

    # Set status to Credit Note if this is a credit note
    if self.docstatus == 1 and self.custom_is_prepayment_credit_note:
        self.status = 'Credit Note'

    # Force the tax to be Deduct and not included_in_paid_amount
    if self.taxes:
        for row in self.get('taxes'):
            if row.included_in_paid_amount:
                fthrow(
                    msg=ft('You cannot set Included in Paid Amount for Prepayment Invoice.'),
                    title=ft('This Action Is Not Allowed'),
                )
            if row.add_deduct_tax != 'Deduct':
                fthrow(
                    msg=ft('You cannot set Add Or Deduct type to: Add for Prepayment Invoice. Allowed type is Deduct.'),
                    title=ft('This Action Is Not Allowed'),
                )
            if row.charge_type not in ['Actual', 'On Paid Amount']:
                fthrow(
                    msg=ft(
                        'You cannot set Charge Type to anything other than Actual or On Paid Amount for Prepayment Invoice.'
                    ),
                    title=ft('This Action Is Not Allowed'),
                )

    if self.custom_is_prepayment_credit_note:
        _validate_prepayment_credit_note(self)


def _validate_prepayment_credit_note(self: PaymentEntry) -> None:
    if not self.custom_original_prepayment_invoice:
        fthrow(
            msg=ft('Please specify the Original Prepayment Invoice that this credit note is reversing.'),
            title=ft('Validation Error'),
        )

    original_pe = frappe.get_doc('Payment Entry', self.custom_original_prepayment_invoice)

    if not original_pe.custom_prepayment_invoice:
        fthrow(
            msg=ft(
                'The document "$pe" is not a prepayment invoice. '
                'The Original Prepayment Invoice field must point to a submitted prepayment.',
                pe=self.custom_original_prepayment_invoice,
            ),
            title=ft('Validation Error'),
        )

    if original_pe.docstatus != 1:
        fthrow(
            msg=ft(
                'The original prepayment invoice "$pe" must be submitted before a credit note can be issued against it.',
                pe=self.custom_original_prepayment_invoice,
            ),
            title=ft('Validation Error'),
        )

    # Prevent issuing more than one credit note per prepayment invoice
    existing_credit_note = frappe.db.exists(
        'Payment Entry',
        {
            'custom_original_prepayment_invoice': self.custom_original_prepayment_invoice,
            'docstatus': 1,
            'name': ('!=', self.name),
        },
    )
    if existing_credit_note:
        fthrow(
            msg=ft(
                'A credit note "$cn" already exists for prepayment invoice "$pe". '
                'You cannot issue more than one credit note per prepayment.',
                cn=existing_credit_note,
                pe=self.custom_original_prepayment_invoice,
            ),
            title=ft('This Action Is Not Allowed'),
        )

    # Credit note amount must not exceed the original prepayment amount
    original_amount = abs(original_pe.paid_amount)
    credit_note_amount = abs(self.paid_amount)
    if credit_note_amount > original_amount:
        fthrow(
            msg=ft(
                'The credit note amount ($credit) exceeds the original prepayment amount ($original). '
                'A credit note cannot be for more than the original invoice.',
                credit=frappe.format_value(credit_note_amount, {'fieldtype': 'Currency'}),
                original=frappe.format_value(original_amount, {'fieldtype': 'Currency'}),
            ),
            title=ft('Validation Error'),
        )


def _set_prepayment_status_after_submit(self: PaymentEntry) -> None:
    """What a prepayment's own `status` becomes the moment it is submitted - "Invoice" for an ordinary
    prepayment, "Credit Note" for one that reverses another (which also marks the ORIGINAL "Refunded").

    Must run AFTER core's own `on_submit()` (called from our `on_submit` hook, never from `validate`):
    `PaymentEntry.on_submit()` always ends with `self.set_status()`, which forces "Submitted" via a direct
    `db_set` - so anything written earlier (e.g. in a `validate` hook) is overwritten by the time this runs.
    Setting it back here, with our own `db_set`, is what actually sticks. `validate_payment_entry`'s own
    in-memory assignment stays alongside this - it is what keeps the status correct across any LATER
    save of an already-submitted entry, where `on_submit()` (and its `set_status()`) never runs again.

    Deliberately not part of the ZATCA path below: these statuses record what happened to the money, which is
    true whether or not ZATCA integration is enabled (or even configured) for the company, so they must never
    be skipped by the early returns that follow.
    """
    if self.custom_is_prepayment_credit_note:
        if self.custom_original_prepayment_invoice:
            frappe.db.set_value('Payment Entry', self.custom_original_prepayment_invoice, 'status', 'Refunded')
            logger.info(f'Marked original prepayment invoice {self.custom_original_prepayment_invoice} as Refunded')
        frappe.db.set_value('Payment Entry', self.name, 'status', 'Credit Note')
    else:
        frappe.db.set_value('Payment Entry', self.name, 'status', 'Invoice')


def create_prepayment_invoice_additional_fields_doctype(self: PaymentEntry, method: str = None):
    if not self.custom_prepayment_invoice:
        logger.info(f"Skipping additional fields for {self.name} because it's not a prepayment invoice")
        return

    _set_prepayment_status_after_submit(self)

    settings = ZATCABusinessSettings.for_invoice(self.name, self.doctype)
    if not settings:
        if ZATCABusinessSettings.is_revoked_for_company(self.company):
            logger.info(f'Skipping additional fields for {self.name} because of revoked ZATCA settings')
            return
        logger.info(f'Skipping additional fields for {self.name} because of missing ZATCA settings')
        return

    if not settings.enable_zatca_integration:
        logger.info(f'Skipping additional fields for {self.name} because ZATCA integration is disabled in settings')
        return

    # --- Invoice number assignment ---
    # frappe.rename_doc() commits immediately (not transactional). If SIAF creation later
    # fails and the outer transaction rolls back, the rename stays but the tabSeries counter
    # is rolled back. On the next submit attempt make_autoname() would re-generate the same
    # number — which now already exists. We handle this with two guards:
    #
    # Guard 1: if this PE already has a number assigned from a previous partial attempt, reuse
    #          it so we never generate a duplicate.
    # Guard 2: _generate_prepayment_invoice_number() skips any number that already exists as
    #          a Payment Entry, keeping the series monotonically increasing.

    # Guard 1 — reuse existing number if already assigned
    existing_number = frappe.db.get_value('Payment Entry', self.name, 'custom_prepayment_invoice_number')
    if existing_number:
        invoice_number = existing_number
        logger.info(f'Reusing previously assigned invoice number {invoice_number} for {self.name}')
    else:
        invoice_number = _generate_prepayment_invoice_number(self.company)

    # Rename the Payment Entry itself so that doc.name IS the invoice number.
    # Skip if already renamed (e.g. previous attempt committed the rename but failed on SIAF).
    if self.name != invoice_number:
        try:
            frappe.rename_doc('Payment Entry', self.name, invoice_number, force=True)
            self.name = invoice_number
            logger.info(f'Renamed Payment Entry → {invoice_number}')
        except Exception as exc:
            logger.error(f'Failed to rename Payment Entry {self.name} to {invoice_number}: {exc}')
            fthrow(
                msg=ft(
                    'Could not assign invoice number "$num" to this prepayment entry. '
                    'Please contact your administrator. (Detail: $detail)',
                    num=invoice_number,
                    detail=str(exc),
                ),
                title=ft('Naming Error'),
            )
    else:
        # PE was already renamed in a previous commit — just continue
        logger.info(f'Payment Entry already named {invoice_number}, skipping rename')

    # Also persist the invoice number in the display field
    frappe.db.set_value('Payment Entry', self.name, 'custom_prepayment_invoice_number', invoice_number)
    self.custom_prepayment_invoice_number = invoice_number

    prepayment_additional_fields_doc = SalesInvoiceAdditionalFields.create_for_invoice(self.name, self.doctype)
    is_live_sync = settings.is_live_sync
    prepayment_additional_fields_doc.insert()

    if is_live_sync:
        # We're running in the context of invoice submission (on_submit hook). We only want to run our ZATCA logic if
        # the invoice submits successfully after on_submit is run successfully from all apps.
        frappe.utils.background_jobs.enqueue(
            _submit_additional_fields, doc=prepayment_additional_fields_doc, enqueue_after_commit=True
        )


def _generate_prepayment_invoice_number(company: str) -> str:
    from frappe.model.naming import make_autoname

    # Fetch the naming series from the most recent Sales Invoice for this company
    # so we share the exact same counter and sequence
    naming_series = frappe.db.get_value(
        'Sales Invoice',
        {'company': company},
        'naming_series',
        order_by='creation desc',
    )
    if not naming_series:
        fthrow(
            ft(
                'Cannot generate a prepayment invoice number: no Sales Invoices found for company $company '
                'to determine the naming series. Please create at least one Sales Invoice first.',
                company=company,
            )
        )

    # Guard 2 — skip any number that already exists as a Payment Entry.
    # This handles the case where a previous rename committed but the tabSeries counter
    # was rolled back, causing make_autoname() to re-generate an already-used number.
    max_attempts = 20
    for attempt in range(max_attempts):
        invoice_number = make_autoname(naming_series)
        if not frappe.db.exists('Payment Entry', invoice_number):
            if attempt > 0:
                logger.warning(f'Skipped {attempt} already-used number(s); assigned {invoice_number}')
            return invoice_number
        logger.warning(f'Invoice number {invoice_number} already exists as a Payment Entry — skipping')

    fthrow(
        ft(
            'Could not generate a unique invoice number after $n attempts. '
            'Please contact your administrator.',
            n=max_attempts,
        )
    )


def _submit_additional_fields(doc: SalesInvoiceAdditionalFields):
    logger.info(f'Submitting {doc.name}')
    result = doc.submit_to_zatca()
    message = result.ok_value if is_ok(result) else result.err_value
    logger.info(f'Submission result: {message}')


def prevent_cancellation_of_prepayment_invoice(self: PaymentEntry, method):
    is_phase_2_enabled_for_company = ZATCABusinessSettings.is_enabled_for_company(self.company)
    if is_phase_2_enabled_for_company and self.custom_prepayment_invoice:
        frappe.throw(
            msg=_(
                'You cannot cancel a Prepayment Invoice according to ZATCA Regulations. '
                'Issue a Credit Note (Refund Prepayment) instead.'
            ),
            title=_('This Action Is Not Allowed'),
        )


@frappe.whitelist()
def make_prepayment_credit_note_doc(source_name: str) -> str:
    """Create a draft credit note Payment Entry against a submitted prepayment invoice.

    Returns the name of the new draft document so the client can route to it.
    All fields are populated server-side to avoid NaN from missing form handlers.
    """
    original: PaymentEntry = frappe.get_doc('Payment Entry', source_name)

    if not original.custom_prepayment_invoice:
        frappe.throw(_('The selected Payment Entry is not a prepayment invoice.'))
    if original.docstatus != 1:
        frappe.throw(_('The original prepayment invoice must be submitted before a credit note can be created.'))

    existing = frappe.db.exists(
        'Payment Entry',
        {'custom_original_prepayment_invoice': source_name, 'docstatus': ['!=', 2]},
    )
    if existing:
        frappe.throw(
            _('A credit note {0} already exists for this prepayment invoice.').format(frappe.bold(existing))
        )

    new_pe: PaymentEntry = frappe.new_doc('Payment Entry')
    new_pe.payment_type = 'Pay'
    new_pe.company = original.company
    new_pe.mode_of_payment = original.mode_of_payment
    new_pe.party_type = original.party_type
    new_pe.party = original.party
    new_pe.party_name = original.party_name

    # Reverse the flow of funds — money goes back to the customer
    new_pe.paid_from = original.paid_to
    new_pe.paid_from_account_type = original.paid_to_account_type
    new_pe.paid_from_account_currency = original.paid_to_account_currency
    new_pe.paid_to = original.paid_from
    new_pe.paid_to_account_type = original.paid_from_account_type
    new_pe.paid_to_account_currency = original.paid_from_account_currency

    new_pe.paid_amount = original.paid_amount
    new_pe.received_amount = original.received_amount
    new_pe.source_exchange_rate = original.source_exchange_rate or 1
    new_pe.target_exchange_rate = original.target_exchange_rate or 1
    new_pe.base_paid_amount = original.base_paid_amount
    new_pe.base_received_amount = original.base_received_amount

    # Use the human-readable invoice number if available (post-rename PEs),
    # otherwise fall back to the raw document name (legacy PEs)
    invoice_ref = original.custom_prepayment_invoice_number or original.name

    new_pe.custom_prepayment_invoice = 1
    new_pe.custom_is_prepayment_credit_note = 1
    new_pe.custom_original_prepayment_invoice = source_name
    new_pe.custom_prepayment_invoice_description = f'Credit Note for {invoice_ref}'
    new_pe.sales_taxes_and_charges_template = original.sales_taxes_and_charges_template
    new_pe.posting_date = frappe.utils.today()
    new_pe.custom_posting_time = frappe.utils.nowtime()

    # Copy tax rows exactly — same VAT must be reversed
    for tax in original.get('taxes', []):
        new_pe.append('taxes', {
            'account_head': tax.account_head,
            'charge_type': tax.charge_type,
            'rate': tax.rate,
            'tax_amount': tax.tax_amount,
            'add_deduct_tax': tax.add_deduct_tax,
            'description': tax.description,
            'included_in_paid_amount': tax.included_in_paid_amount,
            'cost_center': tax.cost_center,
        })

    new_pe.flags.ignore_validate = True
    new_pe.insert(ignore_permissions=True)
    return new_pe.name
