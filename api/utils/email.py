import smtplib
from email.mime.text import MIMEText

import os
from dotenv import load_dotenv
load_dotenv() 

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = os.getenv("SMTP_PORT")
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL")

WEBAPI_KEY = os.getenv("WEBAPI_KEY")

def send_otp_email_via_snmp(to_email: str, otp_code: str):

    print(f'Emailing {to_email} with OTP {otp_code}')

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



import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def send_otp_email_via_api(to_email: str, otp_code: str):

    print(f'Emailing {to_email} with OTP {otp_code}')

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject="Your ruckus.tools OTP Code",
        html_content=f'<strong>{otp_code}</strong>')
    try:
        sg = SendGridAPIClient(os.environ.get('WEBAPI_KEY'))
        response = sg.send(message)
        print(response.status_code)
        print(response.body)
        print(response.headers)
    except Exception as e:
        print(e.message)