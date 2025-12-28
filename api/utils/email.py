import logging
import smtplib
from email.mime.text import MIMEText
import requests
import os
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = os.getenv("SMTP_PORT")
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL")

WEBAPI_KEY = os.getenv("WEBAPI_KEY")

MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")

def send_otp_email_via_snmp(to_email: str, otp_code: str):

    logger.info(f'Sending OTP email to {to_email}')

    subject = "Your ruckus.tools OTP Code"
    body = f"""\
Hello,

Your One-Time Password (OTP) is:

**{otp_code}**

It will expire in 15 minutes. Please enter it to complete your login.

Thanks,
The Ruckus Tools Team
"""

    # Create a MIMEText object
    message = MIMEText(body, "plain")
    message["Subject"] = subject
    message["From"] = FROM_EMAIL
    message["To"] = to_email

    # Connect to SMTP server
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(FROM_EMAIL, [to_email], message.as_string())


def send_otp_email_via_api(to_email: str, otp_code: str):
    logger.info(f'Sending OTP email to {to_email} via Mailgun API')

    url = f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages"

    data = {
        "from": FROM_EMAIL,
        "to": to_email,
        "subject": f"{otp_code} ruckus.tools OTP Code",
        "text": f"Your OTP is: {otp_code}\n\nThis code will expire in 15 minutes.\n\nhttps://ruckus.tools",
        "html": f"Your OTP is: <strong>{otp_code}</strong><br><br>This code will expire in 15 minutes.<br><br><a href='https://ruckus.tools'>https://ruckus.tools</a>",
    }

    try:
        response = requests.post(
            url,
            auth=("api", MAILGUN_API_KEY),
            data=data
        )
        if response.status_code != 200:
            raise Exception(f"Mailgun error: {response.status_code} {response.text}")
        logger.info("Email sent via Mailgun")
    except Exception as e:
        logger.error(f"Mailgun failed: {e}")
        try:
            send_otp_email_via_snmp(to_email, otp_code)
        except Exception as smtp_e:
            logger.error(f"SMTP fallback failed too: {smtp_e}")


from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def SENDGRID_send_otp_email_via_api(to_email: str, otp_code: str):

    logger.info(f'Sending OTP email to {to_email} via SendGrid')

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject="Your ruckus.tools OTP Code",
        html_content=f'<strong>{otp_code}</strong>')
    try:
        sg = SendGridAPIClient(os.environ.get('WEBAPI_KEY'))
        response = sg.send(message)
        logger.debug(f"SendGrid response: {response.status_code}")
    except Exception as e:
        logger.error(f"SendGrid error: {str(e)}")
        try:
            send_otp_email_via_snmp(to_email, otp_code)
        except Exception as smtp_e:
            logger.error(f"SMTP fallback failed too: {smtp_e}")
