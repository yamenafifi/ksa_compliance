
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
    const result = await frappe.call({
        method: 'ksa_compliance.standard_doctypes.payment_entry.payment_entry.make_prepayment_credit_note_doc',
        args: { source_name: frm.doc.name },
        freeze: true,
        freeze_message: __('Creating Credit Note...'),
    })

    if (result && result.message) {
        frappe.set_route('Form', 'Payment Entry', result.message)
    }
}
