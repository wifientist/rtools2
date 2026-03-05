"""
Generic Report Engine.

Renders a Jinja2 template with data from a report-specific data source,
converts it to PDF via WeasyPrint, and emails it as an attachment.

Report types are registered in api/reports/__init__.py. To add a new report
type, create a data source module and a template — no changes needed here.
"""
import importlib
import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from sqlalchemy.orm import Session

from models.scheduled_report import ScheduledReport
from reports import REPORT_REGISTRY
from utils.email import send_email_with_attachment, send_email_ses

logger = logging.getLogger(__name__)

# Jinja2 template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


def _import_callable(dotted_path: str):
    """
    Import a callable from a dotted path like 'reports.migration:fetch_report_data'.
    """
    module_path, func_name = dotted_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


async def generate_report_pdf(report_type: str, context_id: str, db: Session) -> tuple[bytes, dict]:
    """
    Generate a PDF report.

    Args:
        report_type: Key into REPORT_REGISTRY (e.g., "migration")
        context_id: Context identifier passed to the data source
        db: Database session

    Returns:
        Tuple of (pdf_bytes, template_context_dict)
    """
    config = REPORT_REGISTRY.get(report_type)
    if not config:
        raise ValueError(f"Unknown report type: {report_type}")

    # Fetch data via report-specific data source
    data_fn = _import_callable(config["data_source"])
    template_context = await data_fn(context_id, db)

    # Render HTML template
    template = jinja_env.get_template(config["template"])
    html_content = template.render(**template_context)

    # Convert to PDF
    pdf_bytes = HTML(string=html_content).write_pdf()

    logger.info(
        f"Generated {report_type} report for context={context_id}: {len(pdf_bytes)} bytes"
    )
    return pdf_bytes, template_context


async def generate_and_send_report(report: ScheduledReport, db: Session) -> dict:
    """
    Full pipeline: fetch data → render template → PDF → email attachment.

    Args:
        report: ScheduledReport model instance
        db: Database session

    Returns:
        Result dict with status and details
    """
    config = REPORT_REGISTRY.get(report.report_type)
    if not config:
        raise ValueError(f"Unknown report type: {report.report_type}")

    display_name = config["display_name"]

    # Generate PDF
    pdf_bytes, template_context = await generate_report_pdf(
        report.report_type, report.context_id, db
    )

    # Build email content
    generated_at = template_context.get("generated_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    report_label = template_context.get("controller_name", display_name)
    subject = f"{display_name} Report: {report_label} — {generated_at}"
    filename = f"{report.report_type}_report_{datetime.utcnow().strftime('%Y-%m-%d')}.pdf"

    # Pull stats from template context for the email summary
    ctx = template_context
    total_aps = ctx.get("total_aps", 0)
    target_aps = ctx.get("target_aps", 0)
    pct = ctx.get("percentage", 0)
    operational = ctx.get("operational", 0)
    offline = ctx.get("offline", 0)
    total_switches = ctx.get("total_switches", 0)
    total_venues = ctx.get("total_venues", 0)
    total_clients = ctx.get("total_clients", 0)
    message = ctx.get("message", "")

    text_body = (
        f"{display_name} Report — {report_label}\n"
        f"Generated: {generated_at}\n\n"
        f"Progress: {total_aps:,} / {target_aps:,} APs ({pct:.1f}%)\n"
        f"{message}\n\n"
        f"Total APs: {total_aps:,}  |  Operational: {operational:,}  |  Offline: {offline:,}\n"
        f"Switches: {total_switches:,}  |  Venues: {total_venues:,}  |  Clients: {total_clients:,}\n\n"
        f"Full report attached as PDF."
    )

    # Progress bar color
    bar_color = "#22c55e" if pct >= 100 else "#2563eb"
    pct_clamped = min(pct, 100)

    html_body = f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
  <h2 style="color: #111827; margin: 0 0 4px 0;">{display_name} Report</h2>
  <p style="color: #6b7280; margin: 0 0 20px 0;">{report_label} &mdash; {generated_at}</p>

  <!-- Progress -->
  <div style="background: #f3f4f6; border-radius: 12px; height: 32px; position: relative; overflow: hidden; margin-bottom: 6px;">
    <div style="height: 32px; border-radius: 12px; width: {pct_clamped:.1f}%; background: {bar_color};"></div>
  </div>
  <p style="text-align: center; font-size: 14px; font-weight: 600; color: #374151; margin: 0 0 4px 0;">
    {total_aps:,} / {target_aps:,} APs ({pct:.1f}%)
  </p>
  <p style="text-align: center; font-size: 13px; color: #6b7280; font-style: italic; margin: 0 0 20px 0;">
    {message}
  </p>

  <!-- Stats grid -->
  <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
    <tr>
      <td style="padding: 10px 8px; text-align: center; border: 1px solid #e5e7eb; border-radius: 8px;">
        <div style="font-size: 20px; font-weight: 700; color: #111827;">{total_aps:,}</div>
        <div style="font-size: 11px; color: #6b7280;">Total APs</div>
      </td>
      <td style="padding: 10px 8px; text-align: center; border: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #16a34a;">{operational:,}</div>
        <div style="font-size: 11px; color: #6b7280;">Operational</div>
      </td>
      <td style="padding: 10px 8px; text-align: center; border: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #d97706;">{offline:,}</div>
        <div style="font-size: 11px; color: #6b7280;">Offline</div>
      </td>
    </tr>
    <tr>
      <td style="padding: 10px 8px; text-align: center; border: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #111827;">{total_switches:,}</div>
        <div style="font-size: 11px; color: #6b7280;">Switches</div>
      </td>
      <td style="padding: 10px 8px; text-align: center; border: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #111827;">{total_venues:,}</div>
        <div style="font-size: 11px; color: #6b7280;">Venues</div>
      </td>
      <td style="padding: 10px 8px; text-align: center; border: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #111827;">{total_clients:,}</div>
        <div style="font-size: 11px; color: #6b7280;">Clients</div>
      </td>
    </tr>
  </table>

  <p style="font-size: 13px; color: #6b7280;">Full report attached as PDF.</p>

  <hr style="border: none; border-top: 1px solid #e5e7eb; margin-top: 24px;">
  <p style="font-size: 12px; color: #9ca3af; text-align: center; margin-top: 12px;">Ruckus.Tools</p>
</div>
"""

    # Send single email with all recipients in To:
    sent = send_email_with_attachment(
        to_email=report.recipients,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        attachment_bytes=pdf_bytes,
        attachment_filename=filename,
    )

    logger.info(
        f"Report email {'sent' if sent else 'FAILED'} to {len(report.recipients)} recipient(s) "
        f"for {report.report_type} context={report.context_id}"
    )

    return {
        "status": "success",
        "report_type": report.report_type,
        "context_id": report.context_id,
        "emails_sent": len(report.recipients) if sent else 0,
        "emails_total": len(report.recipients),
    }
