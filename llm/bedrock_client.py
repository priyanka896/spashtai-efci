import os
import json
import time
import logging
import boto3
from retrieval.medical_rag import MedicalRAG

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Model Fallback Chain
# Primary  : Nova Lite  — fast, cheap, handles structured JSON well
# Fallback : Nova Pro   — heavier, if Lite fails or returns bad JSON
# ---------------------------------------------------------
PRIMARY_MODEL  = "amazon.nova-lite-v1:0"
FALLBACK_MODEL = "amazon.nova-pro-v1:0"

# Retry config
MAX_RETRIES  = 3
RETRY_DELAYS = [1, 2, 4]   # seconds — exponential backoff


class BedrockClient:
    """
    Explainability Agent for EFCI.

    Responsibilities:
    - Structured clinical concept extraction
    - RAG-based grounding retrieval
    - Grounded plain-language explanation generation

    Resilience:
    - Nova Lite primary with exponential backoff (3 attempts)
    - Automatic fallback to Nova Pro on persistent failure
    - Per-call fallback: extraction and explanation fail independently
    - models_used dict returned for audit transparency

    Does NOT perform:
    - Safety gating        (SafetyClassifier)
    - Confidence scoring   (ConfidenceCalibrator)
    - Audit logging        (AuditLogger)
    """

    def __init__(self, region=None):
        self.region         = region or os.getenv("AWS_REGION", "us-east-1")
        self.client         = boto3.client(
            service_name="bedrock-runtime",
            region_name=self.region
        )
        self.primary_model  = os.getenv("BEDROCK_MODEL_ARN",          PRIMARY_MODEL)
        self.fallback_model = os.getenv("BEDROCK_FALLBACK_MODEL_ARN",  FALLBACK_MODEL)
        self.rag            = MedicalRAG()

    # ---------------------------------------------------------
    # Normalize LLM schema drift (dict lab values to strings)
    # ---------------------------------------------------------
    def _normalize_to_strings(self, items):
        normalized = []
        for item in items:
            if isinstance(item, str):
                normalized.append(item)
            elif isinstance(item, dict):
                for key in ["name", "value", "term", "condition"]:
                    if key in item:
                        normalized.append(str(item[key]))
                        break
        return normalized

    # ---------------------------------------------------------
    # Single raw model call
    # ---------------------------------------------------------
    def _call_model(self, prompt: str, model_id: str,
                    max_tokens: int = 1000, temperature: float = 0.1) -> str:
        response = self.client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature}
        )
        text = response["output"]["message"]["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.replace("```json", "").replace("```", "").strip()
        return text

    # ---------------------------------------------------------
    # Retry + automatic fallback wrapper
    # ---------------------------------------------------------
    def _call_with_resilience(self, prompt: str, max_tokens: int = 1000,
                               temperature: float = 0.1,
                               label: str = "call") -> tuple:
        """
        Returns (response_text, model_id_used).
        Tries primary model up to MAX_RETRIES with exponential backoff,
        then falls back to fallback model on persistent failure.
        """
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"{label}: attempt {attempt+1}/{MAX_RETRIES} "
                            f"[{self.primary_model}]")
                text = self._call_model(prompt, self.primary_model,
                                        max_tokens, temperature)
                return text, self.primary_model
            except Exception as e:
                last_error = e
                logger.warning(f"{label} attempt {attempt+1} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.info(f"Retrying {label} in {delay}s...")
                    time.sleep(delay)

        # Primary exhausted — try fallback
        logger.warning(f"{label}: switching to fallback [{self.fallback_model}]")
        try:
            text = self._call_model(prompt, self.fallback_model,
                                    max_tokens, temperature)
            logger.info(f"{label}: fallback succeeded")
            return text, self.fallback_model
        except Exception as e:
            raise RuntimeError(
                f"{label} failed on both models. "
                f"Primary: {last_error}. Fallback: {e}"
            )

    # ---------------------------------------------------------
    # Safe JSON parse
    # ---------------------------------------------------------
    def _parse_json(self, text: str, label: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise Exception(
                f"{label}: model did not return valid JSON. "
                f"Error: {e}. Raw output: {text[:200]}"
            )

    # ---------------------------------------------------------
    # Main API
    # ---------------------------------------------------------
    def extract_structured_concepts(self, clinical_text: str) -> dict:

        models_used = {}

        # ── 1. Structured Extraction ──────────────────────────
        extraction_prompt = f"""You are a strict medical information extractor.

Your task is to extract structured data from the medical document below.

IMPORTANT RULES:
- Return valid JSON only. No explanations, commentary, or markdown.
- No text outside the JSON object.
- If unsure, leave fields empty.
- If no diagnosis is explicitly stated, do NOT infer one.

Return JSON exactly in this format:

{{
  "conditions": [],
  "lab_values": [],
  "medications": [],
  "follow_up_actions": [],
  "clinical_reasoning_summary": ""
}}

Extraction Guidelines:
- Conditions: explicitly stated diagnoses only.
- Lab values: include test_name, value, unit, reference_range if present.
- Do NOT infer pregnancy if only HCG is elevated. HCG elevation has many causes.
- Do NOT mention pregnancy unless the word "pregnant" or "pregnancy" is explicitly written in the document.
- If the patient gender is Male, NEVER mention pregnancy under any circumstances.
- Do NOT interpret beyond what is explicitly written.
- CLINICAL ACCURACY — kidney markers: HIGH BUN/Creatinine = possible kidney impairment. LOW BUN/Creatinine = often normal or due to low protein/muscle mass. Do NOT say low BUN/Creatinine indicates reduced kidney function.
- CLINICAL ACCURACY — eGFR: ≥60 mL/min = normal or mildly reduced. <60 = reduced kidney function. eGFR of exactly 60 is borderline normal, not clearly impaired.
- CLINICAL ACCURACY — pH: 7.35-7.45 is normal. 7.31 is mildly below normal (mild acidemia), not "normal".

Medical Document:
{clinical_text}"""

        raw, model_used = self._call_with_resilience(
            extraction_prompt, max_tokens=800, temperature=0.1, label="extraction"
        )
        models_used["extraction"] = model_used
        structured_data = self._parse_json(raw, "Structured extraction")

        # ── 2. RAG Retrieval ──────────────────────────────────
        # Use test_name from lab values — NEVER the numeric value.
        # Searching "11.00" returns ICD coding tools, not HCG physiology.
        conditions  = self._normalize_to_strings(structured_data.get("conditions", []))
        medications = self._normalize_to_strings(structured_data.get("medications", []))

        lab_search_terms = []
        for lab in structured_data.get("lab_values", []):
            if isinstance(lab, dict) and lab.get("test_name"):
                lab_search_terms.append(lab["test_name"])
            elif isinstance(lab, str):
                lab_search_terms.append(lab)

        all_terms = conditions + lab_search_terms + medications

        # Per-term RAG — prevents single-term dominance (e.g. Serum Creatinine x3)
        # Numeric values are already excluded since we use test_name not value
        retrieved_entries = []
        if all_terms:
            import hashlib as _hl
            seen_hashes = set()
            seen_terms_rag = set()
            for term in all_terms[:6]:
                term_clean = term.strip()
                if not term_clean or term_clean.lower() in seen_terms_rag:
                    continue
                seen_terms_rag.add(term_clean.lower())
                try:
                    entries = self.rag.retrieve(term_clean, top_k=2)
                    for e in entries:
                        h = _hl.sha256(e.get("definition","").strip().lower().encode()).hexdigest()
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            retrieved_entries.append(e)
                except Exception as rag_e:
                    logger.warning(f"RAG failed for '{term_clean}': {rag_e}")
            retrieved_entries = retrieved_entries[:8]
        else:
            retrieved_entries = self.rag.retrieve(clinical_text[:200])

        retrieved_context = "\n".join(
            f"{e['term']}: {e['definition']}" for e in retrieved_entries
        )

        # ── 3. Grounded Explanation ───────────────────────────
        # Extract gender from clinical text to pass explicitly to model
        import re as _re
        gender_match = _re.search(r'gender[:\s]+([a-zA-Z]+)', clinical_text, _re.IGNORECASE)
        patient_gender = gender_match.group(1).strip().upper() if gender_match else "UNKNOWN"
        gender_instruction = (
            "CRITICAL: The patient is MALE. Under NO circumstances mention pregnancy, "
            "even if HCG is elevated. In males, elevated HCG may relate to other "
            "physiological factors. Do not speculate on cause."
            if patient_gender == "MALE"
            else "Do NOT mention pregnancy unless explicitly stated in the document."
        )

        explanation_prompt = f"""You are an Explainability-Only Clinical Education Engine.

You MUST NOT:
- Recommend treatments, medications, or transfusions
- Suggest referrals, diagnostic workups, or escalation of care
- Mention pregnancy unless the word appears explicitly in the clinical document

{gender_instruction}

If treatment or follow-up is not in the clinical note, follow_up_actions MUST be empty.

You ARE allowed to:
- Explain what the condition or lab result is
- Explain why a lab value is outside the reference range
- Explain what a diagnosis means in educational terms
- Write plain_language_explanation in simple, calm language a patient can understand
- NEVER state or imply a diagnosis that is not explicitly documented
- CLINICAL ACCURACY: LOW BUN/Creatinine does NOT indicate kidney damage. Only HIGH values suggest impairment. Say low values may reflect low protein intake or muscle mass.
- CLINICAL ACCURACY: eGFR 60 is borderline — say "at the lower end of normal" not "mildly impaired".
- CLINICAL ACCURACY: pH 7.31 is mildly below the normal range (7.35-7.45), not normal. Say "slightly acidic, just below the normal range".

Strictly educational tone only. No care plans. No interventions.

Grounded Medical Context (from trusted public sources):
{retrieved_context}

Structured Clinical Data (Patient Gender: {patient_gender}):
{json.dumps(structured_data, indent=2)}

Return ONLY valid JSON in this structure:

{{
  "conditions": [],
  "lab_values": [],
  "medications": [],
  "follow_up_actions": [],
  "clinical_reasoning_summary": "2-3 sentence clinical summary: what is normal, what is abnormal, why it matters. For clinicians. No treatment recommendations.",
  "plain_language_explanation": ""
}}

IMPORTANT: clinical_reasoning_summary must always be filled. Never leave it empty."""

        raw, model_used = self._call_with_resilience(
            explanation_prompt, max_tokens=2000, temperature=0.2, label="explanation"
        )
        models_used["explanation"] = model_used
        llm_output = self._parse_json(raw, "Grounded explanation")

        # ── 4. Return orchestrator contract ───────────────────
        return {
            "structured_data":   structured_data,
            "retrieved_entries": retrieved_entries,
            "llm_output":        llm_output,
            "models_used":       models_used    # surfaced in Advanced View / audit log
        }
