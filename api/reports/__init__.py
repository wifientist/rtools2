"""
Report type registry.

Each report type maps to a Jinja2 template and a data source function.
To add a new report type, add an entry here and create the corresponding
data source module and template file.
"""

REPORT_REGISTRY = {
    "migration": {
        "template": "reports/migration.html",
        "data_source": "reports.migration:fetch_report_data",
        "display_name": "Migration Dashboard",
    },
}
