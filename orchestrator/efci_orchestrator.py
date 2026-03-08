from typing import Dict

from safety.safety_classifier import SafetyClassifier
from llm.bedrock_client import BedrockClient
from reasoning.confidence_calibrator import ConfidenceCalibrator
from reasoning.reason_graph_builder import ReasonGraphBuilder
from governance.audit_logger import AuditLogger


class EFCIOrchestrator:
    """
    Central coordination layer for EFCI.

    Responsible for:
    - Input safety validation
    - Structured explainability extraction
    - RAG-based grounding
    - Confidence calibration
    - Reason graph generation
    - Audit logging
    """

    def __init__(self):
        self.safety = SafetyClassifier()
        self.explainer = BedrockClient()
        self.calibrator = ConfidenceCalibrator()
        self.graph_builder = ReasonGraphBuilder()
        self.audit_logger = AuditLogger()

    # ---------------------------------------------------------
    # Main Entry Point
    # ---------------------------------------------------------
    def process(self, clinical_text: str) -> Dict:

        # -----------------------------------------------------
        # 1️⃣ Input Safety Gate
        # -----------------------------------------------------
        safety_result = self.safety.evaluate(clinical_text)

        if not safety_result["allowed"]:

            self.audit_logger.log_pipeline_run(
                user_input=clinical_text,
                safety_classifier=safety_result,
                explainability_summary={},
                confidence_summary={},
                safety_monitor={"status": "blocked_at_input"},
                orchestrator_action="blocked_by_safety_classifier"
            )

            return {
                "status": "blocked",
                "safety": safety_result,
                "structured_data": {},
                "grounding_sources": [],
                "explanation": {},
                "confidence": {},
                "reason_graph": {}
            }

        # -----------------------------------------------------
        # 2️⃣ Explainability + Grounding
        # -----------------------------------------------------
        try:
            explainability_result = self.explainer.extract_structured_concepts(
                clinical_text
            )
        except Exception as e:

            self.audit_logger.log_pipeline_run(
                user_input=clinical_text,
                safety_classifier=safety_result,
                explainability_summary={"error": str(e)},
                confidence_summary={},
                safety_monitor={"status": "skipped_due_to_error"},
                orchestrator_action="bedrock_failure"
            )

            return {
                "status": "error",
                "message": str(e),
                "safety": safety_result,
                "structured_data": {},
                "grounding_sources": [],
                "explanation": {},
                "confidence": {},
                "reason_graph": {}
            }

        structured_data = explainability_result.get("structured_data", {})
        retrieved_entries = explainability_result.get("retrieved_entries", [])
        llm_output = explainability_result.get("llm_output", {})

        # -----------------------------------------------------
        # 3️⃣ Confidence Calibration
        # -----------------------------------------------------
        confidence_package = self.calibrator.calibrate(
            structured_data=structured_data,
            retrieved_entries=retrieved_entries,
            llm_output=llm_output
        )

        # -----------------------------------------------------
        # 4️⃣ Reason Graph Generation (NEW ENHANCEMENT)
        # -----------------------------------------------------
        reason_graph = self.graph_builder.build(
            structured_data=structured_data,
            retrieved_entries=retrieved_entries,
            llm_output=llm_output
        )

        # -----------------------------------------------------
        # 5️⃣ Audit Log (Successful Run)
        # -----------------------------------------------------
        self.audit_logger.log_pipeline_run(
            user_input=clinical_text,
            safety_classifier=safety_result,
            explainability_summary=llm_output,
            confidence_summary=confidence_package,
            safety_monitor={"status": "passed"},
            orchestrator_action="processed_successfully"
        )

        # -----------------------------------------------------
        # 6️⃣ Final Structured Response
        # -----------------------------------------------------
        return {
            "status": "success",
            "safety": safety_result,
            "structured_data": structured_data,
            "grounding_sources": retrieved_entries,
            "explanation": llm_output,
            "confidence": confidence_package,
            "reason_graph": reason_graph
        }