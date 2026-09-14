import re
import socket
import smtplib
import logging
import dns.resolver
from typing import Tuple

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

DUMMY_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "10minutemail.com",
    "trashmail.com", "yopmail.com", "dispostable.com", "getnada.com",
    "sharklasers.com", "temp-mail.org", "fakeinbox.com", "example.com",
    "domain.com", "test.com", "sentry.io", "wixpress.com", "schema.org",
    "company.com", "company.ie", "email.com", "domain.co"
}

DUMMY_PREFIXES = (
    "you@", "your@", "user@", "name@", "someone@", "email@", "test@",
    "example@", "slick-carousel@", "drupal-bootstrap@", "bootstrap@",
    "entreprise7pro@", "company@"
)

class EmailVerifier:
    @staticmethod
    def is_valid_syntax(email: str) -> bool:
        if not email or not isinstance(email, str):
            return False
        email = email.strip().lower()
        if any(email.startswith(p) for p in DUMMY_PREFIXES):
            return False
        if len(email) > 254:
            return False
        if not EMAIL_REGEX.match(email):
            return False
        # Filter out common false positives from scraped web assets
        if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".webp", ".pdf")):
            return False
        return True

    @staticmethod
    def is_disposable_or_dummy(email: str) -> bool:
        try:
            domain = email.split("@")[1].lower()
            return domain in DUMMY_DOMAINS
        except IndexError:
            return True

    @staticmethod
    def check_mx_records(domain: str) -> bool:
        """Verifies if the domain has valid Mail Exchange (MX) DNS records."""
        try:
            answers = dns.resolver.resolve(domain, 'MX', lifetime=5.0)
            return len(answers) > 0
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.exception.Timeout):
            logger.warning(f"No MX records found for domain: {domain}")
            return False
        except Exception as e:
            logger.debug(f"DNS MX lookup error for {domain}: {e}")
            return False

    @staticmethod
    def verify_smtp_handshake(email: str, sender_domain: str = "outreach.bloobeach.com") -> Tuple[bool, str]:
        """
        Attempts SMTP handshake (RCPT TO) check against the MX server.
        Returns (is_valid, reason_str).
        """
        try:
            domain = email.split("@")[1].lower()
            mx_records = dns.resolver.resolve(domain, 'MX', lifetime=5.0)
            if not mx_records:
                return False, "No MX records"
            
            # Sort by priority
            sorted_mx = sorted(mx_records, key=lambda r: r.preference)
            primary_mx = str(sorted_mx[0].exchange).rstrip('.')

            # Attempt socket connection on port 25 with 5s timeout
            server = smtplib.SMTP(timeout=5)
            server.connect(primary_mx, 25)
            server.helo(sender_domain)
            server.mail(f"verify@{sender_domain}")
            code, resp = server.rcpt(email)
            server.quit()

            if code == 250:
                return True, "SMTP 250 OK"
            elif code in (550, 551, 552, 553, 554):
                return False, f"SMTP Mailbox rejected ({code})"
            else:
                # Catch-all / ambiguous SMTP server response
                return True, f"SMTP unverified response ({code})"

        except (socket.error, smtplib.SMTPException, TimeoutError) as e:
            # Port 25 is frequently blocked by local ISPs/Windows firewalls.
            # Fall back to DNS MX verification.
            logger.debug(f"SMTP port 25 blocked or timeout for {email} ({e}). Falling back to DNS MX validation.")
            return True, "MX Validated (SMTP Port 25 blocked)"
        except Exception as e:
            return False, f"Verification failed: {str(e)}"

    @classmethod
    def verify_email(cls, email: str) -> Tuple[bool, str]:
        """
        Full multi-stage verification pipeline.
        Returns (is_valid, reason).
        """
        if not email:
            return False, "Email is empty"

        clean_email = email.strip().lower().rstrip('.')

        # Step 1: Syntax check
        if not cls.is_valid_syntax(clean_email):
            return False, "Invalid email syntax"

        # Step 2: Disposable domain check
        if cls.is_disposable_or_dummy(clean_email):
            return False, "Disposable or dummy email domain"

        domain = clean_email.split("@")[1]

        # Step 3: DNS MX Record check
        if not cls.check_mx_records(domain):
            return False, f"Domain {domain} has no MX records"

        # Step 4: SMTP Handshake ping (with fallback)
        smtp_ok, reason = cls.verify_smtp_handshake(clean_email)
        if not smtp_ok:
            return False, f"SMTP rejection: {reason}"

        return True, f"Verified ({reason})"

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_emails = [
        "support@google.com",
        "invalid_fake_user_12345@gmail.com",
        "fake@nonexistentdomain123456789.com",
        "test@mailinator.com",
        "bad-syntax-email"
    ]
    print("\n--- Testing Email Verifier ---")
    for mail in test_emails:
        valid, reason = EmailVerifier.verify_email(mail)
        print(f"Email: {mail:<40} | Valid: {str(valid):<5} | Reason: {reason}")
