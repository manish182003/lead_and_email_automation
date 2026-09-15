import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Database Path
DB_DIR = BASE_DIR / "data"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "leads.db"

# Logs Path
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / "pipeline.log"

# CSV Import Directory
IMPORT_CSV_PATH = DB_DIR / "import_leads.csv"

# Service & Portfolio Credentials
COMPANY_NAME = "Bloo Beach Softwares LLC"
COMPANY_ADDRESS = "1309 Coffeen Avenue STE 1200, Sheridan, WY 82801, USA"
PORTFOLIO_URL = os.getenv("PORTFOLIO_WEBSITE", "https://bloobeach.com")
SECONDARY_PORTFOLIO = "https://manishjoshi.online"
SENDER_NAME = os.getenv("SENDER_NAME", "Jefferson Geerman")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "hello@outreach.bloobeach.com")

OFFICIAL_SIGNATURE = (
    f"Jefferson Geerman\n"
    f"Bloo Beach Softwares LLC | 1309 Coffeen Avenue STE 1200, Sheridan, WY 82801, USA\n"
    f"Website: https://bloobeach.com | Portfolio: https://manishjoshi.online"
)

# Target Services Description for Groq Personalization
SERVICES_SUMMARY = """
Jefferson Geerman from Bloo Beach Softwares LLC (1309 Coffeen Avenue STE 1200, Sheridan, WY 82801, USA | bloobeach.com & manishjoshi.online).
- Core Offerings: Workorder & dispatch systems, mobile workflow apps for field techs, custom AI process automation, and Flutter apps.
"""

# Configurable Search Queries tailored for Global Small & Medium Size Businesses (US, UK, Canada, Australia)
SEARCH_QUERIES = [
    "field service contractors workorder dispatch USA",
    "HVAC plumbing contractors mobile app Texas California Florida",
    "logistics freight brokers process automation US Canada",
    "regional healthcare clinics patient intake software USA UK",
    "boutique e-commerce brands mobile app US Australia",
    "construction contractors field worker app USA Canada",
    "local service business dispatch automation UK US",
    "b2b software companies workflow automation USA"
]

# List of mega enterprise brands to strictly exclude from outreach
EXCLUDED_ENTERPRISE_KEYWORDS = [
    "walmart", "target", "homedepot", "bestbuy", "verizon", "amazon",
    "google", "microsoft", "apple", "facebook", "linkedin", "wikipedia",
    "costco", "lowes", "cvs", "walgreens", "starbucks", "mcdonalds"
]

# Deliverability & Sending Guardrails
DAILY_SEND_CAP = 20
MIN_SEND_DELAY_SEC = 60
MAX_SEND_DELAY_SEC = 180

# Business Sending Hours (24-hour format IST)
# 8:00 AM IST to 11:59 PM IST covers UK, European, and US Business Hours (8:30 AM EST to 2:30 PM EST)
BUSINESS_HOURS_START = 8   # 8:00 AM IST
BUSINESS_HOURS_END = 24    # 12:00 AM IST (23:59:59 IST)
BUSINESS_TIMEZONE = "Asia/Kolkata"

# Follow-Up Intervals (Days)
FOLLOWUP_1_DELAY_DAYS = 4
FOLLOWUP_2_DELAY_DAYS = 8
MAX_FOLLOWUPS = 2

# Email Footer Compliance
FOOTER_UNSUBSCRIBE = "Reply STOP if you'd rather not hear from me again."

# LLM Configuration (Groq)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "groq/compound-mini"
GROQ_FALLBACK_MODEL = "openai/gpt-oss-120b"

def setup_logging(name: str = None):
    """Sets up unified logging to both logs/pipeline.log file and stdout console."""
    import sys
    from logging.handlers import RotatingFileHandler
    
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        
        # File handler
        fh = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
        fh.setFormatter(formatter)
        root_logger.addHandler(fh)
        
        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        root_logger.addHandler(ch)
        
    return logging.getLogger(name) if name else root_logger

# Auto-initialize root logger
setup_logging()

# SerpAPI / Google Places API Credentials
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")

# HTTP Email API Relays (Bypasses Cloud Platform SMTP Port 25/465/587 blocks)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")

# SMTP Email Credentials
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.hostinger.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "hello@outreach.bloobeach.com")
SMTP_PASS = os.getenv("SMTP_PASS", "")

# Meeting Call-To-Action Option
# Default is soft conversational CTA for maximum inbox placement
OPTIONAL_CALENDLY_LINK = os.getenv("CALENDLY_LINK", "")
