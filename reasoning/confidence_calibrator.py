from typing import Dict, List


class ConfidenceCalibrator:
    """
    Confidence Calibrator for EFCI.
    
    Produces:
    - Qualitative confidence bands (High / Moderate / Low)
    - Normalized integer scores for orchestrator fusion
    - Overall confidence band
    - Human-readable uncertainty summary
    
    This module is intentionally rule-based—no probabilities,
    no numeric clinical scoring—to align with explainability-only
    and safety-by-design requirements.
    """

    # Markers that suggest uncertainty in LLM reasoning text
    UNCERTAINTY_MARKERS = [
        "may", "might", "possible", "possibly",
        "suggests", "unclear", "likely", "could"
    ]

    # Mapping qualitative bands to normalized scores
    SCORE_MAP = {
        "High": 3,
        "Moderate": 2,
        "Low": 1
    }

    def __init__(self):
        pass

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------
    def calibrate(
        self,
        structured_data: Dict,
        retrieved_entries: List[Dict],
        llm_output: Dict
    ) -> Dict:
        """
        Returns a structured confidence package that the orchestrator
        can consume and pass to the UI + Reason Graph layer.
        """

        structured_data = structured_data or {}
        retrieved_entries = retrieved_entries or []
        llm_output = llm_output or {}

        # Step 1: Compute sub-confidence bands
        extraction_band = self._evaluate_extraction(structured_data)
        grounding_band = self._evaluate_grounding(structured_data, retrieved_entries)
        reasoning_band = self._evaluate_reasoning(llm_output)

        # Step 2: Convert bands to numeric scores for fusion logic
        extraction_score = self.SCORE_MAP[extraction_band]
        grounding_score = self.SCORE_MAP[grounding_band]
        reasoning_score = self.SCORE_MAP[reasoning_band]

        # Step 3: Compute overall confidence band
        overall_band = self._derive_overall_band(
            extraction_score,
            grounding_score,
            reasoning_score
        )

        # Step 4: Uncertainty notes for UI & audit
        uncertainty_notes = self._generate_uncertainty_notes(
            extraction_band,
            grounding_band,
            reasoning_band
        )

        # Final packaged output
        return {
            "bands": {
                "extraction": {
                    "label": extraction_band,
                    "score": extraction_score
                },
                "grounding": {
                    "label": grounding_band,
                    "score": grounding_score
                },
                "reasoning": {
                    "label": reasoning_band,
                    "score": reasoning_score
                }
            },
            "overall_confidence": overall_band,
            "uncertainty_notes": uncertainty_notes
        }

    # ---------------------------------------------------------
    # Extraction Confidence
    # ---------------------------------------------------------
    def _evaluate_extraction(self, structured_data: Dict) -> str:
        """Estimates completeness of structured extraction.
        
        A pure lab report (lots of lab_values, no conditions/meds) 
        should still score High if lab_values is well-populated.
        """
        lab_values = structured_data.get("lab_values") or []
        conditions = structured_data.get("conditions") or []
        medications = structured_data.get("medications") or []
        follow_up = structured_data.get("follow_up_actions") or []

        # Lab-heavy reports (kidney panel, blood panel etc)
        if len(lab_values) >= 3:
            return "High"
        if len(lab_values) >= 1:
            return "Moderate"

        # Non-lab reports — conditions/meds based
        populated = sum(1 for f in [conditions, medications, follow_up] if f)
        if populated >= 2:
            return "High"
        if populated == 1:
            return "Moderate"
        return "Low"

    # ---------------------------------------------------------
    # Grounding Confidence
    # ---------------------------------------------------------
    def _evaluate_grounding(self, structured_data: Dict, retrieved_entries: List[Dict]) -> str:
        """Evaluates grounding strength using public guideline retrieval."""

        lab_values = structured_data.get("lab_values") or []
        conditions = structured_data.get("conditions") or []
        medications = structured_data.get("medications") or []
        extracted_terms = lab_values + conditions + medications

        if not extracted_terms:
            return "Low"

        # For lab panels: at least 1 retrieved entry per 3 tests is reasonable
        n_tests = max(len(lab_values), 1)
        ratio = len(retrieved_entries) / n_tests

        if len(retrieved_entries) >= 3 or ratio >= 0.5:
            return "High"
        if len(retrieved_entries) >= 1:
            return "Moderate"
        return "Low"

    # ---------------------------------------------------------
    # Reasoning Confidence
    # ---------------------------------------------------------
    def _evaluate_reasoning(self, llm_output: Dict) -> str:
        """Checks reasoning summary for uncertainty markers."""

        summary = llm_output.get("clinical_reasoning_summary", "").lower()

        if any(marker in summary for marker in self.UNCERTAINTY_MARKERS):
            return "Moderate"
        if summary:
            return "High"
        return "Low"

    # ---------------------------------------------------------
    # Overall Band Fusion Logic
    # ---------------------------------------------------------
    def _derive_overall_band(self, e_score: int, g_score: int, r_score: int) -> str:
        """
        Weighted fusion: extraction + grounding weighted higher than reasoning.
        Reasoning uses hedging language naturally so penalising it too hard
        causes unfairly Low scores on well-grounded lab reports.
        """
        # Extraction and grounding carry more weight than reasoning language
        weighted = (e_score * 1.5 + g_score * 1.5 + r_score * 1.0) / 4.0

        if weighted >= 2.4:
            return "High"
        if weighted >= 1.5:
            return "Moderate"
        return "Low"

    # ---------------------------------------------------------
    # Human-Readable Uncertainty Notes
    # ---------------------------------------------------------
    def _generate_uncertainty_notes(self, e_band: str, g_band: str, r_band: str) -> str:

        notes = []

        if e_band == "Low":
            notes.append("Structured extraction coverage is limited.")
        if g_band == "Low":
            notes.append("Glossary grounding was insufficient for extracted concepts.")
        if r_band == "Moderate":
            notes.append("Reasoning summary contains uncertainty indicators.")

        return (
            " ".join(notes)
            if notes else
            "No significant uncertainty detected."
        )
