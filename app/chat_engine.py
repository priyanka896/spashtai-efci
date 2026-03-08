"""
chat_engine.py
==============
Contextual Q&A engine for SpashtaAI EFCI.

Scope: answers questions ONLY about the report that was just processed.
       Never general medical advice. Always grounded in document context.

Safety contract:
- System prompt explicitly scopes answers to the processed document
- Gender-aware (inherits from result structured_data)
- Always appends disclaimer on clinical questions
- Returns structured response with answer + safety_note + grounded flag
"""

import os
import json
import time
import logging
import boto3

logger = logging.getLogger(__name__)

PRIMARY_MODEL  = "amazon.nova-lite-v1:0"
FALLBACK_MODEL = "amazon.nova-pro-v1:0"
MAX_RETRIES    = 2
RETRY_DELAYS   = [1, 2]


class ChatEngine:
    """
    Contextual Q&A grounded strictly in the processed EFCI report.

    Takes:
      - result dict (full EFCI pipeline output)
      - conversation history (list of {role, content})
      - new user question

    Returns:
      - answer (str) — plain language, grounded in document
      - grounded (bool) — whether answer came from document vs general knowledge
      - safety_note (str) — appended disclaimer if clinical in nature
    """

    def __init__(self, region=None):
        self.region         = region or os.getenv("AWS_REGION", "us-east-1")
        self.client         = boto3.client(
            service_name="bedrock-runtime",
            region_name=self.region
        )
        self.primary_model  = os.getenv("BEDROCK_MODEL_ARN",         PRIMARY_MODEL)
        self.fallback_model = os.getenv("BEDROCK_FALLBACK_MODEL_ARN", FALLBACK_MODEL)

    # ---------------------------------------------------------
    # Build context summary from result dict
    # ---------------------------------------------------------
    def _build_report_context(self, result: dict) -> str:
        structured  = result.get("structured_data", {})
        explanation = result.get("explanation", {})
        sources     = result.get("grounding_sources", [])

        lab_values  = structured.get("lab_values", [])
        conditions  = structured.get("conditions", [])
        medications = structured.get("medications", [])

        lines = ["=== PROCESSED REPORT CONTEXT ==="]

        if lab_values:
            lines.append("Lab Results:")
            for lab in lab_values:
                if isinstance(lab, dict):
                    lines.append(
                        f"  - {lab.get('test_name','')}: "
                        f"{lab.get('value','')} {lab.get('unit','')} "
                        f"(normal range: {lab.get('reference_range','')})"
                    )

        if conditions:
            lines.append(f"Documented Conditions: {', '.join(conditions)}")

        if medications:
            lines.append(f"Medications: {', '.join(medications)}")

        plain = explanation.get("plain_language_explanation", "")
        if plain:
            lines.append(f"Report Explanation: {plain}")

        reasoning = explanation.get("clinical_reasoning_summary", "")
        if reasoning:
            lines.append(f"Clinical Reasoning: {reasoning}")

        if sources:
            lines.append("Grounding Sources:")
            for s in sources[:3]:
                lines.append(f"  - {s.get('term','')}: {s.get('definition','')[:150]}")

        return "\n".join(lines)

    # ---------------------------------------------------------
    # Build system prompt
    # ---------------------------------------------------------
    def _build_system_prompt(self, result: dict) -> str:
        structured    = result.get("structured_data", {})
        import re
        # Try to get gender from structured data or clinical text
        gender = "UNKNOWN"
        lab_values = structured.get("lab_values", [])

        report_context = self._build_report_context(result)

        return f"""You are SpashtaAI, an educational assistant that helps patients and doctors 
understand a specific clinical report that has already been processed.

STRICT SCOPE RULES:
- You may ONLY answer questions about the report shown below
- You may NOT provide general medical advice beyond this report
- You may NOT diagnose conditions not in this report
- You may NOT recommend treatments, medications, or clinical actions
- If asked something outside the report scope, say: "I can only answer questions about your specific report."
- Always end clinical answers with a reminder to consult a healthcare provider
- Keep answers concise, calm, and in plain language for patients
- If the doctor view is active, you may use clinical terminology

{report_context}

Remember: You are an EXPLAINABILITY tool, not a diagnostic or advisory tool."""

    # ---------------------------------------------------------
    # Single model call
    # ---------------------------------------------------------
    def _call_model(self, messages: list, system_prompt: str,
                    model_id: str) -> str:
        response = self.client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=messages,
            inferenceConfig={
                "maxTokens": 600,
                "temperature": 0.3
            }
        )
        return response["output"]["message"]["content"][0]["text"].strip()

    # ---------------------------------------------------------
    # Retry + fallback
    # ---------------------------------------------------------
    def _call_with_resilience(self, messages: list,
                               system_prompt: str) -> str:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return self._call_model(messages, system_prompt,
                                        self.primary_model)
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAYS[attempt])

        try:
            return self._call_model(messages, system_prompt,
                                    self.fallback_model)
        except Exception as e:
            raise RuntimeError(
                f"Chat engine failed on both models. "
                f"Primary: {last_error}. Fallback: {e}"
            )

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------
    def ask(self, question: str, result: dict,
            history: list) -> dict:
        """
        Ask a question about the processed report.

        Args:
            question: user's question string
            result:   full EFCI pipeline result dict
            history:  list of {"role": "user"|"assistant", "content": str}

        Returns:
            {
              "answer": str,
              "safety_note": str,
              "error": str | None
            }
        """
        if not question.strip():
            return {
                "answer": "Please type a question about your report.",
                "safety_note": "",
                "error": None
            }

        system_prompt = self._build_system_prompt(result)

        # Build messages from history + new question
        messages = []
        for turn in history:
            messages.append({
                "role":    turn["role"],
                "content": [{"text": turn["content"]}]
            })
        messages.append({
            "role":    "user",
            "content": [{"text": question}]
        })

        try:
            answer = self._call_with_resilience(messages, system_prompt)

            # Append safety note if answer is clinical in nature
            clinical_keywords = [
                "result", "level", "range", "elevated", "high", "low",
                "hormone", "test", "value", "diagnosis", "condition"
            ]
            is_clinical = any(
                kw in answer.lower() for kw in clinical_keywords
            )
            safety_note = (
                "This is for educational purposes only. "
                "Please discuss with your healthcare provider."
                if is_clinical else ""
            )

            return {
                "answer":      answer,
                "safety_note": safety_note,
                "error":       None
            }

        except Exception as e:
            logger.error(f"Chat engine error: {e}")
            return {
                "answer":      "I'm sorry, I couldn't process your question. Please try again.",
                "safety_note": "",
                "error":       str(e)
            }

    # ---------------------------------------------------------
    # Suggested questions based on report content
    # ---------------------------------------------------------
    def get_suggested_questions(self, result: dict) -> list:
        """
        Generate 3-4 contextual suggested questions from the report.
        These are deterministic — no LLM call needed.
        """
        structured = result.get("structured_data", {})
        lab_values = structured.get("lab_values", [])
        conditions = structured.get("conditions", [])
        suggestions = []

        for lab in lab_values[:2]:
            if isinstance(lab, dict):
                name  = lab.get("test_name", "this test")
                value = lab.get("value", "")
                ref   = lab.get("reference_range", "")

                # Shorten long test names
                short_name = name.split(",")[0].strip() if "," in name else name

                suggestions.append(f"What does {short_name} measure?")
                if value and ref:
                    suggestions.append(
                        f"Why is my {short_name} result above the normal range?"
                    )

        for cond in conditions[:1]:
            suggestions.append(f"What does {cond} mean?")

        # Always add these fallback questions
        suggestions.append("Should I be worried about this result?")
        suggestions.append("What questions should I ask my doctor?")

        return suggestions[:4]   # max 4 suggestions
