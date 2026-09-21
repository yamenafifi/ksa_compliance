
frappe.ui.form.on('Payment Entry', {

    async refresh(frm) {
        if (frm.doc.custom_prepayment_invoice && frm.doc.docstatus == 1) {
            await set_zatca_integration_status(frm)

            // Only show the credit note button if no credit note has been issued yet
            const existing = await frappe.db.get_value(
                'Payment Entry',
                { custom_original_prepayment_invoice: frm.doc.name, docstatus: ['!=', 2] },
                'name'
            )
            if (!existing.message?.name) {
                frm.add_custom_button(__('Create Credit Note'), () => {
                    create_prepayment_credit_note(frm)
                }, __('Actions'))
            }
        }
    },
})



async function set_zatca_integration_status(frm) {
    const res = await frappe.call({
        method: "ksa_compliance.ksa_compliance.doctype.sales_invoice_additional_fields.sales_invoice_additional_fields.get_zatca_integration_status",
        args: {
            invoice_id: frm.doc.name,
            doctype: frm.doc.doctype
        },
    });

    const status = res.integration_status;
    if (status) {
        let color = "blue"
        if (status === 'Accepted') {
            color = "green"
        } else if (["Rejected", "Resend"].includes(status)) {
            color = "red"
        }
        frm.set_intro(`<b>Zatca Status: ${status}</b>`, color)
    }
}


async function create_prepayment_credit_note(frm) {
    const doc = frm.doc

    // Build a new Payment Entry pre-filled as a credit note
    const new_pe = frappe.model.get_new_doc('Payment Entry')

    new_pe.payment_type = 'Pay'
    new_pe.company = doc.company
    new_pe.mode_of_payment = doc.mode_of_payment
    new_pe.party_type = doc.party_type
    new_pe.party = doc.party
    new_pe.party_name = doc.party_name

    // Reverse the accounts: money flows back out to the customer
    new_pe.paid_from = doc.paid_to
    new_pe.paid_from_account_type = doc.paid_to_account_type
    new_pe.paid_from_account_currency = doc.paid_to_account_currency
    new_pe.paid_to = doc.paid_from
    new_pe.paid_to_account_type = doc.paid_from_account_type
    new_pe.paid_to_account_currency = doc.paid_from_account_currency

    new_pe.paid_amount = doc.paid_amount
    new_pe.received_amount = doc.received_amount
    new_pe.source_exchange_rate = doc.source_exchange_rate
    new_pe.target_exchange_rate = doc.target_exchange_rate

    // Prepayment credit note flags
    new_pe.custom_prepayment_invoice = 1
    new_pe.custom_is_prepayment_credit_note = 1
    new_pe.custom_original_prepayment_invoice = doc.name
    new_pe.custom_prepayment_invoice_description = __('Credit Note for {0}', [doc.name])
    new_pe.sales_taxes_and_charges_template = doc.sales_taxes_and_charges_template

    // Copy posting date/time
    new_pe.posting_date = frappe.datetime.get_today()
    new_pe.custom_posting_time = frappe.datetime.now_time()

    // Open the new document
    frappe.set_route('Form', 'Payment Entry', new_pe.name)
}
