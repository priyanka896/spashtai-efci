"""
medical_rag.py
Dynamic Retrieval Layer for EFCI (AWS Bedrock Compatible)

Responsibility: RETRIEVAL ONLY.
"""

import os
import re
import json
import time
import hashlib
import logging
import urllib.parse
import urllib.request
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("MedicalRAG")


# ---------------------------------------------------------------------------
# Trust Scoring
# ---------------------------------------------------------------------------

HIGH_TRUST = {
    "nih.gov",
    "who.int",
    "cdc.gov",
    "medlineplus.gov",
    "ncbi.nlm.nih.gov",
}

MID_TRUST = {
    "mayoclinic.org",
    "clevelandclinic.org",
    "hopkinsmedicine.org",
}


def _source_trust_bonus(url: str) -> int:
    if any(domain in url for domain in HIGH_TRUST):
        return 3
    if any(domain in url for domain in MID_TRUST):
        return 2
    return 1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}&kl=us-en"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_PAGE_CHARS = 8000
CACHE_TTL_HOURS = 24
MAX_SEARCH_RESULTS = 5
MAX_WORKERS = 3
POLITE_DELAY_SECONDS = 0.2
REQUIRED_KEYS = {"term", "definition", "confidence_band", "source_url"}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-zA-Z0-9]+", text.lower()))


def _safe_json_load(text: str) -> Optional[list]:
    try:
        return json.loads(text)
    except Exception:
        logger.warning("Invalid JSON from model.")
        return None


def _validate_entry(entry: Dict) -> bool:
    return isinstance(entry, dict) and REQUIRED_KEYS.issubset(entry.keys())


# ---------------------------------------------------------------------------
# Cache — DynamoDB (cloud-native, persistent across restarts, EC2/Lambda safe)
# ---------------------------------------------------------------------------
#
# Table schema:
#   Table name  : efci-rag-cache          (set via DYNAMO_CACHE_TABLE env var)
#   Partition key: cache_key  (String)    SHA256 of query string
#   Attribute   : value       (String)    JSON-serialised list of RAG entries
#   Attribute   : expires_at  (Number)    Unix epoch — DynamoDB native TTL
#
# DynamoDB TTL:
#   Enable TTL on the "expires_at" attribute in the AWS console.
#   DynamoDB will automatically delete expired items within ~48h.
#   No Lambda or cron job needed.
#
# Fallback:
#   If DynamoDB is unavailable (network, permissions), the cache silently
#   degrades — retrieve() still works, just without caching. Never crashes.
# ---------------------------------------------------------------------------

class _DynamoCache:
    """
    Cloud-native DynamoDB cache replacing local file cache.

    Advantages over file cache:
    - Persists across EC2 reboots and container restarts
    - Works on Lambda (no writable filesystem needed)
    - Shared across multiple EC2 instances behind a load balancer
    - Native TTL — no cleanup cron job required
    - Visible in AWS Console for audit/debugging
    - Costs ~$0.00 for hackathon-level traffic
    """

    def __init__(self):
        self.table_name = os.getenv("DYNAMO_CACHE_TABLE", "efci-rag-cache")
        self.region     = os.getenv("AWS_REGION", "us-east-1")
        self._enabled   = False
        try:
            self._dynamo = boto3.resource("dynamodb", region_name=self.region)
            self._table  = self._dynamo.Table(self.table_name)
            # Lightweight probe — confirms table exists and IAM allows access
            self._table.table_status
            self._enabled = True
            logger.info(f"DynamoDB cache enabled: {self.table_name}")
        except Exception as e:
            logger.warning(
                f"DynamoDB cache unavailable ({e}). "
                f"Falling back to no-cache mode. RAG will still work."
            )

    def _make_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def get(self, key: str):
        if not self._enabled:
            return None
        try:
            response = self._table.get_item(
                Key={"cache_key": self._make_key(key)}
            )
            item = response.get("Item")
            if not item:
                return None
            # DynamoDB TTL handles expiry, but double-check for safety
            if int(item.get("expires_at", 0)) < int(time.time()):
                return None
            return json.loads(item["value"])
        except Exception as e:
            logger.warning(f"DynamoDB cache get failed: {e}")
            return None

    def set(self, key: str, value: list):
        if not self._enabled:
            return
        try:
            expires_at = int(time.time()) + (CACHE_TTL_HOURS * 3600)
            self._table.put_item(Item={
                "cache_key":  self._make_key(key),
                "value":      json.dumps(value),
                "expires_at": expires_at,          # DynamoDB native TTL attribute
                "query_hint": key[:200],           # human-readable in console
            })
        except Exception as e:
            logger.warning(f"DynamoDB cache set failed: {e}")


# ---------------------------------------------------------------------------
# Web Helpers
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = 8) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"Fetch failed [{url}]: {e}")
        return None


