import os
import re
import time
import json
import logging
from typing import Dict, Any, Tuple, Optional
from groq import Groq

from config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_FALLBACK_MODEL,
    SERVICES_SUMMARY, PORTFOLIO_URL, SECONDARY_PORTFOLIO,
    COMPANY_NAME, COMPANY_ADDRESS, OFFICIAL_SIGNATURE
)
from database import Database

logger = logging.getLogger(__name__)

class DraftingAgent:
    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.client = None
        if GROQ_API_KEY and GROQ_API_KEY != "your_groq_api_key_here":
            try:
                self.client = Groq(api_key=GROQ_API_KEY)
            except Exception as e:
                logger.error(f"Failed to initialize Groq client: {e}")

    def generate_email_llm(self, business_name: str, summary: str, pain_point: str, model: str = GROQ_MODEL) -> Tuple[str, str]:
        """Calls Groq LLM API to generate a short, curiosity-driven cold email focused on booking a discovery chat."""
        if not self.client:
            raise ValueError("Groq API Key missing or client uninitialized.")

        prompt = f"""
You are Jefferson Geerman from Bloo Beach Softwares LLC (bloobeach.com & manishjoshi.online).
Write a short, casual, curiosity-driven cold email to a small/medium business owner.

GOAL: Ask a quick question about their workflow (workorders, paper/excel vs software, dispatch, client intake) to start a 10-minute discovery chat. DO NOT try to sell services or pitch products in this first email!

TARGET BUSINESS DETAILS:
- Company Name: {business_name}
- What They Do (Summary): {summary}
- Context / Service Type: {pain_point}

EMAIL STRUCTURE INSTRUCTIONS:
1. GREETING: "Hey there," or "Hi {business_name} team," or "Hi [Name] — quick one:"
2. FIRST SENTENCE: Ask a casual, curious question about how a company like theirs handles their paperwork, dispatch, or client intake workflows.
   Example style: "When a team member at {business_name} finishes a job, how does the paperwork get back to the office? Still spreadsheets or paper?"
3. CALL TO ACTION: A soft, low-pressure request:
   "We built workflow automation tools so teams can dispatch, log jobs, and get signed work orders out to clients in a few taps. Open to a quick 10-minute look?"
4. SIGNATURE (MUST MATCH THIS EXACT FORMAT AT THE BOTTOM):
Jefferson Geerman
Bloo Beach Softwares LLC | 1309 Coffeen Avenue STE 1200, Sheridan, WY 82801, USA
Website: https://bloobeach.com | Portfolio: https://manishjoshi.online

RULES:
- NO HARD SALES PITCH! NO "I am an AI engineer"! NO "buy our software"!
- Keep total email body under 75 words.
- Natural, casual, direct human tone.
- Subject Line: Short (3-5 words), lowercase, curiosity-driven (e.g. "quick question about workflow", "dispatch workflow question", "quick question for {business_name}").

OUTPUT FORMAT (JSON ONLY):
Return ONLY a JSON object with keys "subject" and "body".
"""

        response = self.client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You output strictly valid JSON with 'subject' and 'body' keys."},
                {"role": "user", "content": prompt}
            ],
            model=model,
            temperature=0.7,
            max_tokens=280,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        return data.get("subject", "").strip(), data.get("body", "").strip()

    def validate_quality_heuristic(self, subject: str, body: str, business_name: str) -> Tuple[bool, str]:
        """Validates draft quality, length, and official company signature."""
        if not subject or not body:
            return False, "Empty subject or body"

        words = body.split()
        if len(words) > 100:
            return False, f"Body too long ({len(words)} words > 100)"

        if len(words) < 20:
            return False, f"Body too short ({len(words)} words < 20)"

        if subject.isupper():
            return False, "Subject is ALL CAPS"

        # Ensure signature and official address are present
        if "Jefferson Geerman" not in body or "Bloo Beach Softwares LLC" not in body:
            return False, "Missing Jefferson Geerman or Bloo Beach Softwares LLC signature"

        # Reject hard sales pitches or generic boilerplate
        prohibited_phrases = [
            "dear sir", "dear madam", "hope this email finds you well", "synergy",
            "game-changer", "i was reviewing your business operations",
            "we offer", "our services include", "buy our", "discount"
        ]
        if any(phrase in body.lower() for phrase in prohibited_phrases):
            return False, "Contains hard sales pitch or prohibited boilerplate"

        return True, "Passed quality heuristic"

    def draft_email_for_lead(self, lead: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str], str]:
        """Generates and validates draft email with auto-retry."""
        business_name = self.db.clean_business_name(lead["business_name"]) or lead["business_name"]
        summary = lead.get("summary", "") or f"{business_name} offers professional services."
        pain_point = lead.get("pain_point", "") or "Opportunity to streamline workorders and dispatch workflows."

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                model_to_use = GROQ_MODEL if attempt == 1 else GROQ_FALLBACK_MODEL
                subject, body = self.generate_email_llm(business_name, summary, pain_point, model=model_to_use)
                
                is_valid, reason = self.validate_quality_heuristic(subject, body, business_name)
                if is_valid:
                    return True, subject, body, "Success"
                else:
                    logger.warning(f"Draft failed heuristic check (Attempt {attempt}): {reason}")
            except Exception as e:
                logger.error(f"Groq API error on attempt {attempt} for {business_name}: {e}")
                time.sleep(1.0)

        # Fallback template matching exact company signature and structure
        fallback_subject = f"quick question for {business_name}"
        fallback_body = (
            f"Hey there,\n\n"
            f"Quick one: when a team member at {business_name} finishes a job, how does the paperwork get back to the office — still spreadsheets or paper?\n\n"
            f"We built workflow automation tools so teams can dispatch, log jobs, and get signed work orders out to clients in a few taps. Open to a quick 10-minute look?\n\n"
            f"Jefferson Geerman\n"
            f"Bloo Beach Softwares LLC | 1309 Coffeen Avenue STE 1200, Sheridan, WY 82801, USA\n"
            f"Website: https://bloobeach.com | Portfolio: https://manishjoshi.online"
        )
        return True, fallback_subject, fallback_body, "Fallback template used"

    def run(self, limit: int = 50) -> Dict[str, int]:
        logger.info("=== Running Email Drafting Agent ===")
        enriched_leads = self.db.get_leads_by_status("enriched", limit=limit)
        if not enriched_leads:
            logger.info("No 'enriched' leads found to draft emails for.")
            return {"processed": 0, "drafted": 0, "failed": 0}

        drafted_count = 0
        failed_count = 0

        for lead in enriched_leads:
            lead_id = lead["id"]
            business_name = lead["business_name"]
            logger.info(f"Drafting cold email for Lead ID {lead_id}: {business_name}...")

            success, subject, body, status_msg = self.draft_email_for_lead(lead)
            if success and subject and body:
                self.db.update_lead_draft(lead_id, subject, body)
                drafted_count += 1
                logger.info(f"Successfully drafted email for {business_name} | Subject: '{subject}'")
            else:
                self.db.update_status(lead_id, "draft_failed", status_msg)
                failed_count += 1

            time.sleep(1.5)  # Rate pacing to stay well under Groq Free Tier TPM limit

        processed = len(enriched_leads)
        logger.info(f"Drafting Finished: Processed={processed}, Drafted={drafted_count}, Failed={failed_count}")
        self.db.log_run("DraftingAgent", processed, drafted_count, failed_count, 
                        f"Drafted {drafted_count} personalized emails.")
        return {
            "processed": processed,
            "drafted": drafted_count,
            "failed": failed_count
        }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = DraftingAgent()
    stats = agent.run()
    print("Drafting Stats:", stats)
