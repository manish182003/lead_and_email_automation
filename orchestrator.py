import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import LOG_FILE, LOGS_DIR
from database import Database
from agents.scraper_agent import ScraperAgent
from agents.enrichment_agent import EnrichmentAgent
from agents.drafting_agent import DraftingAgent
from agents.sender_agent import SenderAgent
from agents.followup_agent import FollowUpAgent

# Configure Rotating Log File + Console Logging
LOGS_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("Orchestrator")

def job_scraper():
    logger.info("Executing Scheduled Job: ScraperAgent")
    try:
        agent = ScraperAgent()
        stats = agent.run()
        logger.info(f"Scraper Job Complete: {stats}")
    except Exception as e:
        logger.error(f"Scraper Job Exception: {e}", exc_info=True)

def job_enrichment():
    logger.info("Executing Scheduled Job: EnrichmentAgent")
    try:
        agent = EnrichmentAgent()
        stats = agent.run()
        logger.info(f"Enrichment Job Complete: {stats}")
    except Exception as e:
        logger.error(f"Enrichment Job Exception: {e}", exc_info=True)

def job_drafting():
    logger.info("Executing Scheduled Job: DraftingAgent")
    try:
        agent = DraftingAgent()
        stats = agent.run()
        logger.info(f"Drafting Job Complete: {stats}")
    except Exception as e:
        logger.error(f"Drafting Job Exception: {e}", exc_info=True)

def job_sender():
    logger.info("Executing Scheduled Job: SenderAgent")
    try:
        agent = SenderAgent()
        stats = agent.run(enforce_hours=True)
        logger.info(f"Sender Job Complete: {stats}")
    except Exception as e:
        logger.error(f"Sender Job Exception: {e}", exc_info=True)

def job_followup():
    logger.info("Executing Scheduled Job: FollowUpAgent")
    try:
        agent = FollowUpAgent()
        stats = agent.process_followups()
        logger.info(f"Follow-Up Job Complete: {stats}")
    except Exception as e:
        logger.error(f"Follow-Up Job Exception: {e}", exc_info=True)

def run_full_pipeline_once():
    """Runs all agents sequentially in a single pass."""
    logger.info("==================================================")
    logger.info("Starting Full Single-Pass Pipeline Execution")
    logger.info("==================================================")
    
    db = Database()
    
    print("\n--- 1. Running Scraper Agent ---")
    job_scraper()
    
    print("\n--- 2. Running Enrichment Agent ---")
    job_enrichment()
    
    print("\n--- 3. Running Drafting Agent ---")
    job_drafting()
    
    print("\n--- 4. Running Follow-Up Agent ---")
    job_followup()
    
    print("\n--- 5. Running Sender Agent ---")
    job_sender()

    logger.info("==================================================")
    logger.info("Full Pipeline Pass Completed Successfully")
    logger.info("==================================================")

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        res_data = {
            "status": "healthy",
            "service": "Jefferson Geerman Cold Outreach Multi-Agent Pipeline",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.wfile.write(json.dumps(res_data).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress HTTP server noise

def start_health_server():
    port = int(os.getenv("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"Render Health Check HTTP server listening on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Could not start Health Check HTTP server on port {port}: {e}")

def run_startup_bootstrap():
    """Replenishes lead queue and triggers sender agent if drafted emails are ready on container boot/restart."""
    try:
        db = Database()
        drafts = db.get_leads_by_status("drafted", limit=100)
        logger.info(f"Container startup check: Currently {len(drafts)} ready drafted emails in queue.")

        if len(drafts) < 10:
            logger.info("Drafted queue below 10. Triggering automatic bootstrap pass (Scraper -> Enrichment -> Drafting)...")
            job_scraper()
            job_enrichment()
            job_drafting()
            drafts = db.get_leads_by_status("drafted", limit=100)
            logger.info(f"Startup bootstrap complete! Total ready drafted emails: {len(drafts)}")

        # Trigger Sender Agent if drafted emails exist and daily cap not reached today
        if len(drafts) > 0 and db.get_sends_today_count() < 20:
            logger.info(f"Drafted emails ready ({len(drafts)}). Triggering SenderAgent batch...")
            job_sender()
    except Exception as e:
        logger.error(f"Startup bootstrap exception: {e}", exc_info=True)

def start_scheduler():
    # 1. Start Render Health Check server thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    # 2. Trigger automatic bootstrap pass if DB is empty on startup
    bootstrap_thread = threading.Thread(target=run_startup_bootstrap, daemon=True)
    bootstrap_thread.start()

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    # 1. Lead Scraper: Daily at 2:00 AM IST
    scheduler.add_job(job_scraper, CronTrigger(hour=2, minute=0), id="scraper_daily", replace_existing=True)
    
    # 2. Enrichment Agent: Every 3 hours
    scheduler.add_job(job_enrichment, IntervalTrigger(hours=3), id="enrichment_interval", replace_existing=True)

    # 3. Email Drafting Agent: Every 3 hours (offset by 15 mins)
    scheduler.add_job(job_drafting, IntervalTrigger(hours=3, start_date=time.strftime("%Y-%m-%d %H:15:00")), id="drafting_interval", replace_existing=True)

    # 4. Sender Agent: Daily at 6:30 PM IST / 18:30 IST (Peak US/UK Business Hours: 9:00 AM EST)
    scheduler.add_job(job_sender, CronTrigger(hour=18, minute=30), id="sender_daily", replace_existing=True)

    # 5. Follow-Up Agent: Daily at 7:30 PM IST / 19:30 IST
    scheduler.add_job(job_followup, CronTrigger(hour=19, minute=30), id="followup_daily", replace_existing=True)

    logger.info("APScheduler 24/7 Multi-Agent Orchestrator Started successfully.")
    logger.info("Scheduled Jobs:")
    logger.info("  - Lead Scraper    : Daily at 02:00 AM IST")
    logger.info("  - Enrichment Agent: Every 3 Hours")
    logger.info("  - Drafting Agent  : Every 3 Hours (+15 mins)")
    logger.info("  - Sender Agent    : Daily at 06:30 PM IST (US 9:00 AM EST)")
    logger.info("  - Follow-Up Agent : Daily at 07:30 PM IST (US 10:00 AM EST)")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Orchestrator stopped gracefully.")

if __name__ == "__main__":
    if "--run-once" in sys.argv:
        run_full_pipeline_once()
    else:
        start_scheduler()
