import os
import sys
from pathlib import Path
from datetime import date
from database import Database
from config import DAILY_SEND_CAP, LOG_FILE

def print_status_report():
    db = Database()
    total_leads = db.get_total_leads_count()
    status_counts = db.get_status_counts()
    sends_today = db.get_sends_today_count()

    print("=" * 65)
    print("       MANISHJOSHI.ONLINE LEAD OUTREACH SYSTEM DASHBOARD       ")
    print("=" * 65)
    print(f" Total Leads Collected : {total_leads}")
    print(f" Emails Sent Today     : {sends_today} / {DAILY_SEND_CAP} (Daily Cap)")
    print("-" * 65)
    print(" LEAD BREAKDOWN BY STATUS:")
    
    all_statuses = [
        "new", "enriching", "enriched", "no_email", "invalid_email", "duplicate_email",
        "enrich_failed", "drafted", "draft_failed", "sent", "send_failed",
        "followed_up_1", "followed_up_2", "closed_no_response", "replied", "opted_out"
    ]

    for st in all_statuses:
        count = status_counts.get(st, 0)
        pct = (count / total_leads * 100) if total_leads > 0 else 0.0
        print(f"   - {st:<20} : {count:>4} ({pct:>5.1f}%)")

    print("-" * 65)
    with db.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM suppression_list")
        sup_count = c.fetchone()[0]
        
        c.execute("SELECT * FROM run_logs ORDER BY id DESC LIMIT 5")
        recent_db_logs = [dict(r) for r in c.fetchall()]

    print(f" Suppressed / Opted-Out Emails: {sup_count}")
    print("-" * 65)
    print(" COMPLETED AGENT BATCH RUNS (DB LOGS):")
    if recent_db_logs:
        for log in recent_db_logs:
            print(f"   [{log['timestamp'][:19]}] {log['agent_name']:<16} | Processed: {log['leads_processed']:>3} | Success: {log['success_count']:>3} | Errors: {log['error_count']:>2}")
    else:
        print("   No completed batch logs recorded yet.")

    print("-" * 65)
    print(" LIVE LOG TAIL (ACTIVE PIPELINE ACTIVITY):")
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                last_lines = [line.strip() for line in lines[-6:] if line.strip()]
                for l in last_lines:
                    print(f"   {l[:100]}")
        except Exception as e:
            print(f"   Could not read log file: {e}")
    else:
        print("   Log file not generated yet.")
    print("=" * 65)

if __name__ == "__main__":
    print_status_report()
