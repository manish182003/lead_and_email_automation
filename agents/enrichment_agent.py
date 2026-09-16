from typing import Any
import re
import time
import logging
import urllib.robotparser
from typing import Optional, List, Dict, Tuple
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

from database import Database
from verifier import EmailVerifier

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

class EnrichmentAgent:
    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def can_fetch_robots(self, url: str) -> bool:
        """Checks robots.txt compliance before crawling with 3s timeout."""
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            res = self.session.get(robots_url, headers=HEADERS, timeout=3)
            if res.status_code == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(res.text.splitlines())
                return rp.can_fetch("*", url)
            return True
        except Exception:
            return True  # If robots.txt unreadable or missing, proceed with standard scraping

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetches page HTML with 5s timeout, content-type filter, and 2MB max stream cap."""
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        if not self.can_fetch_robots(url):
            logger.info(f"Robots.txt blocks crawling for: {url}")
            return None

        try:
            res = self.session.get(url, headers=HEADERS, timeout=5, allow_redirects=True, stream=True)
            if res.status_code == 200:
                content_type = res.headers.get("content-type", "").lower()
                if content_type and not any(ct in content_type for ct in ["text/html", "text/plain", "application/xhtml", "xml"]):
                    logger.debug(f"Skipping non-text Content-Type '{content_type}' for {url}")
                    return None

                # Read max 2MB into memory to prevent memory bloat on large files
                max_bytes = 2 * 1024 * 1024
                chunks = []
                downloaded = 0
                for chunk in res.iter_content(chunk_size=64 * 1024):
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if downloaded >= max_bytes:
                        logger.debug(f"Reached 2MB stream byte cap for {url}")
                        break

                return b"".join(chunks).decode("utf-8", errors="ignore")
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
        return None

    def extract_emails_from_html(self, html: str, domain: str) -> List[str]:
        """Finds mailto links, regex matches, and pattern matches."""
        found_emails = set()
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")

        # 1. Check mailto: links
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                clean = href.split("mailto:")[1].split("?")[0].strip()
                if EmailVerifier.is_valid_syntax(clean):
                    found_emails.add(clean.lower())

        # 2. Check regex in text and HTML
        text_matches = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", html)
        for m in text_matches:
            if EmailVerifier.is_valid_syntax(m):
                found_emails.add(m.lower())

        # Filter out emails not belonging to lead's domain if domain matches
        valid_domain_emails = [e for e in found_emails if domain and domain in e.split("@")[1]]
        if valid_domain_emails:
            return valid_domain_emails
        return list(found_emails)

    def extract_business_summary_and_pain_points(self, html: str, business_name: str) -> Tuple[str, str]:
        """Summarizes business model and detects potential AI/Automation/Mobile pain points."""
        if not html:
            return (f"{business_name} is a business providing services.", 
                    "No mobile app or custom AI automation detected.")

        soup = BeautifulSoup(html, "lxml")

        # Get title & meta description
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta_desc = ""
        meta_elem = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
        if meta_elem and meta_elem.get("content"):
            meta_desc = meta_elem["content"].strip()

        # Get main headers & paragraphs
        h1s = " ".join([h.get_text(strip=True) for h in soup.find_all(["h1", "h2"])[:3]])
        paras = " ".join([p.get_text(strip=True) for p in soup.find_all("p")[:4]])

        full_text = f"{title}. {meta_desc}. {h1s}. {paras}".strip()
        # Clean whitespace
        full_text = re.sub(r"\s+", " ", full_text)[:400]

        summary = meta_desc if meta_desc and len(meta_desc) > 30 else full_text
        if not summary:
            summary = f"{business_name} offers services in their respective industry."

        # Detect pain points
        pain_points = []
        lower_html = html.lower()
        
        if not any(kw in lower_html for kw in ["flutter", "ios app", "android app", "download on app store", "google play"]):
            pain_points.append("No native or mobile application presence listed.")
        
        if not any(kw in lower_html for kw in ["ai", "chatbot", "automation", "llm", "rag", "machine learning"]):
            pain_points.append("Manual processes or missing modern AI/automation integration.")

        if "contact us" in lower_html or "fill out form" in lower_html:
            pain_points.append("Relies on generic contact forms rather than automated workflow systems.")

        pain_signal = " ".join(pain_points) if pain_points else "Opportunity to optimize operational workflows with custom AI and mobile solutions."
        return summary, pain_signal

    @staticmethod
    def score_email_priority(email: str) -> int:
        """
        Ranks candidate emails by business value and decision-maker likelihood.
        Higher score = Higher priority.
        """
        if not email or "@" not in email:
            return 0
        email_clean = email.lower().strip()
        prefix = email_clean.split("@")[0]

        # Tier 1: Direct Executive / Owner / Founder (Score 100)
        if any(kw in prefix for kw in ["ceo", "founder", "owner", "president", "director", "manager", "head", "vp", "exec"]):
            return 100

        # Tier 2: Direct Named Individual Emails (e.g. john@, sarah@, alex@) (Score 80)
        if not any(kw in prefix for kw in ["info", "contact", "hello", "sales", "support", "help", "admin", "office", "service", "billing", "careers", "jobs", "receiving", "ticket"]):
            return 80

        # Tier 3: High-Value General Business Inboxes (Score 60)
        if any(kw in prefix for kw in ["hello", "contact", "info", "sales", "office", "inquiries", "inquiry", "biz"]):
            return 60

        # Tier 4: General Admin (Score 40)
        if any(kw in prefix for kw in ["admin", "general"]):
            return 40

        # Tier 5: Low-Priority / Auto-Responder Ticketing / Support (Score 10 - Deprioritized)
        if any(kw in prefix for kw in ["support", "help", "service", "ticket", "tickets", "receiving", "billing", "careers", "jobs", "privacy", "feedback", "noreply", "no-reply"]):
            return 10

        return 50

    def enrich_lead(self, lead: Dict[str, Any]) -> Tuple[str, Optional[str], bool, str, str, Optional[str]]:
        """
        Enriches a single lead dictionary.
        Returns: (status, contact_email, is_verified, summary, pain_point, error_msg)
        """
        lead_id = lead["id"]
        business_name = lead["business_name"]
        website = lead.get("website", "")
        domain = lead.get("domain", "") or self.db.extract_domain(website)

        logger.info(f"Enriching Lead ID {lead_id}: {business_name} ({website})...")

        if not website:
            return "no_email", None, False, f"{business_name} (No website provided)", "No website available.", "Missing website URL"

        if not website.startswith(("http://", "https://")):
            website = "https://" + website

        # 1. Fetch Homepage
        homepage_html = self.fetch_page(website)
        if not homepage_html:
            # Try http fallback
            if website.startswith("https://"):
                homepage_html = self.fetch_page(website.replace("https://", "http://"))

        if not homepage_html:
            logger.warning(f"Could not reach website for {business_name}")
            return "enrich_failed", None, False, "", "", f"Failed to fetch homepage: {website}"

        # 2. Extract Emails from Homepage
        found_emails = self.extract_emails_from_html(homepage_html, domain)

        # 3. If no email, check Contact / About pages
        if not found_emails:
            soup = BeautifulSoup(homepage_html, "lxml")
            sub_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"].lower()
                if any(sub in href for sub in ["contact", "about", "team", "support", "help"]):
                    full_sub = urljoin(website, a["href"])
                    sub_links.append(full_sub)

            for sub_url in sub_links[:3]:
                sub_html = self.fetch_page(sub_url)
                if sub_html:
                    sub_emails = self.extract_emails_from_html(sub_html, domain)
                    found_emails.extend(sub_emails)
                    if found_emails:
                        break

        # 4. Extract Business Summary & Pain Points
        summary, pain_point = self.extract_business_summary_and_pain_points(homepage_html, business_name)

        # 6. Verify Extracted Emails & Check for Duplicates
        target_email = None
        email_verified = False
        is_duplicate = False

        # Sort candidate emails by decision-maker score (highest priority first)
        sorted_candidates = sorted(list(set(found_emails)), key=lambda e: self.score_email_priority(e), reverse=True)

        for candidate in sorted_candidates:
            cand = candidate.strip().rstrip('.')
            if self.db.is_email_used(cand, exclude_lead_id=lead_id):
                logger.info(f"Email '{cand}' is already used by another lead or suppressed. Skipping duplicate email.")
                is_duplicate = True
                continue

            is_valid, reason = EmailVerifier.verify_email(cand)
            logger.info(f"Testing email '{cand}' (Priority Score={self.score_email_priority(cand)}): Valid={is_valid} ({reason})")
            if is_valid:
                target_email = cand
                email_verified = True
                break
            elif not target_email:
                target_email = cand  # Store unverified for fallback tracking

        if email_verified and target_email:
            return "enriched", target_email, True, summary, pain_point, None
        elif is_duplicate and not target_email:
            return "duplicate_email", None, False, summary, pain_point, "Contact email is duplicate or suppressed"
        elif target_email:
            return "invalid_email", target_email, False, summary, pain_point, "Email failed verification"
        else:
            return "no_email", None, False, summary, pain_point, "No contact email found"

    def run(self, limit: int = 50) -> Dict[str, int]:
        logger.info("=== Running Enrichment Agent ===")
        new_leads = self.db.get_leads_by_status("new", limit=limit)
        if not new_leads:
            logger.info("No 'new' leads found to enrich. Triggering ScraperAgent to fetch fresh leads...")
            try:
                from agents.scraper_agent import ScraperAgent
                ScraperAgent(db=self.db).run()
                new_leads = self.db.get_leads_by_status("new", limit=limit)
            except Exception as scrape_err:
                logger.warning(f"Auto-scraper trigger notice: {scrape_err}")

            if not new_leads:
                logger.info("No new leads available to enrich.")
                return {"processed": 0, "enriched": 0, "no_email": 0, "failed": 0}

        enriched_count = 0
        no_email_count = 0
        failed_count = 0

        for lead in new_leads:
            try:
                status, email, is_verified, summary, pain_point, error = self.enrich_lead(lead)
                self.db.update_lead_enrichment(
                    lead_id=lead["id"],
                    email=email,
                    is_verified=is_verified,
                    summary=summary,
                    pain_point=pain_point,
                    status=status,
                    error=error
                )

                if status == "enriched":
                    enriched_count += 1
                elif status in ("no_email", "invalid_email"):
                    no_email_count += 1
                else:
                    failed_count += 1

                time.sleep(1.0)  # Rate control
            except Exception as e:
                logger.error(f"Error enriching lead ID {lead['id']}: {e}")
                self.db.update_status(lead["id"], "enrich_failed", str(e))
                failed_count += 1

        processed = len(new_leads)
        logger.info(f"Enrichment Finished: Processed={processed}, Enriched={enriched_count}, NoEmail/Invalid={no_email_count}, Failed={failed_count}")
        self.db.log_run("EnrichmentAgent", processed, enriched_count, failed_count, 
                        f"Enriched {enriched_count} leads successfully.")
        return {
            "processed": processed,
            "enriched": enriched_count,
            "no_email": no_email_count,
            "failed": failed_count
        }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = EnrichmentAgent()
    stats = agent.run()
    print("Enrichment Stats:", stats)
