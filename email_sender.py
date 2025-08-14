import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from datetime import datetime
from dotenv import load_dotenv
# Load environment variables
load_dotenv()


def send_email(to_email, subject, body):
    """Send email notification to a single recipient"""
    try:
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        sender_email = os.getenv('FROM_EMAIL')
        sender_password = os.getenv('EMAIL_PASSWORD')

        if not sender_email or not sender_password:
            print("Email credentials not configured")
            return False

        # Create message
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = to_email
        message["Subject"] = subject

        # Add body
        message.attach(MIMEText(body, "plain"))

        # SMTP session
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, message.as_string())

        print(f"Email sent successfully to {to_email}")
        return True

    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False
    

# send_email(
#     to_email="immohdtalal@gmail.com",
#     subject="Test Email",
#     body="This is a test email."
# )