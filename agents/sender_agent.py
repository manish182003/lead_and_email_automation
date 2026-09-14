from typing import Dict
import os
import sys
import time
import imaplib
import email
import random
import logging
import smtplib
import socket
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pytz

# Force IPv4 socket resolution to prevent IPv6 [Errno 101] Network is unreachable on Cloud platforms (Render/Docker)
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = _getaddrinfo_ipv4_only

import requests
from config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
    SENDER_NAME, SENDER_EMAIL, DAILY_SEND_CAP,
    MIN_SEND_DELAY_SEC, MAX_SEND_DELAY_SEC,
    BUSINESS_HOURS_START, BUSINESS_HOURS_END,
    BUSINESS_TIMEZONE, FOOTER_UNSUBSCRIBE,
    RESEND_API_KEY, BREVO_API_KEY
)
from database import Database

logger = logging.getLogger(__name__)

class SenderAgent:
    def __init__(self, db: Database = None):
        self.db = db or Database()

    def is_within_business_hours(self) -> bool:
        """Checks if current time is within configured business hours (e.g. 9 AM - 6 PM IST)."""
        try:
            tz = pytz.timezone(BUSINESS_TIMEZONE)
            now = datetime.now(tz)
            current_hour = now.hour
            is_weekday = now.weekday() < 5  # Monday-Friday
            is_valid_hour = BUSINESS_HOURS_START <= current_hour < BUSINESS_HOURS_END
            return is_valid_hour and is_weekday
        except Exception as e:
            logger.warning(f"Timezone check failed: {e}. Defaulting to allowing send.")
            return True

    def _get_smtp_connection(self, timeout: int = 10):
        """
        Attempts SMTP connection with dual-port fallback (Port 587 STARTTLS first, then Port 465 SSL).
        Port 587 STARTTLS is immune to cloud firewall port 465 timeouts on platforms like Render.
        """
        # Try Port 587 STARTTLS first
        try:
            logger.info(f"Connecting to Hostinger SMTP {SMTP_HOST}:587 (STARTTLS)...")
            server = smtplib.SMTP(SMTP_HOST, 587, timeout=timeout)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            logger.info("Hostinger SMTP connected via Port 587 (STARTTLS)!")
            return server
        except Exception as err_587:
            logger.warning(f"SMTP Port 587 STARTTLS notice ({err_587}). Trying Port 465 (SSL)...")

        # Fallback to Port 465 SSL
        try:
            logger.info(f"Connecting to Hostinger SMTP {SMTP_HOST}:465 (SSL)...")
            server = smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=timeout)
            server.login(SMTP_USER, SMTP_PASS)
            logger.info("Hostinger SMTP connected via Port 465 (SSL)!")
            return server
        except Exception as err_465:
            logger.error(f"SMTP connection failed on both Port 587 and Port 465: {err_465}")
            raise err_465

    def test_smtp_connection(self) -> bool:
        """Verifies Hostinger SMTP authentication."""
        try:
            server = self._get_smtp_connection(timeout=10)
            server.quit()
            logger.info("Hostinger SMTP authentication successful!")
            return True
        except Exception as e:
            logger.error(f"Hostinger SMTP authentication failed: {e}")
            return False

    def send_email_smtp(self, recipient_email: str, subject: str, body_text: str) -> bool:
        """Sends email via Resend/Brevo HTTPS API (Port 443) or Hostinger SMTP (Ports 587/465)."""
        full_body = f"{body_text.strip()}\n\n---\n{FOOTER_UNSUBSCRIBE}"

        # Option 1: Send via Resend HTTPS API (Port 443 - immune to cloud platform SMTP port blocks)
        if RESEND_API_KEY:
            try:
                logger.info(f"Sending email to {recipient_email} via Resend HTTPS API (Port 443)...")
                res = requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "from": f"{SENDER_NAME} <{SENDER_EMAIL}>",
                        "to": [recipient_email],
                        "subject": subject,
                        "text": full_body
                    },
                    timeout=10
                )
                if res.status_code in (200, 201):
                    logger.info(f"Resend HTTPS API successfully delivered email to {recipient_email}!")
                    return True
                else:
                    logger.warning(f"Resend HTTPS API response error ({res.status_code}): {res.text}. Trying fallbacks...")
            except Exception as resend_err:
                logger.warning(f"Resend HTTPS API exception: {resend_err}. Trying fallbacks...")

        # Option 2: Send via Brevo HTTPS API (Port 443 - immune to cloud platform SMTP port blocks)
        if BREVO_API_KEY:
            try:
                logger.info(f"Sending email to {recipient_email} via Brevo HTTPS API (Port 443)...")
                res = requests.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                    json={
                        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
                        "to": [{"email": recipient_email}],
                        "subject": subject,
                        "textContent": full_body
                    },
                    timeout=10
                )
                if res.status_code in (200, 201):
                    logger.info(f"Brevo HTTPS API successfully delivered email to {recipient_email}!")
                    return True
                else:
                    logger.warning(f"Brevo HTTPS API response error ({res.status_code}): {res.text}. Trying fallbacks...")
            except Exception as brevo_err:
                logger.warning(f"Brevo HTTPS API exception: {brevo_err}. Trying fallbacks...")

        # Option 3: Send via Hostinger SMTP Sockets (Dual Port 587 STARTTLS -> Port 465 SSL)
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["To"] = recipient_email
        msg["Subject"] = subject
        msg.attach(MIMEText(full_body, "plain", "utf-8"))

        try:
            # Send via SMTP socket
            server = self._get_smtp_connection(timeout=10)
            server.sendmail(SENDER_EMAIL, [recipient_email], msg.as_string())
            server.quit()

            # 2. Sync to Hostinger Webmail IMAP 'Sent' folder
            try:
                imap_host = SMTP_HOST.replace("smtp.", "imap.")
                mail = imaplib.IMAP4_SSL(imap_host, 993, timeout=10)
                mail.login(SMTP_USER, SMTP_PASS)
                # Try standard Sent folder names
                for folder in ["Sent", "INBOX.Sent"]:
                    try:
                        res, _ = mail.select(folder)
                        if res == "OK":
                            mail.append(folder, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
                            break
                    except Exception:
                        continue
                mail.logout()
            except Exception as imap_err:
                logger.debug(f"Could not copy to IMAP Sent folder: {imap_err}")

            return True
        except Exception as e:
            logger.error(f"Failed to send email to {recipient_email}: {e}")
            return False

    def check_inbox_for_optouts(self):
        """Scans Hostinger IMAP inbox for STOP/unsubscribe replies."""
        try:
            imap_host = SMTP_HOST.replace("smtp.", "imap.")
            logger.info(f"Checking IMAP inbox ({imap_host}:993) for STOP replies...")
            mail = imaplib.IMAP4_SSL(imap_host, 993)
            mail.login(SMTP_USER, SMTP_PASS)
            mail.select("inbox")

            status, messages = mail.search(None, "UNSEEN")
            if status == "OK" and messages[0]:
                for num in messages[0].split():
                    res, data = mail.fetch(num, "(RFC822)")
                    for response_part in data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            sender = msg.get("From", "")
                            subject = msg.get("Subject", "")

                            # Extract sender address
                            sender_addr = sender.split("<")[-1].replace(">", "").strip().lower()
                            
                            # Read body
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                        break
                            else:
                                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                            content_upper = (subject + " " + body).upper()
                            if "STOP" in content_upper or "UNSUBSCRIBE" in content_upper:
                                logger.info(f"Auto Opt-Out detected from {sender_addr}. Adding to suppression list.")
                                self.db.add_to_suppression(sender_addr, reason="User requested STOP reply")

            mail.logout()
        except Exception as e:
            logger.debug(f"IMAP check error: {e}")

    def run(self, enforce_hours: bool = True) -> Dict[str, int]:
        logger.info("=== Running Sender Agent ===")

        # Step 1: Scan inbox for opt-outs
        self.check_inbox_for_optouts()

        # Step 2: Enforce business hours check
        if enforce_hours and not self.is_within_business_hours():
            logger.info("Current time is outside business hours. Sender Agent sleeping until next scheduled run.")
            return {"sent": 0, "skipped_hours": 1, "failed": 0}

        # Step 3: Check daily send cap
        sends_today = self.db.get_sends_today_count()
        if sends_today >= DAILY_SEND_CAP:
            logger.warning(f"Daily send cap reached ({sends_today}/{DAILY_SEND_CAP}). No more sends allowed today.")
            return {"sent": 0, "cap_reached": True, "failed": 0}

        remaining_cap = DAILY_SEND_CAP - sends_today
        drafted_leads = self.db.get_leads_by_status("drafted", limit=remaining_cap)

        if not drafted_leads:
            logger.info("No 'drafted' leads ready to send.")
            return {"sent": 0, "failed": 0}

        sent_count = 0
        failed_count = 0

        for lead in drafted_leads:
            lead_id = lead["id"]
            recipient = lead.get("contact_email", "").strip().rstrip('.')
            subject = lead.get("email_subject", "")
            body = lead.get("email_body", "")

            if not recipient:
                logger.warning(f"Lead ID {lead_id} missing contact email. Skipping.")
                self.db.update_status(lead_id, "send_failed", "Missing contact email")
                failed_count += 1
                continue

            # Check suppression list
            if self.db.is_suppressed(recipient):
                logger.info(f"Skipping suppressed email: {recipient}")
                self.db.update_status(lead_id, "opted_out", "Email in suppression list")
                continue

            logger.info(f"Sending email to {recipient} (Lead ID {lead_id})...")
            success = self.send_email_smtp(recipient, subject, body)

            if success:
                self.db.record_send(lead_id)
                sent_count += 1
                logger.info(f"Successfully sent email to {recipient}.")

                # Random jitter delay between sends (e.g. 60 to 180 sec)
                if sent_count < len(drafted_leads):
                    delay = random.randint(MIN_SEND_DELAY_SEC, MAX_SEND_DELAY_SEC)
                    logger.info(f"Waiting {delay} seconds before next send to protect domain reputation...")
                    time.sleep(delay)
            else:
                self.db.update_status(lead_id, "send_failed", "SMTP transmission failed")
                failed_count += 1

        logger.info(f"Sender Agent Finished: Sent={sent_count}, Failed={failed_count}")
        self.db.log_run("SenderAgent", len(drafted_leads), sent_count, failed_count, 
                        f"Sent {sent_count} emails via Hostinger SMTP.")
        return {"sent": sent_count, "failed": failed_count}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = SenderAgent()

    if "--test-connection" in sys.argv:
        print("Testing Hostinger SMTP Connection...")
        ok = agent.test_smtp_connection()
        print("SMTP Test Result:", "SUCCESS" if ok else "FAILED")
    else:
        stats = agent.run(enforce_hours=False)  # Manual test run
        print("Sender Stats:", stats)
