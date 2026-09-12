import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from config import (
    FOLLOWUP_1_DELAY_DAYS, FOLLOWUP_2_DELAY_DAYS,
    MAX_FOLLOWUPS, FOOTER_UNSUBSCRIBE
)
from database import Database
from agents.sender_agent import SenderAgent

logger = logging.getLogger(__name__)

class FollowUpAgent:
    def __init__(self, db: Database = None, sender: SenderAgent = None):
        self.db = db or Database()
        self.sender = sender or SenderAgent(db=self.db)

    def process_followups(self) -> Dict[str, int]:
        logger.info("=== Running Follow-Up Agent ===")
        
        # Scan inbox for opt-outs first
        self.sender.check_inbox_for_optouts()

        now = datetime.now()
        f1_count = 0
        f2_count = 0
        closed_count = 0

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Process Follow-up 1 (status = 'sent', sent_at >= 4 days ago)
            cursor.execute("SELECT * FROM leads WHERE status = 'sent' ORDER BY id ASC LIMIT 100")
            sent_leads = [dict(r) for r in cursor.fetchall()]

            for lead in sent_leads:
                sent_at_str = lead.get("sent_at")
                recipient = lead.get("contact_email")
                if not sent_at_str or not recipient:
                    continue

                sent_at = datetime.fromisoformat(sent_at_str)
                if (now - sent_at) >= timedelta(days=FOLLOWUP_1_DELAY_DAYS):
                    if self.db.is_suppressed(recipient):
                        self.db.update_status(lead["id"], "opted_out")
                        continue

                    business_name = lead["business_name"]
                    subject = f"Re: {lead.get('email_subject', 'AI / Mobile App Automation')}"
                    body = (
                        f"Hi team at {business_name},\n\n"
                        f"Just floating this back up — happy to skip the call and just answer any questions over email if easier.\n\n"
                        f"Are you currently open to exploring AI automation or mobile app solutions for your team?\n\n"
                        f"Best,\nJefferson Geerman\nbloobeach.com | manishjoshi.online"
                    )

                    logger.info(f"Sending Follow-Up 1 to {recipient} ({business_name})...")
                    if self.sender.send_email_smtp(recipient, subject, body):
                        self.db.record_followup(lead["id"], 1, "followed_up_1")
                        f1_count += 1
                        time.sleep(30.0)  # Safe delay between follow-ups

            # 2. Process Follow-up 2 (status = 'followed_up_1', last_followup_at >= 4 days ago)
            cursor.execute("SELECT * FROM leads WHERE status = 'followed_up_1' ORDER BY id ASC LIMIT 100")
            f1_leads = [dict(r) for r in cursor.fetchall()]

            for lead in f1_leads:
                last_f_str = lead.get("last_followup_at") or lead.get("sent_at")
                recipient = lead.get("contact_email")
                if not last_f_str or not recipient:
                    continue

                last_f = datetime.fromisoformat(last_f_str)
                if (now - last_f) >= timedelta(days=4):  # 4 days after F1
                    if self.db.is_suppressed(recipient):
                        self.db.update_status(lead["id"], "opted_out")
                        continue

                    business_name = lead["business_name"]
                    subject = f"Re: {lead.get('email_subject', 'AI / Mobile App Automation')}"
                    body = (
                        f"Hi team at {business_name},\n\n"
                        f"One final check on this — let me know if custom AI automation or mobile app development is on your roadmap this quarter.\n\n"
                        f"Either way, wish you all the best with your projects!\n\n"
                        f"Best,\nJefferson Geerman\nbloobeach.com | manishjoshi.online"
                    )

                    logger.info(f"Sending Final Follow-Up 2 to {recipient} ({business_name})...")
                    if self.sender.send_email_smtp(recipient, subject, body):
                        self.db.record_followup(lead["id"], 2, "followed_up_2")
                        f2_count += 1
                        time.sleep(30.0)

            # 3. Close leads after Follow-up 2 with 4 days no response
            cursor.execute("SELECT * FROM leads WHERE status = 'followed_up_2' ORDER BY id ASC LIMIT 100")
            f2_leads = [dict(r) for r in cursor.fetchall()]

            for lead in f2_leads:
                last_f_str = lead.get("last_followup_at")
                if not last_f_str:
                    continue

                last_f = datetime.fromisoformat(last_f_str)
                if (now - last_f) >= timedelta(days=4):
                    self.db.update_status(lead["id"], "closed_no_response")
                    closed_count += 1

        total_processed = f1_count + f2_count + closed_count
        logger.info(f"Follow-Up Agent Finished: F1 Sent={f1_count}, F2 Sent={f2_count}, Closed={closed_count}")
        self.db.log_run("FollowUpAgent", total_processed, f1_count + f2_count, 0, 
                        f"Sent {f1_count} F1 and {f2_count} F2 emails. Closed {closed_count} non-responsive leads.")
        return {"f1_sent": f1_count, "f2_sent": f2_count, "closed": closed_count}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = FollowUpAgent()
    stats = agent.process_followups()
    print("Follow-Up Agent Stats:", stats)
