import os
import re
import csv
import time
import random
import logging
import requests
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import quote, urlparse
from bs4 import BeautifulSoup

from config import (
    SERPAPI_KEY, GOOGLE_PLACES_API_KEY, SEARCH_QUERIES, IMPORT_CSV_PATH
)
from database import Database

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

class ScraperAgent:
    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/"
        }

    def search_serpapi_google_maps(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Extracts Google Maps leads via SerpAPI (250 free searches/month)."""
        if not SERPAPI_KEY or SERPAPI_KEY == "your_serpapi_key_here":
            logger.info("SerpAPI key not configured. Skipping SerpAPI.")
            return []

        leads = []
        try:
            url = "https://serpapi.com/search.json"
            params = {
                "engine": "google_maps",
                "q": query,
                "api_key": SERPAPI_KEY,
                "type": "search",
                "hl": "en"
            }
            logger.info(f"Querying SerpAPI Google Maps for: '{query}'...")
            res = self.session.get(url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                results = data.get("local_results", [])
                for item in results:
                    website = item.get("website", "")
                    if not website and item.get("links", {}).get("website"):
                        website = item.get("links", {}).get("website")

                    lead = {
                        "business_name": item.get("title", ""),
                        "website": website,
                        "category": item.get("type", "") or item.get("category", ""),
                        "address": item.get("address", ""),
                        "phone": item.get("phone", ""),
                        "google_place_id": item.get("place_id", ""),
                        "source": "serpapi_google_maps"
                    }
                    if lead["business_name"]:
                        leads.append(lead)
                logger.info(f"SerpAPI returned {len(leads)} leads for '{query}'.")
            else:
                logger.warning(f"SerpAPI returned status code {res.status_code}: {res.text[:200]}")
        except Exception as e:
            logger.error(f"Error during SerpAPI Google Maps search: {e}")
        return leads

    def search_google_places_api(self, query: str) -> List[Dict[str, Any]]:
        """Queries Google Places Text Search API (New & Legacy endpoints)."""
        if not GOOGLE_PLACES_API_KEY or GOOGLE_PLACES_API_KEY.startswith("your_"):
            return []

        leads = []
        try:
            logger.info(f"Querying Google Places API for: '{query}'...")
            # 1. Try Google Places API (New) Text Search endpoint first
            url_new = "https://places.googleapis.com/v1/places:searchText"
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
                "X-Goog-FieldMask": "places.id,places.displayName,places.websiteUri,places.formattedAddress,places.nationalPhoneNumber,places.primaryType"
            }
            payload = {"textQuery": query}
            res = self.session.post(url_new, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for place in data.get("places", []):
                    leads.append({
                        "business_name": place.get("displayName", {}).get("text", ""),
                        "website": place.get("websiteUri", ""),
                        "category": place.get("primaryType", ""),
                        "address": place.get("formattedAddress", ""),
                        "phone": place.get("nationalPhoneNumber", ""),
                        "google_place_id": place.get("id", ""),
                        "source": "google_places_api_new"
                    })
                if leads:
                    logger.info(f"Google Places API (New) returned {len(leads)} leads.")
                    return leads

            # 2. Fallback to Legacy Places Text Search API
            url_legacy = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={quote(query)}&key={GOOGLE_PLACES_API_KEY}"
            res_leg = self.session.get(url_legacy, timeout=10)
            if res_leg.status_code == 200:
                data_leg = res_leg.json()
                for place in data_leg.get("results", []):
                    leads.append({
                        "business_name": place.get("name", ""),
                        "website": "",
                        "category": ", ".join(place.get("types", [])),
                        "address": place.get("formatted_address", ""),
                        "phone": "",
                        "google_place_id": place.get("place_id", ""),
                        "source": "google_places_api_legacy"
                    })
                logger.info(f"Google Places API (Legacy) returned {len(leads)} leads.")
        except Exception as e:
            logger.error(f"Error querying Google Places API: {e}")
        return leads

    def search_stealth_google_maps(self, query: str) -> List[Dict[str, Any]]:
        """Stealth web search fallback using DuckDuckGo HTML parsing & headers rotation."""
        leads = []
        try:
            url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
            logger.info(f"Running stealth web search for: '{query}'...")
            time.sleep(random.uniform(1.0, 2.5))  # Anti-bot delay jitter
            res = self.session.get(url, headers=self.get_headers(), timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "lxml")
                results = soup.find_all("div", class_="result")
                for r in results:
                    title_elem = r.find("a", class_="result__a")
                    snippet_elem = r.find("a", class_="result__snippet")
                    url_elem = r.find("a", class_="result__url")
                    
                    if title_elem and url_elem:
                        name = title_elem.get_text(strip=True)
                        raw_url = url_elem.get_text(strip=True)
                        if not raw_url.startswith(("http://", "https://")):
                            raw_url = "https://" + raw_url
                        
                        snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

                        # Filter out mega directories or platforms
                        domain = self.db.extract_domain(raw_url)
                        if any(ignored in domain for ignored in ["wikipedia", "facebook", "twitter", "linkedin", "youtube", "amazon"]):
                            continue

                        leads.append({
                            "business_name": name,
                            "website": raw_url,
                            "category": "Search Result",
                            "address": "",
                            "phone": "",
                            "google_place_id": "",
                            "source": "stealth_web_scraper"
                        })
            logger.info(f"Stealth search returned {len(leads)} raw web leads.")
        except requests.exceptions.Timeout:
            logger.warning(f"Stealth search connection timed out for query '{query}' (DuckDuckGo rate-limited cloud IP). Skipping stealth fallback.")
        except Exception as e:
            logger.warning(f"Stealth search notice: {e}")
        return leads

    def import_from_csv(self, csv_path: Path = IMPORT_CSV_PATH) -> List[Dict[str, Any]]:
        """Imports leads from a CSV export file if present."""
        if not csv_path.exists():
            return []
        
        leads = []
        try:
            logger.info(f"Importing leads from CSV file: {csv_path}")
            with open(csv_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    business_name = row.get("business_name") or row.get("name") or row.get("Company")
                    website = row.get("website") or row.get("url") or row.get("Website")
                    if business_name:
                        leads.append({
                            "business_name": business_name.strip(),
                            "website": (website or "").strip(),
                            "category": row.get("category", "CSV Import"),
                            "address": row.get("address", ""),
                            "phone": row.get("phone", ""),
                            "google_place_id": row.get("google_place_id", ""),
                            "source": "csv_import"
                        })
            logger.info(f"Loaded {len(leads)} entries from CSV.")
        except Exception as e:
            logger.error(f"Failed to read CSV file {csv_path}: {e}")
        return leads

    def run(self) -> Dict[str, int]:
        logger.info("=== Running Lead Scraper Agent ===")
        total_found = 0
        total_inserted = 0
        duplicates_skipped = 0

        # Step 1: Check CSV import first
        csv_leads = self.import_from_csv()
        for lead in csv_leads:
            total_found += 1
            if self.db.insert_lead(lead):
                total_inserted += 1
            else:
                duplicates_skipped += 1

        # Step 2: Run Query-based Scrapers
        for query in SEARCH_QUERIES:
            # 1. Try Google Places API first if key configured
            query_leads = self.search_google_places_api(query)

            # 2. Try SerpAPI Google Maps if Google Places returned nothing
            if not query_leads:
                query_leads = self.search_serpapi_google_maps(query)
            
            # 3. Fallback to Stealth web scraper if both returned nothing
            if not query_leads:
                query_leads = self.search_stealth_google_maps(query)

            for lead in query_leads:
                total_found += 1
                if self.db.insert_lead(lead):
                    total_inserted += 1
                else:
                    duplicates_skipped += 1

            # Respectful delay between queries
            time.sleep(random.uniform(1.5, 3.5))

        logger.info(f"Scraper Run Finished: Found={total_found}, Inserted={total_inserted}, Duplicates Skipped={duplicates_skipped}")
        self.db.log_run("ScraperAgent", total_found, total_inserted, duplicates_skipped, 
                        f"Inserted {total_inserted} new leads from {len(SEARCH_QUERIES)} search queries.")
        return {
            "found": total_found,
            "inserted": total_inserted,
            "skipped": duplicates_skipped
        }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = ScraperAgent()
    stats = agent.run()
    print("Scraper Stats:", stats)
