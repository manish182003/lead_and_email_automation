import sqlite3
import logging
from database import Database
from verifier import DUMMY_DOMAINS, DUMMY_PREFIXES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CleanupDB")

def cleanup():
    db = Database()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Delete placeholder/dummy email rows
        cursor.execute("SELECT id, contact_email FROM leads WHERE contact_email IS NOT NULL")
        rows = cursor.fetchall()
        deleted_count = 0
        for r in rows:
            lead_id = r["id"]
            email = (r["contact_email"] or "").strip().lower()
            if any(email.startswith(p) for p in DUMMY_PREFIXES) or any(d in email for d in DUMMY_DOMAINS):
                cursor.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
                deleted_count += 1
                logger.info(f"Deleted dummy email lead ID {lead_id}: {email}")

        # 2. Delete blog post / directory article titles
        cursor.execute("SELECT id, business_name FROM leads")
        name_rows = cursor.fetchall()
        deleted_blogs = 0
        for r in name_rows:
            lead_id = r["id"]
            cleaned = db.clean_business_name(r["business_name"])
            if not cleaned:
                cursor.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
                deleted_blogs += 1
                logger.info(f"Deleted blog article title lead ID {lead_id}: {r['business_name']}")
            elif cleaned != r["business_name"]:
                cursor.execute("UPDATE leads SET business_name = ? WHERE id = ?", (cleaned, lead_id))
                logger.info(f"Cleaned business name ID {lead_id}: '{r['business_name']}' -> '{cleaned}'")

        # 3. Reset un-sent drafted leads so fresh high-converting emails are drafted
        cursor.execute("UPDATE leads SET status = 'enriched' WHERE status = 'drafted'")

        conn.commit()
        print("=" * 60)
        print(f"CLEANUP COMPLETE: Deleted {deleted_count} dummy emails and {deleted_blogs} blog title leads.")
        print("=" * 60)

if __name__ == "__main__":
    cleanup()
