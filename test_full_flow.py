import sys
import os
import time
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from database import Database
from agents.scraper_agent import ScraperAgent
from agents.enrichment_agent import EnrichmentAgent
from agents.drafting_agent import DraftingAgent
from agents.sender_agent import SenderAgent
from status_report import print_status_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("TestFullFlow")

def run_test():
    print("=" * 60)
    print("   STARTING TEST RUN: 3 FRESH VERIFIED COLD EMAILS   ")
    print("=" * 60)

    db = Database()

    # Step 1: Scrape Fresh Business Leads
    print("\n--- STEP 1: SCRAPING LEADS (Google Places + SerpAPI + Stealth) ---")
    scraper = ScraperAgent(db=db)
    scrape_stats = scraper.run()
    print("Scrape Summary:", scrape_stats)

    # Step 2: Enrich & Verify Deliverability (No Pattern Guessing)
    print("\n--- STEP 2: ENRICHING & VERIFYING EMAILS ---")
    enricher = EnrichmentAgent(db=db)
    enrich_stats = enricher.run(limit=25)
    print("Enrichment Summary:", enrich_stats)

    # Step 3: Select 3 Enriched Leads for Personalized Drafting
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM leads WHERE status = 'enriched' AND email_verified = 1 LIMIT 3")
        verified_leads = [dict(r) for r in cursor.fetchall()]

    print(f"\nSelected {len(verified_leads)} Verified Leads for Outreach:")
    for l in verified_leads:
        print(f"  - Lead ID {l['id']}: {l['business_name']} | Email: {l['contact_email']} (VERIFIED EXPLICIT)")

    if not verified_leads:
        print("No new verified leads found in current batch. Enriching more...")
        enricher.run(limit=30)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM leads WHERE status = 'enriched' AND email_verified = 1 LIMIT 3")
            verified_leads = [dict(r) for r in cursor.fetchall()]

    if not verified_leads:
        print("ERROR: No deliverable scraped emails found in database.")
        return

    # Step 4: Draft Personalized Emails using Groq LLM API
    print("\n--- STEP 4: DRAFTING PERSONALIZED COLD EMAILS WITH GROQ LLM ---")
    drafter = DraftingAgent(db=db)
    for lead in verified_leads:
        success, subject, body, status_msg = drafter.draft_email_for_lead(lead)
        if success and subject and body:
            db.update_lead_draft(lead["id"], subject, body)
            print(f"\n[DRAFT CREATED for {lead['business_name']} ({lead['contact_email']})]")
            print(f"SUBJECT: {subject}")
            print(f"BODY:\n{body}")
            print("-" * 50)

    # Step 5: Send 3 Cold Emails via Hostinger SMTP & Sync to IMAP Sent Folder
    print("\n--- STEP 5: SENDING COLD EMAILS & SYNCING TO WEBMAIL SENT FOLDER ---")
    sender = SenderAgent(db=db)
    drafted_to_send = db.get_leads_by_status("drafted", limit=3)
    
    sent_count = 0
    for lead in drafted_to_send:
        recipient = lead["contact_email"]
        subject = lead["email_subject"]
        body = lead["email_body"]
        
        print(f"\nSending email #{sent_count+1} to {recipient} ({lead['business_name']})...")
        if sender.send_email_smtp(recipient, subject, body):
            db.record_send(lead["id"])
            sent_count += 1
            print(f"-> SENT SUCCESSFUL & SYNCED TO HOSTINGER WEBMAIL SENT TAB: {recipient}")
            if sent_count < len(drafted_to_send):
                print("Waiting 10 seconds before next send...")
                time.sleep(10)
        else:
            print(f"-> SEND FAILED for {recipient}")

    print(f"\nSuccessfully sent {sent_count} cold emails!")
    
    # Step 6: Final Dashboard Summary
    print("\n--- STEP 6: SYSTEM DASHBOARD ---")
    print_status_report()

if __name__ == "__main__":
    run_test()
