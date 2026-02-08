import logging
import smtplib
from email.mime.text import MIMEText
import os
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# SMTP fallback config
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = os.getenv("SMTP_PORT")
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL")

# AWS SES config (uses existing AWS credentials from environment)
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

# Initialize SES client
_ses_client = None

def get_ses_client():
    """Lazy initialization of SES client."""
    global _ses_client
    if _ses_client is None:
        _ses_client = boto3.client('ses', region_name=AWS_REGION)
    return _ses_client


def send_email_ses(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str = None
) -> bool:
    """
    Send an email via Amazon SES.

    Args:
        to_email: Recipient email address
        subject: Email subject
        text_body: Plain text body
        html_body: Optional HTML body

    Returns:
        True if email sent successfully, False otherwise
    """
    if not FROM_EMAIL:
        logger.error("FROM_EMAIL not configured")
        return False

    ses = get_ses_client()

    body = {
        'Text': {'Data': text_body, 'Charset': 'UTF-8'}
    }
    if html_body:
        body['Html'] = {'Data': html_body, 'Charset': 'UTF-8'}

    try:
        response = ses.send_email(
            Source=FROM_EMAIL,
            Destination={'ToAddresses': [to_email]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': body
            }
        )
        logger.info(f"Email sent via SES. MessageId: {response['MessageId']}")
        return True
    except ClientError as e:
        logger.error(f"SES error: {e.response['Error']['Message']}")
        return False


def send_otp_email_via_smtp(to_email: str, otp_code: str):
    """Fallback: Send OTP email via SMTP."""
    logger.info(f'Sending OTP email to {to_email} via SMTP fallback')

    subject = "Your ruckus.tools OTP Code"
    body = f"""\
Hello,

Your One-Time Password (OTP) is:

**{otp_code}**

It will expire in 15 minutes. Please enter it to complete your login.

Thanks,
The Ruckus Tools Team
"""

    message = MIMEText(body, "plain")
    message["Subject"] = subject
    message["From"] = FROM_EMAIL
    message["To"] = to_email

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, [to_email], message.as_string())


def send_otp_email_via_api(to_email: str, otp_code: str):
    """Send OTP email via Amazon SES with SMTP fallback."""
    logger.info(f'Sending OTP email to {to_email} via SES')

    subject = f"{otp_code} ruckus.tools OTP Code"
    text_body = f"Your OTP is: {otp_code}\n\nThis code will expire in 15 minutes.\n\nhttps://ruckus.tools"
    html_body = f"Your OTP is: <strong>{otp_code}</strong><br><br>This code will expire in 15 minutes.<br><br><a href='https://ruckus.tools'>https://ruckus.tools</a>"

    success = send_email_ses(to_email, subject, text_body, html_body)

    if not success:
        logger.warning("SES failed, attempting SMTP fallback")
        try:
            send_otp_email_via_smtp(to_email, otp_code)
        except Exception as smtp_e:
            logger.error(f"SMTP fallback failed too: {smtp_e}")


def send_report_notification(
    to_emails: list[str],
    reporter_email: str,
    filename: str,
    folder_name: str,
    reason: str,
    file_id: int
):
    """Send email notification about a reported file via SES."""
    subject = f"[FILESHARE REPORT] File reported: {filename}"

    text_body = f"""A file has been reported on RUCKUS.Tools Fileshare.

Reporter: {reporter_email}
File: {filename}
Folder: {folder_name}
File ID: {file_id}

Reason for report:
{reason}

Please review this file and take appropriate action.

---
RUCKUS.Tools Fileshare
"""

    html_body = f"""
<h2>File Report Notification</h2>
<p>A file has been reported on RUCKUS.Tools Fileshare.</p>

<table style="border-collapse: collapse; margin: 20px 0;">
    <tr><td style="padding: 8px; font-weight: bold;">Reporter:</td><td style="padding: 8px;">{reporter_email}</td></tr>
    <tr><td style="padding: 8px; font-weight: bold;">File:</td><td style="padding: 8px;">{filename}</td></tr>
    <tr><td style="padding: 8px; font-weight: bold;">Folder:</td><td style="padding: 8px;">{folder_name}</td></tr>
    <tr><td style="padding: 8px; font-weight: bold;">File ID:</td><td style="padding: 8px;">{file_id}</td></tr>
</table>

<h3>Reason for Report</h3>
<p style="background: #f5f5f5; padding: 15px; border-radius: 5px;">{reason}</p>

<p>Please review this file and take appropriate action.</p>

<hr>
<p style="color: #666; font-size: 12px;">RUCKUS.Tools Fileshare</p>
"""

    success_count = 0
    for email in to_emails:
        if send_email_ses(email, subject, text_body, html_body):
            success_count += 1

    logger.info(f"Report notifications sent: {success_count}/{len(to_emails)}")
