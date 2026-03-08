import json
import hashlib
import os
from datetime import datetime
from typing import Dict, Any


class AuditLogger:
    """
    Audit Logger for EFCI.

    Logs:
    - safety classifier decisions
    - final output safety decisions
    - orchestrator actions
    - hashes of input/output (never raw text)
    - confidence calibration summary

    Stored as JSON Lines (JSONL).
    """

    def __init__(self, log_dir: str = "logs", filename: str = "audit_log.jsonl"):
        self.log_dir = log_dir
        self.file_path = os.path.join(log_dir, filename)

        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

    # ---------------------------------------------------------
    # Hash Helper (no PHI stored)
    # ---------------------------------------------------------
    @staticmethod
    def _hash_value(value: str) -> str:
        if not value:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------
    # Base Log Structure
    # ---------------------------------------------------------
    def _base_entry(self) -> Dict[str, Any]:
        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "system": "EFCI",
            "version": "1.0",
            "event_type": "pipeline_execution"
        }

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------
    def log_pipeline_run(
        self,
        user_input: str,
        safety_classifier: Dict,
        explainability_summary: Dict,
        confidence_summary: Dict,
        safety_monitor: Dict,
        orchestrator_action: str
    ):

        entry = self._base_entry()

        entry.update({
            "input_hash": self._hash_value(user_input),
            "output_hash": self._hash_value(
                json.dumps(explainability_summary, sort_keys=True)
            ),
            "safety_classifier": safety_classifier,
            "explainability": explainability_summary,
            "confidence": confidence_summary,
            "safety_monitor": safety_monitor,
            "orchestrator_action": orchestrator_action
        })

        self._write(entry)

    # ---------------------------------------------------------
    # Write to JSONL
    # ---------------------------------------------------------
    def _write(self, entry: Dict[str, Any]):
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")