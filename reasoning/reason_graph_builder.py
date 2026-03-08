from typing import Dict, List


class ReasonGraphBuilder:
    """
    Builds a Reason Graph that explains *why* certain pieces of information
    appear in the clinical documentation.

    Explainability-only relationships. No diagnosis or treatment inference.
    """

    def __init__(self):
        pass

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------
    def build(self, structured_data: Dict, retrieved_entries: List[Dict], llm_output: Dict) -> Dict:

        nodes = self._build_nodes(structured_data)
        edges = self._build_edges(structured_data, retrieved_entries, llm_output)

        return {
            "nodes": nodes,
            "edges": edges
        }

    # ---------------------------------------------------------
    # Node Construction
    # ---------------------------------------------------------
    def _build_nodes(self, structured_data: Dict) -> List[Dict]:

        nodes = []

        def add_nodes(items, item_type):
            for idx, item in enumerate(items):
                nodes.append({
                    "id": f"{item_type}_{idx}",
                    "label": item,
                    "type": item_type
                })

        add_nodes(structured_data.get("conditions", []), "condition")
        add_nodes(structured_data.get("lab_values", []), "lab_value")
        add_nodes(structured_data.get("medications", []), "medication")
        add_nodes(structured_data.get("follow_up_actions", []), "action")

        return nodes

    # ---------------------------------------------------------
    # Edge Construction
    # ---------------------------------------------------------
    def _build_edges(
        self,
        structured_data: Dict,
        retrieved_entries: List[Dict],
        llm_output: Dict
    ) -> List[Dict]:

        edges = []
        reasoning_text = llm_output.get("clinical_reasoning_summary", "")

        # lab → condition relationships
        for c_idx, condition in enumerate(structured_data.get("conditions", [])):
            for l_idx, lab in enumerate(structured_data.get("lab_values", [])):
                justification = self._find_grounding_justification(
                    condition,
                    lab,
                    retrieved_entries,
                    reasoning_text
                )
                edges.append({
                    "source": f"lab_value_{l_idx}",
                    "target": f"condition_{c_idx}",
                    "relationship": "supports_context",
                    "justification": justification
                })

        # medication → condition contextual relationships
        for c_idx, condition in enumerate(structured_data.get("conditions", [])):
            for m_idx, med in enumerate(structured_data.get("medications", [])):
                edges.append({
                    "source": f"medication_{m_idx}",
                    "target": f"condition_{c_idx}",
                    "relationship": "contextual_reference",
                    "justification": f"The documentation references '{med}' in relation to '{condition}' for contextual explanation."
                })

        return edges

    # ---------------------------------------------------------
    # Grounding Justification
    # ---------------------------------------------------------
    def _find_grounding_justification(
        self,
        condition: str,
        lab: str,
        retrieved_entries: List[Dict],
        reasoning_text: str
    ) -> str:

        for entry in retrieved_entries:
            snippet = (entry.get("definition") or "").lower()
            if condition.lower() in snippet or lab.lower() in snippet:
                return f"Grounded in glossary definition: {entry.get('definition', '')}"

        if condition.lower() in reasoning_text.lower():
            return f"The reasoning summary references '{condition}' in context."

        return "This relationship is derived from structural context in the document."