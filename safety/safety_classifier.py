import os
import json
import time
import logging
import boto3
from typing import Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Strict Global Enum Enforcement
# ---------------------------------------------------------
ALLOWED_CATEGORIES = {
    "clinical_documentation",
    "medical_advice_request",
    "emergency_scenario",
    "non_medical"
}

# ---------------------------------------------------------
# Model Fallback Chain
# Primary  : Nova Micro  — cheapest, sufficient for classification
# Fallback : Nova Lite   — if Micro is throttled or unavailable
# ---------------------------------------------------------
PRIMARY_MODEL  = "amazon.nova-micro-v1:0"
FALLBACK_MODEL = "amazon.nova-lite-v1:0"

# Retry config
MAX_RETRIES    = 3
RETRY_DELAYS   = [1, 2, 4]   # seconds — exponential backoff


class SafetyClassifier:
    """
    Primary input safety classifier for the EFCI system.

    Responsibilities:
    - AI-based intent classification (Nova Micro)
    - Strict enum enforcement
    - Policy gating (only clinical_documentation allowed)
    - Exponential backoff retry (3 attempts)
    - Automatic fallback to Nova Lite on model failure
    - Fail-safe: blocks on unknown errors, never silently passes
    """

    def __init__(self, region: str = None):
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.client = boto3.client(
            service_name="bedrock-runtime",
            region_name=self.region
        )
        # Primary model from env, default to Nova Micro
        self.primary_model  = os.getenv("SAFETY_MODEL_ARN", PRIMARY_MODEL)
        self.fallback_model = os.getenv("SAFETY_FALLBACK_MODEL_ARN", FALLBACK_MODEL)

    # ---------------------------------------------------------
    # Input sanitization
    # ---------------------------------------------------------
    def _sanitize_input(self, text: str) -> str:
        return text[:8000]

    # ---------------------------------------------------------
    # Prompt builder
    # ---------------------------------------------------------
    def _build_prompt(self, text: str) -> str:
        return f"""You are a safety classifier for a medical information system.

Classify the input into exactly one category:

- clinical_documentation: Lab reports, discharge summaries, clinical notes, blood test results,
  medical records, patient data with test values, reference ranges, diagnoses, medications,
  symptoms, medical history. ALSO includes: general medical questions like "what is anemia",
  "explain diabetes", "what does HCG mean" - these are educational queries that should be processed.

- medical_advice_request: Explicit requests for personalised treatment decisions WITHOUT any
  clinical context. Examples: "should I take aspirin", "what dosage of X should I use",
  "should I see a doctor". Note: "what is X" questions are NOT advice requests.

- emergency_scenario: Expressions of immediate danger, suicidal ideation, active medical emergency.
  Examples: "I'm having a heart attack", "I want to hurt myself", "chest pain can't breathe".

- non_medical: Completely unrelated content with zero medical context.
  Examples: jokes, code, recipes, sports, politics, general trivia.

CRITICAL SAFETY RULES (these must still be blocked):
- Emergency scenarios → emergency_scenario
- Personal treatment advice without clinical data → medical_advice_request
- Completely non-medical content → non_medical

ALLOW THROUGH (classify as clinical_documentation):
- Any lab values, test results, medical records
- Any medical questions or educational queries ("what is...", "explain...", "tell me about...")
- Any symptoms or medical conditions described
- When in doubt about medical content → clinical_documentation

Return ONLY valid JSON:
{{
  "category": "one_of_the_above",
  "reason": "brief explanation"
}}

Classify this input:
{text}"""

    # ---------------------------------------------------------
    # Single model call (no retry logic here)
    # ---------------------------------------------------------
    def _call_model(self, prompt: str, model_id: str) -> Dict:
        response = self.client.converse(
            modelId=model_id,
            messages=[{
                "role": "user",
                "content": [{"text": prompt}]
            }],
            inferenceConfig={
                "maxTokens": 200,
                "temperature": 0.0
            }
        )
        output_text = response["output"]["message"]["content"][0]["text"].strip()
        if output_text.startswith("```"):
            output_text = output_text.replace("```json", "").replace("```", "").strip()
        return json.loads(output_text)

    # ---------------------------------------------------------
    # Retry wrapper with exponential backoff + model fallback
    # ---------------------------------------------------------
    def _call_with_resilience(self, prompt: str) -> Dict:
        """
        Attempt primary model (Nova Micro) with exponential backoff.
        On persistent failure, automatically fall back to Nova Lite.
        """
        last_error = None

        # --- Primary model attempts ---
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"Safety classifier: attempt {attempt + 1}/{MAX_RETRIES} "
                            f"using {self.primary_model}")
                return self._call_model(prompt, self.primary_model)
            except Exception as e:
                last_error = e
                logger.warning(f"Primary model attempt {attempt + 1} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)

        # --- Fallback model (single attempt) ---
        logger.warning(
            f"Primary model exhausted after {MAX_RETRIES} attempts. "
            f"Falling back to {self.fallback_model}"
        )
        try:
            result = self._call_model(prompt, self.fallback_model)
            result["_fallback_used"] = True
            return result
        except Exception as e:
            raise RuntimeError(
                f"Both primary ({self.primary_model}) and fallback "
                f"({self.fallback_model}) models failed. "
                f"Last primary error: {last_error}. Fallback error: {e}"
            )

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------
    def evaluate(self, text: str) -> Dict:
        """
        Classifies input into strict enum category.
        Only 'clinical_documentation' is permitted to proceed.

        Resilience:
        - 3 retries with exponential backoff on primary model
        - Automatic fallback to Nova Lite if primary exhausted
        - Always fails safe (blocks) on unrecoverable errors
        """
        if not text or not isinstance(text, str):
            return {
                "allowed": False,
                "category": "invalid_input",
                "reason": "Input text is empty or invalid.",
                "model_used": "none",
                "fallback_used": False
            }

        text    = self._sanitize_input(text)
        prompt  = self._build_prompt(text)

        try:
            result       = self._call_with_resilience(prompt)
            category     = result.get("category")
            fallback_used = result.get("_fallback_used", False)

            # Strict enum enforcement
            if category not in ALLOWED_CATEGORIES:
                raise ValueError(f"Unexpected category from model: {category}")

            return {
                "allowed":       category == "clinical_documentation",
                "category":      category,
                "reason":        result.get("reason", ""),
                "model_used":    self.fallback_model if fallback_used else self.primary_model,
                "fallback_used": fallback_used
            }

        except Exception as e:
            logger.error(f"Safety classifier unrecoverable error: {e}")
            # Fail safe — never pass unknown content
            return {
                "allowed":       False,
                "category":      "classification_error",
                "reason":        f"Safety classifier error: {str(e)}",
                "model_used":    "none",
                "fallback_used": False
            }
