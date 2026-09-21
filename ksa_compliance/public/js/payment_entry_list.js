frappe.listview_settings['Payment Entry'] = {
    get_indicator: function (doc) {
        if (doc.status === "Draft") {
            return [__("Draft"), "red", "status,=,Draft"];
        } else if (doc.status === "Credit Note") {
            return [__("Credit Note"), "orange", "status,=,Credit Note"];
        } else if (doc.status === "Refunded") {
            return [__("Refunded"), "green", "status,=,Refunded"];
        } else if (doc.status === "Submitted") {
            return [__("Submitted"), "blue", "status,=,Submitted"];
        } else if (doc.status === "Cancelled") {
            return [__("Cancelled"), "red", "status,=,Cancelled"];
        }
    }
}
