import sqlite3
import logging
from datetime import datetime, date
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any
from config import DB_PATH

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = str(db_path)
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA cache_size = -64000;")
        except Exception as e:
            logger.debug(f"Failed setting SQLite PRAGMAs: {e}")
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Leads Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_name TEXT NOT NULL,
                    website TEXT,
                    domain TEXT UNIQUE,
                    category TEXT,
                    address TEXT,
                    phone TEXT,
                    google_place_id TEXT,
                    contact_email TEXT,
                    email_verified BOOLEAN DEFAULT 0,
                    source TEXT,
                    summary TEXT,
                    pain_point TEXT,
                    email_subject TEXT,
                    email_body TEXT,
                    status TEXT DEFAULT 'new',
                    error_message TEXT,
                    sent_at TIMESTAMP,
                    last_followup_at TIMESTAMP,
                    followup_count INTEGER DEFAULT 0,
                    date_found TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Suppression List Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS suppression_list (
                    email TEXT PRIMARY KEY,
                    reason TEXT DEFAULT 'opt_out',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Agent Run Logs Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    leads_processed INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Indexes for high performance status and duplicate filtering
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_sent_at ON leads(sent_at);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_contact_email ON leads(contact_email);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_business_name ON leads(business_name);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_suppression_email ON suppression_list(email);")

            conn.commit()
            logger.debug("Database schema initialized successfully.")

    @staticmethod
    def extract_domain(url_or_domain: str) -> str:
        if not url_or_domain:
            return ""
        url = url_or_domain.strip().lower()
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        try:
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path
            if domain.startswith("www."):
                domain = domain[4:]
            return domain.split(":")[0]  # Remove port if present
        except Exception:
            return url_or_domain.lower()

    @staticmethod
    def normalize_company_name(raw_name: str) -> str:
        """Strips legal entity suffixes and non-alphanumeric punctuation for robust duplicate comparison."""
        if not raw_name:
            return ""
        name = raw_name.lower().strip()
        legal_suffixes = [
            r"\bllc\b", r"\bl\.l\.c\.\b", r"\binc\b", r"\binc\.\b", r"\bcorp\b",
            r"\bcorporation\b", r"\bltd\b", r"\bltd\.\b", r"\blimited\b", r"\bco\b",
            r"\bco\.\b", r"\bcompany\b", r"\bpvt\b", r"\bpvt\.\b", r"\bprivate\b"
        ]
        import re
        for suff in legal_suffixes:
            name = re.sub(suff, "", name, flags=re.IGNORECASE)
        name = re.sub(r"[^\w\s]", "", name)
        return " ".join(name.split())

    @staticmethod
    def clean_business_name(raw_name: str) -> Optional[str]:
        if not raw_name:
            return None
        name = raw_name.strip()
        lower_name = name.lower()

        # Reject article/listicle blog titles and mega enterprises
        blog_triggers = [
            "top 10", "top 20", "top 25", "top 50", "top 100", "best 10", "best 20", "best 25",
            "11 top", "10 top", "15 top", "20 top", "list of", "startups transforming",
            "startups to watch", "funded by", "ranking of", "review of", " vs ", "guide to",
            "healthcare it startups", "ai healthcare startups", "best ai tools"
        ]
        from config import EXCLUDED_ENTERPRISE_KEYWORDS
        if any(trig in lower_name for trig in blog_triggers) or any(ent in lower_name for ent in EXCLUDED_ENTERPRISE_KEYWORDS):
            return None

        # Clean generic SEO suffixes (e.g. "Software Orca - App Development" -> "Software Orca")
        if " - " in name:
            name = name.split(" - ")[0].strip()
        if " | " in name:
            name = name.split(" | ")[0].strip()

        if len(name) > 45:
            name = name[:45].strip()

        return name if len(name) >= 2 else None

    def is_email_used(self, email: str, exclude_lead_id: Optional[int] = None) -> bool:
        """Checks if contact email is already used by an active lead or present in suppression list."""
        if not email:
            return False
        email_clean = email.strip().lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM suppression_list WHERE LOWER(email) = ?", (email_clean,))
            if cursor.fetchone():
                return True

            if exclude_lead_id:
                cursor.execute("""
                    SELECT 1 FROM leads 
                    WHERE LOWER(contact_email) = ? AND id != ? 
                    AND status NOT IN ('enrich_failed', 'no_email', 'invalid_email')
                """, (email_clean, exclude_lead_id))
            else:
                cursor.execute("""
                    SELECT 1 FROM leads 
                    WHERE LOWER(contact_email) = ? 
                    AND status NOT IN ('enrich_failed', 'no_email', 'invalid_email')
                """, (email_clean,))
            return cursor.fetchone() is not None

    def insert_lead(self, lead: Dict[str, Any]) -> bool:
        domain_raw = self.extract_domain(lead.get("website", "")) or lead.get("domain", "")
        domain = domain_raw.strip().lower() if domain_raw else None
        
        business_name = self.clean_business_name(lead.get("business_name", ""))
        if not business_name:
            return False

        normalized_name = self.normalize_company_name(business_name)

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check domain deduplication
                if domain:
                    cursor.execute("SELECT id FROM leads WHERE domain = ?", (domain,))
                    if cursor.fetchone():
                        logger.debug(f"Skipping duplicate domain: {domain}")
                        return False
                
                # Check exact name deduplication
                cursor.execute("SELECT id FROM leads WHERE LOWER(business_name) = ?", (business_name.lower(),))
                if cursor.fetchone():
                    logger.debug(f"Skipping duplicate business name: {business_name}")
                    return False

                # Check normalized company name deduplication
                if normalized_name:
                    cursor.execute("SELECT business_name FROM leads WHERE business_name IS NOT NULL")
                    for row in cursor.fetchall():
                        if self.normalize_company_name(row["business_name"]) == normalized_name:
                            logger.debug(f"Skipping duplicate normalized business name: '{business_name}' (matches '{row['business_name']}')")
                            return False

                now = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO leads (
                        business_name, website, domain, category, address, phone,
                        google_place_id, source, date_found, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
                """, (
                    business_name,
                    lead.get("website", ""),
                    domain,
                    lead.get("category", ""),
                    lead.get("address", ""),
                    lead.get("phone", ""),
                    lead.get("google_place_id", ""),
                    lead.get("source", "unknown"),
                    lead.get("date_found", now),
                    now,
                    now
                ))
                conn.commit()
                return True
        except sqlite3.IntegrityError as e:
            logger.debug(f"Skipping lead due to DB constraint: {e}")
            return False

    def get_leads_by_status(self, status: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM leads WHERE status = ? ORDER BY id ASC LIMIT ?", (status, limit))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def update_lead_enrichment(self, lead_id: int, email: Optional[str], is_verified: bool, 
                               summary: str, pain_point: str, status: str, error: Optional[str] = None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE leads SET
                    contact_email = ?,
                    email_verified = ?,
                    summary = ?,
                    pain_point = ?,
                    status = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
            """, (email, 1 if is_verified else 0, summary, pain_point, status, error, now, lead_id))
            conn.commit()

    def update_lead_draft(self, lead_id: int, subject: str, body: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE leads SET
                    email_subject = ?,
                    email_body = ?,
                    status = 'drafted',
                    updated_at = ?
                WHERE id = ?
            """, (subject, body, now, lead_id))
            conn.commit()

    def record_send(self, lead_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE leads SET
                    status = 'sent',
                    sent_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (now, now, lead_id))
            conn.commit()

    def record_followup(self, lead_id: int, followup_num: int, next_status: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE leads SET
                    status = ?,
                    last_followup_at = ?,
                    followup_count = ?,
                    updated_at = ?
                WHERE id = ?
            """, (next_status, now, followup_num, now, lead_id))
            conn.commit()

    def update_status(self, lead_id: int, status: str, error: Optional[str] = None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute("UPDATE leads SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                           (status, error, now, lead_id))
            conn.commit()

    def is_suppressed(self, email: str) -> bool:
        if not email:
            return True
        email_clean = email.strip().lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM suppression_list WHERE LOWER(email) = ?", (email_clean,))
            return cursor.fetchone() is not None

    def add_to_suppression(self, email: str, reason: str = "opt_out"):
        if not email:
            return
        email_clean = email.strip().lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO suppression_list (email, reason) VALUES (?, ?)", (email_clean, reason))
            # Also mark lead status if exists
            cursor.execute("UPDATE leads SET status = 'opted_out' WHERE LOWER(contact_email) = ?", (email_clean,))
            conn.commit()

    def get_sends_today_count(self) -> int:
        today_str = date.today().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM leads WHERE status IN ('sent', 'followed_up_1', 'followed_up_2') AND DATE(sent_at) = ?", (today_str,))
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_status_counts(self) -> Dict[str, int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) as count FROM leads GROUP BY status")
            rows = cursor.fetchall()
            return {row["status"]: row["count"] for row in rows}

    def get_total_leads_count(self) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM leads")
            row = cursor.fetchone()
            return row[0] if row else 0

    def log_run(self, agent_name: str, processed: int, success: int, errors: int, details: str = ""):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO run_logs (agent_name, leads_processed, success_count, error_count, details)
                VALUES (?, ?, ?, ?, ?)
            """, (agent_name, processed, success, errors, details))
            conn.commit()

if __name__ == "__main__":
    db = Database()
    print("Database test completed successfully. Total leads:", db.get_total_leads_count())