def _strip_html(html: str) -> str:
    html = re.sub(
        r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _search_web(query: str) -> List[Dict]:
    encoded = urllib.parse.quote_plus(f"{query} medical definition")
    html = _fetch_url(SEARCH_URL_TEMPLATE.format(query=encoded))

    if not html:
        logger.warning("Search HTML fetch failed.")
        return []

    results = []

    links = re.findall(
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        re.DOTALL
    )

    for href, title_html in links:
        if "http" not in href:
            continue

        # Unwrap DuckDuckGo redirect URLs
        if "duckduckgo.com/l/" in href:
            parsed = urllib.parse.parse_qs(
                urllib.parse.urlparse(href).query
            )
            href = parsed.get("uddg", [href])[0]

        title = re.sub(r"<[^>]+>", "", title_html).strip()

        if len(title) < 10:
            continue

        results.append({"title": title, "url": href})

    if not results:
        logger.warning("No search results parsed from HTML.")

    # Prioritize trusted domains
    trusted = [
        r for r in results
        if any(d in r["url"] for d in HIGH_TRUST.union(MID_TRUST))
    ]
    others = [r for r in results if r not in trusted]

    return (trusted + others)[:MAX_SEARCH_RESULTS]


# ---------------------------------------------------------------------------
# MedicalRAG (AWS Native)
# ---------------------------------------------------------------------------

class MedicalRAG:

    def __init__(self, region=None):

        self.region = region or os.getenv("AWS_REGION", "us-east-1")

        self.client = boto3.client(
            service_name="bedrock-runtime",
            region_name=self.region
        )

        self.model_id = os.getenv("BEDROCK_MODEL_ARN")
        if not self.model_id:
            raise ValueError("BEDROCK_MODEL_ARN not set")

        self._cache = _DynamoCache()

    # ------------------------------------------------------------------

    def _extract_entries(self, raw_text: str, source_url: str, query: str) -> List[Dict]:

        prompt = f"""
Extract medical knowledge relevant to: "{query}"

Return a JSON array of objects with exactly:
- term
- definition
- confidence_band
- source_url (must be "{source_url}")

Rules:
- 1–5 entries
- No advice
- JSON only

TEXT:
{raw_text}
"""

        response = self.client.converse(
            modelId=self.model_id,
            messages=[{
                "role": "user",
                "content": [{"text": prompt}]
            }],
            inferenceConfig={
                "maxTokens": 800,
                "temperature": 0.1
            }
        )

        text = response["output"]["message"]["content"][0]["text"].strip()

        if text.startswith("```"):
            text = text.replace("```json", "").replace("```", "").strip()

        entries = _safe_json_load(text)

        if isinstance(entries, list):
            return [e for e in entries if _validate_entry(e)]

        return []

    # ------------------------------------------------------------------

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:

        if not query.strip():
            return []

        cache_key = f"retrieve::{query.lower()}::{top_k}"
        cached = self._cache.get(cache_key)

        if cached:
            logger.info(f"Cache hit: {query}")
            return cached

        search_results = _search_web(query)

        if not search_results:
            return []

        knowledge_base = []

        def scrape_and_extract(result):
            time.sleep(POLITE_DELAY_SECONDS)
            html = _fetch_url(result["url"])
            if not html:
                return []
            text = _strip_html(html)[:MAX_PAGE_CHARS]
            return self._extract_entries(text, result["url"], query)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(scrape_and_extract, r)
                for r in search_results
            ]
            for future in as_completed(futures):
                knowledge_base.extend(future.result())

        if not knowledge_base:
            logger.warning("No knowledge entries extracted.")
            return []

        query_tokens = _tokenize(query)
        scored = []

        for entry in knowledge_base:
            term_tokens = _tokenize(entry["term"])
            def_tokens = _tokenize(entry["definition"])

            overlap = (term_tokens | def_tokens) & query_tokens

            lex_score = (
                len(term_tokens & query_tokens) * 4
                + len(def_tokens & query_tokens)
            )

            if entry["term"].lower() in query.lower():
                lex_score += 5

            trust_bonus = _source_trust_bonus(entry["source_url"])

            scored.append({
                "term": entry["term"],
                "definition": entry["definition"],
                "source_url": entry["source_url"],
                "confidence_band": entry["confidence_band"],
                "retrieval_score": lex_score + trust_bonus,
                "matched_tokens": list(overlap),
            })

        # Deduplicate by term
        seen = set()
        ranked = []

        for entry in sorted(scored, key=lambda x: x["retrieval_score"], reverse=True):
            key = hashlib.sha256(
                entry["definition"].strip().lower().encode()
            ).hexdigest()
            if key not in seen:
                seen.add(key)
                ranked.append(entry)

        result = ranked[:top_k]
        self._cache.set(cache_key, result)

        return result

    # ------------------------------------------------------------------

    def estimate_grounding_strength(self, retrieved_entries: List[Dict]) -> str:

        if not retrieved_entries:
            return "Low"

        top_score = retrieved_entries[0]["retrieval_score"]

        if top_score >= 8:
            return "High"
        if top_score >= 5:
            return "Moderate"
        return "Low"