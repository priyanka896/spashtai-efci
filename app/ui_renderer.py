"""
ui_renderer.py
==============
Rendering layer for SpashtaAI EFCI.

Two cognitive layers:
  Layer 1 — Patient View   (default, plain language, zero jargon)
  Layer 2 — Clinical View  (doctor toggle, structured + narrative)

Root cause fixes applied:
  - All HTML built via explicit st.markdown() calls, never as f-string returns
  - Sentence splitting uses ". " not "." to avoid breaking decimals like 11.00
  - Confidence breakdown uses st.columns(), not HTML string generation
  - Sources use individual st.markdown() calls per field, not one giant HTML string
  - Reason Graph for doctors is a plain English narrative, not node/edge jargon
  - "Grounding Sources" renamed to "Medical References" throughout
  - Textract mention removed from spinner

Backend contract consumed (read-only):
  result["structured_data"]   → conditions, lab_values, medications, follow_up_actions
  result["explanation"]       → clinical_reasoning_summary, plain_language_explanation
  result["confidence"]        → overall_confidence, bands, uncertainty_notes
  result["grounding_sources"] → [{term, definition, source_url, confidence_band, retrieval_score}]
  result["reason_graph"]      → {nodes: [...], edges: [...]}
"""

import streamlit as st

# ─────────────────────────────────────────────────────────────
# Palette
# ─────────────────────────────────────────────────────────────
C = {
    "High":     "#2ea043",
    "Moderate": "#d29922",
    "Low":      "#da3633",
    "neutral":  "#8b949e",
    "bg":       "#161b22",
    "border":   "#30363d",
    "accent":   "#ff9900",
    "text":     "#e6edf3",
    "subtext":  "#c9d1d9",
}


# ─────────────────────────────────────────────────────────────
# Primitive helpers  — each calls st.markdown directly
# ─────────────────────────────────────────────────────────────

def _section_header(icon: str, title: str):
    st.markdown(
        f"<h4 style='color:{C['text']}; margin:20px 0 6px;'>{icon} {title}</h4>",
        unsafe_allow_html=True,
    )


def _divider():
    st.markdown(
        f"<hr style='border:1px solid {C['border']}; margin:20px 0;'>",
        unsafe_allow_html=True,
    )


def _card_open(border_color: str = None):
    bc = border_color or C["border"]
    st.markdown(
        f"<div style='background:{C['bg']}; border:1px solid {bc}; "
        f"border-radius:10px; padding:18px; margin-bottom:14px;'>",
        unsafe_allow_html=True,
    )


def _card_close():
    st.markdown("</div>", unsafe_allow_html=True)


def _html(markup: str):
    st.markdown(markup, unsafe_allow_html=True)


def _conf_color(band: str) -> str:
    return C.get(band, C["neutral"])


def _humanize_confidence(overall: str) -> str:
    return {
        "High":     "The system is highly confident in this explanation.",
        "Moderate": "The system is reasonably confident in this explanation.",
        "Low":      "This explanation has limited supporting evidence — please consult a professional.",
    }.get(overall, "Confidence level undetermined.")


def _label_str(label) -> str:
    """Convert a node label (str or dict) to a clean display string."""
    if isinstance(label, str):
        return label
    if isinstance(label, dict):
        for key in ["test_name", "name", "value", "term", "condition"]:
            if key in label:
                parts = [str(label[key])]
                if label.get("unit"):
                    parts.append(label["unit"])
                if label.get("reference_range"):
                    parts.append(f"(ref: {label['reference_range']})")
                return " ".join(parts)
        return str(label)
    return str(label)


def _split_sentences(text: str):
    """Split on '. ' to avoid breaking decimals like 11.00 mIU/mL."""
    import re
    parts = re.split(r'(?<=[a-zA-Z])\.\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


# ─────────────────────────────────────────────────────────────
# LAYER 1 — PATIENT VIEW
# ─────────────────────────────────────────────────────────────

def render_patient_view(result: dict):
    """Plain language. No scores. No jargon. Reduce anxiety, build trust."""

    structured  = result.get("structured_data", {})
    explanation = result.get("explanation", {})
    confidence  = result.get("confidence", {})
    sources     = result.get("grounding_sources", [])

    overall     = confidence.get("overall_confidence", "Moderate")
    conf_color  = _conf_color(overall)

    # ── 1. Your Report Summary ────────────────────────────────
    _section_header("🧾", "Your Report Summary")

    lab_values = structured.get("lab_values", [])
    conditions = structured.get("conditions", [])

    if lab_values:
        for lab in lab_values:
            if not isinstance(lab, dict):
                continue

            test_raw = lab.get("test_name", "Lab Test")
            # Clean ugly comma-separated Textract names for patient display
            # "HCG,BETA,TOTAL, PREGNANCY, SERUM" → "HCG"
            if "," in test_raw:
                test = test_raw.split(",")[0].strip()
            else:
                test = test_raw
            value = lab.get("value", "—")
            unit  = lab.get("unit", "")
            ref   = lab.get("reference_range", "—")

            # Determine status badge
            # Handles: parentheticals "(CMIA)", zero values, "normal" text, ranges "3.5-5.0"
            import re as _re
            def _clean_num(s):
                s = _re.sub(r'\(.*?\)', '', str(s))
                s = s.replace("<","").replace(">","").strip()
                return float(s)
            def _get_status(value, ref):
                ref_str = str(ref).strip()
                val_str = str(value).strip().lower()
                # Text values like "normal", "no", "negative"
                NORMAL_WORDS = {"normal", "no", "negative", "absent", "none"}
                ABNORMAL_WORDS = {"abnormal", "yes", "positive", "present"}
                if val_str in NORMAL_WORDS:
                    return "Within normal range", C["High"]
                if val_str in ABNORMAL_WORDS:
                    return "Outside normal range", COLORS_STATUS("above")
                # Range format "3.5-5.0" or "3.5 - 5.0"
                range_match = _re.match(r'([\d.]+)\s*[-–]\s*([\d.]+)', ref_str)
                if range_match:
                    try:
                        lo, hi = float(range_match.group(1)), float(range_match.group(2))
                        v = _clean_num(value)
                        if v < lo:
                            return "Below the typical reference range", C["Moderate"]
                        if v > hi:
                            return "Above the typical reference range", COLORS_STATUS("above")
                        return "Within reference range", C["High"]
                    except Exception:
                        pass
                # Less-than ref  e.g. "<5.00"
                if "<" in ref_str:
                    try:
                        v = _clean_num(value)
                        r = _clean_num(ref_str)
                        if v == 0.0 and r > 0:
                            return "Below detectable range", C["Moderate"]
                        return ("Above the typical reference range", COLORS_STATUS("above")) if v >= r else ("Within reference range", C["High"])
                    except Exception:
                        pass
                # Greater-than ref  e.g. ">60"
                if ">" in ref_str:
                    try:
                        v = _clean_num(value)
                        r = _clean_num(ref_str)
                        return ("Below the typical reference range", C["Moderate"]) if v <= r else ("Within reference range", C["High"])
                    except Exception:
                        pass
                # Plain numeric ref — compare directly
                try:
                    v = _clean_num(value)
                    r = _clean_num(ref_str)
                    if v == 0.0 and r > 0:
                        return "Below detectable range", C["Moderate"]
                    return ("Above the typical reference range", COLORS_STATUS("above")) if v > r else ("Within reference range", C["High"])
                except Exception:
                    pass
                return "See reference range", C["neutral"]
            try:
                status_text, status_color = _get_status(value, ref)
            except Exception:
                status_text, status_color = "See reference range", C["neutral"]

            # Render as individual markdown blocks inside a visual card
            # Left border colour = status colour — instant visual scan
            _html(
                f"<div style='background:{C['bg']}; border:1px solid {C['border']}; "
                f"border-left:4px solid {status_color}; "
                f"border-radius:10px; padding:20px; margin-bottom:14px;'>"
                f"<div style='font-size:11px; color:{C['neutral']}; text-transform:uppercase; "
                f"letter-spacing:1px; margin-bottom:6px;'>Test Finding</div>"
                f"<div style='font-size:20px; font-weight:700; color:{C['text']}; margin-bottom:14px;'>"
                f"{test}</div>"
                f"<div style='display:flex; gap:36px; flex-wrap:wrap; align-items:flex-end;'>"
                f"  <div>"
                f"    <div style='font-size:11px; color:{C['neutral']}; margin-bottom:2px;'>Your Result</div>"
                f"    <div style='font-size:26px; font-weight:700; color:{C['text']};'>{value}"
                f"      <span style='font-size:14px; color:{C['neutral']}; margin-left:4px;'>{unit}</span>"
                f"    </div>"
                f"  </div>"
                f"  <div>"
                f"    <div style='font-size:11px; color:{C['neutral']}; margin-bottom:2px;'>Normal Range</div>"
                f"    <div style='font-size:18px; color:{C['neutral']};'>{ref} {unit}</div>"
                f"  </div>"
                f"  <div style='display:flex; align-items:center;'>"
                f"    <span style='background:{status_color}22; color:{status_color}; "
                f"border:1px solid {status_color}66; border-radius:20px; padding:5px 16px; "
                f"font-size:13px; font-weight:600;'>{status_text}</span>"
                f"  </div>"
                f"</div>"
                f"</div>"
            )

    elif conditions:
        for cond in conditions:
            _html(
                f"<div style='background:{C['bg']}; border:1px solid {C['border']}; "
                f"border-radius:10px; padding:18px; margin-bottom:14px;'>"
                f"<div style='font-size:11px; color:{C['neutral']}; text-transform:uppercase; "
                f"margin-bottom:4px;'>Documented Condition</div>"
                f"<div style='font-size:20px; font-weight:700; color:{C['text']};'>{cond}</div>"
                f"</div>"
            )
    else:
        _html(
            f"<div style='background:{C['bg']}; border:1px solid {C['border']}; "
            f"border-radius:10px; padding:18px; color:{C['neutral']};'>"
            f"No specific lab findings or conditions were documented in this report.</div>"
        )

    # ── 2. What This Means ───────────────────────────────────
    _divider()
    _section_header("💬", "What This Means")

    plain = (explanation.get("plain_language_explanation") or
             explanation.get("clinical_reasoning_summary") or "")

    if plain:
        sentences = _split_sentences(plain)
        sentence_html = "".join(
            f"<p style='margin:0 0 10px; line-height:1.75; color:{C['subtext']}; font-size:15px;'>{s}.</p>"
            for s in sentences
        )
        _html(
            f"<div style='border-left:3px solid {C['accent']}; padding:16px 20px; "
            f"background:{C['bg']}; border-radius:0 8px 8px 0; margin-bottom:14px;'>"
            f"{sentence_html}"
            f"</div>"
        )
    else:
        st.info("No explanation was generated for this input.")

    # ── 3. Should I Be Concerned? ────────────────────────────
    _divider()
    _section_header("🤔", "Should I Be Concerned?")

    # Count how many labs are actually flagged as abnormal
    abnormal_labs = []
    normal_labs   = []
    for lab in lab_values:
        if not isinstance(lab, dict):
            continue
        v = str(lab.get("value","")).strip().lower()
        r = str(lab.get("reference_range","")).strip()
        import re as _re2
        NORMAL_WORDS = {"normal","no","negative","absent","none"}
        if v in NORMAL_WORDS:
            normal_labs.append(lab.get("test_name","test"))
            continue
        # Try numeric compare
        try:
            def _cn(s):
                s = _re2.sub(r'\(.*?\)', '', str(s))
                return float(s.replace("<","").replace(">","").strip())
            nv = _cn(v)
            range_m = _re2.match(r'([\d.]+)\s*[-–]\s*([\d.]+)', r)
            if range_m:
                lo,hi = float(range_m.group(1)), float(range_m.group(2))
                if nv < lo or nv > hi:
                    abnormal_labs.append(lab.get("test_name","test"))
                else:
                    normal_labs.append(lab.get("test_name","test"))
            elif "<" in r:
                abnormal_labs.append(lab.get("test_name","test")) if nv >= _cn(r) else normal_labs.append(lab.get("test_name","test"))
            elif ">" in r:
                abnormal_labs.append(lab.get("test_name","test")) if nv <= _cn(r) else normal_labs.append(lab.get("test_name","test"))
        except Exception:
            pass  # no reference range — skip

    has_data = bool(lab_values or conditions)

    # Also check plain language explanation for concern indicators
    # This catches cases where LLM flags issues but no numeric ref range exists
    plain_explanation = explanation.get("plain_language_explanation", "").lower()
    CONCERN_PHRASES = [
        "not filtering", "impairment", "acidemia", "acidosis", "abnormal",
        "below normal", "above normal", "elevated", "low", "high",
        "might suggest", "may indicate", "significant", "potential issue",
        "problem with", "outside", "slightly", "concern"
    ]
    explanation_flags_concern = any(p in plain_explanation for p in CONCERN_PHRASES)

    if not has_data:
        msg_color = C["neutral"]
        msg = "No specific findings were extracted from your document."
    elif abnormal_labs:
        msg_color = C["Moderate"]
        def _cln(n): return n.split(",")[0].strip() if "," in n else n
        flagged = ", ".join(_cln(n) for n in abnormal_labs[:3])
        msg = (
            f"One or more of your results ({flagged}) appear to be outside the typical reference range. "
            f"This is for educational purposes only. "
            f"Please speak with your doctor or healthcare provider to understand what this means for you."
        )
    elif explanation_flags_concern:
        msg_color = C["Moderate"]
        msg = (
            "Some of your results may warrant attention based on clinical context. "
            "Reference ranges were not available in your document for direct comparison. "
            "Please speak with your healthcare provider for a full interpretation."
        )
    else:
        msg_color = C["High"]
        msg = (
            "Based on the available information, your results appear to be within normal limits. "
            "Always confirm with your healthcare provider, as reference ranges can vary."
        )

    _html(
        f"<div style='background:{C['bg']}; border:1px solid {msg_color}; "
        f"border-radius:10px; padding:18px; margin-bottom:14px;'>"
        f"<div style='font-size:15px; color:{msg_color}; line-height:1.7; margin-bottom:12px;'>{msg}</div>"
        f"<div style='border-top:1px solid {C['border']}; padding-top:10px; "
        f"font-size:13px; color:{C['neutral']};'>"
        f"⚠️ SpashtaAI does not provide medical advice, diagnosis, or treatment. "
        f"Always consult a qualified healthcare provider."
        f"</div>"
        f"</div>"
    )

    # ── 4. Confidence ────────────────────────────────────────
    _divider()
    _section_header("📊", "How Confident Is This Explanation?")

    emoji = {"High": "🟢", "Moderate": "🟡", "Low": "🔴"}.get(overall, "⚪")
    
    # Patient-friendly confidence messages
    patient_conf_msg = {
        "High": (
            "This explanation is based on clear, well-documented results supported by "
            "published medical guidelines. You can feel confident sharing this with your doctor."
        ),
        "Moderate": (
            "This explanation is based on the available results, but some details may need "
            "verification. We recommend discussing this with your healthcare provider for a complete picture."
        ),
        "Low": (
            "The document had limited information for us to work with. "
            "Please do not rely on this explanation alone — speak with your doctor."
        ),
    }.get(overall, "Confidence level could not be determined. Please consult your healthcare provider.")

    _html(
        f"<div style='background:{C['bg']}; border:1px solid {conf_color}; "
        f"border-radius:10px; padding:18px; display:flex; align-items:center; gap:16px;'>"
        f"<div style='font-size:32px;'>{emoji}</div>"
        f"<div style='font-size:15px; color:{C['subtext']}; line-height:1.7;'>{patient_conf_msg}</div>"
        f"</div>"
    )

    # ── 5. Information Sources ───────────────────────────────
    if sources:
        _divider()
        _section_header("📚", "Information Sources")
        _html(
            f"<div style='font-size:13px; color:{C['neutral']}; margin-bottom:12px;'>"
            f"This explanation is grounded in publicly available medical guidelines.</div>"
        )

        seen_urls  = set()
        seen_terms = set()
        for src in sources[:8]:   # scan more, show max 4 unique
            url       = src.get("source_url", "")
            term_raw  = src.get("term", "Medical Reference").strip()
            term_key  = term_raw.lower()
            # Display term in Title Case
            # title() breaks acronyms: 'BUN' → 'Bun', 'eGFR' → 'Egfr'
            # Use a smarter capitalizer: preserve ALL-CAPS words and known acronyms
            MEDICAL_ACRONYMS = {
                'bun', 'egfr', 'hcg', 'rbc', 'wbc', 'hba1c', 'ldl', 'hdl',
                'tsh', 't3', 't4', 'alt', 'ast', 'gfr', 'pth', 'psa', 'crp',
                'esr', 'inr', 'pt', 'aptt', 'mcv', 'mch', 'mchc', 'mpv',
                'ph', 'bp', 'bmi', 'ecg', 'ekg', 'mri', 'cbc', 'ct', 'pcr'
            }
            MIXED_CASE_MAP = {
                'egfr': 'eGFR',
                'hba1c': 'HbA1c',
                'ph': 'pH',
            }
            def _smart_title(s):
                if ',' in s:
                    parts = [p.strip() for p in s.split(',')]
                    cleaned = []
                    for p in parts:
                        ws = p.split()
                        wr = []
                        for w in ws:
                            if w.lower() in MIXED_CASE_MAP:
                                wr.append(MIXED_CASE_MAP[w.lower()])
                            elif w.lower() in MEDICAL_ACRONYMS or (w.isupper() and len(w) <= 6):
                                wr.append(w.upper())
                            else:
                                wr.append(w.capitalize())
                        cleaned.append(' '.join(wr))
                    return ', '.join(cleaned)
                words = s.split()
                result = []
                for w in words:
                    if w.lower() in MIXED_CASE_MAP:
                        result.append(MIXED_CASE_MAP[w.lower()])
                    elif w.lower() in MEDICAL_ACRONYMS:
                        result.append(w.upper())
                    elif w.isupper() and len(w) <= 6:
                        result.append(w)
                    else:
                        result.append(w.capitalize())
                return ' '.join(result)
            # For comma-separated raw lab names, just use first token
            if "," in term_raw:
                first_tok = term_raw.split(",")[0].strip()
                term = first_tok.upper() if len(first_tok) <= 6 else first_tok.title()
            else:
                term = _smart_title(term_raw)
            defn      = src.get("definition", "")

            # Skip if same URL or same term already shown
            if url in seen_urls or term_key in seen_terms:
                continue
            seen_urls.add(url)
            seen_terms.add(term_key)

            # Stop after 4 unique sources displayed
            if len(seen_terms) > 4:
                break

            short_def = (defn[:130] + "…") if len(defn) > 130 else defn
            link_html = (
                f"<a href='{url}' target='_blank' style='font-size:12px; color:{C['accent']}; "
                f"text-decoration:none;'>View Source ↗</a>"
                if url.startswith("http") else ""
            )

            _html(
                f"<div style='background:{C['bg']}; border:1px solid {C['border']}; "
                f"border-radius:10px; padding:16px; margin-bottom:10px;'>"
                f"<div style='display:flex; justify-content:space-between; align-items:flex-start;'>"
                f"  <div style='font-size:14px; font-weight:600; color:{C['text']}; margin-bottom:6px;'>"
                f"    📖 {term}"
                f"  </div>"
                f"  {link_html}"
                f"</div>"
                f"<div style='font-size:13px; color:{C['neutral']}; line-height:1.6;'>{short_def}</div>"
                f"</div>"
            )


# ─────────────────────────────────────────────────────────────
# LAYER 2 — CLINICAL VIEW
# ─────────────────────────────────────────────────────────────

def render_clinical_view(result: dict):
    """
    Structured clinical detail for doctors.
    Tables not JSON. Plain English reason narrative. No jargon labels.
    """

    structured  = result.get("structured_data", {})
    explanation = result.get("explanation", {})
    confidence  = result.get("confidence", {})
    sources     = result.get("grounding_sources", [])
    graph       = result.get("reason_graph", {})

    _section_header("🔬", "Clinical Detail View")
    _html(
        f"<div style='font-size:13px; color:{C['neutral']}; margin-bottom:16px;'>"
        f"Structured extraction from the clinical document. "
        f"No inferences made beyond what is explicitly documented.</div>"
    )

    col_left, col_right = st.columns([1, 1])

    # ── Left column: extracted data ───────────────────────────
    with col_left:

        # Lab values as a styled HTML table — st.table() is too dim on dark themes
        lab_values = structured.get("lab_values", [])
        _html(
            f"<div style='font-size:14px; font-weight:700; color:{C['text']}; "
            f"margin-bottom:10px;'>🧪 Lab Values</div>"
        )
        if lab_values:
            rows = []
            for lab in lab_values:
                if isinstance(lab, dict):
                    rows.append({
                        "test":  lab.get("test_name", "—"),
                        "value": f"{lab.get('value','—')} {lab.get('unit','')}".strip(),
                        "ref":   lab.get("reference_range", "—") or "—",
                    })
                elif isinstance(lab, str):
                    rows.append({"test": lab, "value": "—", "ref": "—"})
            if rows:
                # Build HTML table manually for full colour control
                th_style = (
                    f"padding:8px 14px; text-align:left; font-size:12px; "
                    f"font-weight:600; color:{C['neutral']}; "
                    f"text-transform:uppercase; letter-spacing:0.8px; "
                    f"border-bottom:2px solid {C['border']};"
                )
                td_base = (
                    f"padding:9px 14px; font-size:14px; "
                    f"border-bottom:1px solid {C['border']};"
                )
                rows_html = ""
                for r in rows:
                    rows_html += (
                        f"<tr>"
                        f"<td style='{td_base} color:{C['text']}; font-weight:600;'>{r['test']}</td>"
                        f"<td style='{td_base} color:#58a6ff; font-weight:700;'>{r['value']}</td>"
                        f"<td style='{td_base} color:{C['subtext']};'>{r['ref']}</td>"
                        f"</tr>"
                    )
                _html(
                    f"<div style='overflow-x:auto; margin-bottom:16px;'>"
                    f"<table style='width:100%; border-collapse:collapse; "
                    f"background:{C['bg']}; border-radius:8px; overflow:hidden;'>"
                    f"<thead><tr>"
                    f"<th style='{th_style}'>Test</th>"
                    f"<th style='{th_style}'>Result</th>"
                    f"<th style='{th_style}'>Normal Range</th>"
                    f"</tr></thead>"
                    f"<tbody>{rows_html}</tbody>"
                    f"</table></div>"
                )
        else:
            _html(f"<div style='font-size:13px; color:{C['neutral']}; font-style:italic;'>No lab values documented</div>")

        # Conditions
        conditions = structured.get("conditions", [])
        _html(f"<div style='font-size:14px; font-weight:700; color:{C['text']}; margin:14px 0 8px;'>🩺 Documented Conditions</div>")
        if conditions:
            items = "".join(f"<li style='color:{C['subtext']}; font-size:14px; margin-bottom:4px;'>{c}</li>" for c in conditions)
            _html(f"<ul style='margin:0; padding-left:18px;'>{items}</ul>")
        else:
            _html(f"<div style='font-size:13px; color:{C['neutral']}; font-style:italic;'>No explicit conditions documented</div>")

        # Medications
        meds = structured.get("medications", [])
        _html(f"<div style='font-size:14px; font-weight:700; color:{C['text']}; margin:14px 0 8px;'>💊 Medications</div>")
        if meds:
            items = "".join(f"<li style='color:{C['subtext']}; font-size:14px; margin-bottom:4px;'>{m}</li>" for m in meds)
            _html(f"<ul style='margin:0; padding-left:18px;'>{items}</ul>")
        else:
            _html(f"<div style='font-size:13px; color:{C['neutral']}; font-style:italic;'>No medications documented</div>")

        # Follow-up actions
        actions = structured.get("follow_up_actions", [])
        _html(f"<div style='font-size:14px; font-weight:700; color:{C['text']}; margin:14px 0 8px;'>📋 Follow-up Actions</div>")
        if actions:
            items = "".join(f"<li style='color:{C['subtext']}; font-size:14px; margin-bottom:4px;'>{a}</li>" for a in actions)
            _html(f"<ul style='margin:0; padding-left:18px;'>{items}</ul>")
        else:
            _html(f"<div style='font-size:13px; color:{C['neutral']}; font-style:italic;'>No follow-up actions documented</div>")

    # ── Right column: confidence breakdown ───────────────────
    with col_right:

        overall     = confidence.get("overall_confidence", "—")
        overall_color = _conf_color(overall)
        bands       = confidence.get("bands", {})
        notes       = confidence.get("uncertainty_notes", "")

        st.markdown("**📊 Confidence Breakdown**")

        # Overall badge
        _html(
            f"<div style='background:{C['bg']}; border:1px solid {overall_color}; "
            f"border-radius:10px; padding:14px; text-align:center; margin-bottom:12px;'>"
            f"<div style='font-size:11px; color:{C['neutral']}; text-transform:uppercase; "
            f"letter-spacing:1px;'>Overall Confidence</div>"
            f"<div style='font-size:28px; font-weight:700; color:{overall_color}; margin-top:4px;'>"
            f"{overall}</div>"
            f"</div>"
        )

        # Individual bands using st.columns — no f-string HTML generation
        band_labels = {
            "extraction": "Data Extraction",
            "grounding":  "Evidence Grounding",
            "reasoning":  "Clinical Reasoning",
        }
        for key, friendly_label in band_labels.items():
            band_data = bands.get(key, {})
            label = band_data.get("label", "—")
            color = _conf_color(label)
            b1, b2 = st.columns([2, 1])
            with b1:
                st.markdown(
                    f"<div style='font-size:13px; color:{C['neutral']}; padding:4px 0;'>"
                    f"{friendly_label}</div>",
                    unsafe_allow_html=True,
                )
            with b2:
                st.markdown(
                    f"<div style='font-size:13px; font-weight:600; color:{color}; "
                    f"padding:4px 0; text-align:right;'>{label}</div>",
                    unsafe_allow_html=True,
                )

        if notes and notes != "No significant uncertainty detected.":
            _html(
                f"<div style='margin-top:10px; font-size:12px; color:{C['neutral']}; "
                f"border-top:1px solid {C['border']}; padding-top:8px;'>{notes}</div>"
            )

        # Clinical reasoning
        st.markdown("")
        st.markdown("**🧠 Clinical Reasoning**")
        summary = (
            explanation.get("clinical_reasoning_summary", "").strip()
            or explanation.get("plain_language_explanation", "").strip()
        )
        if summary:
            _html(
                f"<div style='font-size:13px; color:{C['subtext']}; line-height:1.75; "
                f"padding:14px; background:{C['bg']}; border-radius:8px; "
                f"border:1px solid {C['border']};'>{summary}</div>"
            )
        else:
            _html(
                f"<div style='font-size:13px; color:{C['neutral']}; font-style:italic;'>"
                f"Explanation not available for this document.</div>"
            )

    # ── Reason narrative (plain English for doctors) ──────────
    _divider()
    _section_header("🔗", "How the Findings Are Connected")

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_map = {n["id"]: n for n in nodes}

    type_icons = {
        "condition":  "🩺",
        "lab_value":  "🧪",
        "medication": "💊",
        "action":     "📋",
    }

    if edges:
        _html(
            f"<div style='font-size:13px; color:{C['neutral']}; margin-bottom:12px;'>"
            f"The system identified {len(edges)} relationship(s) between documented findings. "
            f"These are based solely on what appears in the clinical document.</div>"
        )
        for edge in edges:
            src = node_map.get(edge.get("source", ""), {})
            tgt = node_map.get(edge.get("target", ""), {})
            src_icon  = type_icons.get(src.get("type", ""), "📌")
            tgt_icon  = type_icons.get(tgt.get("type", ""), "📌")
            src_label = _label_str(src.get("label", edge.get("source", "")))
            tgt_label = _label_str(tgt.get("label", edge.get("target", "")))
            just      = edge.get("justification", "")
            rel       = edge.get("relationship", "relates to").replace("_", " ")

            _html(
                f"<div style='background:{C['bg']}; border:1px solid {C['border']}; "
                f"border-radius:10px; padding:16px; margin-bottom:10px;'>"
                f"<div style='display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:8px;'>"
                f"  <span style='font-size:14px; font-weight:600; color:{C['text']};'>"
                f"    {src_icon} {src_label}"
                f"  </span>"
                f"  <span style='color:{C['accent']}; font-size:12px;'>── {rel} ──▶</span>"
                f"  <span style='font-size:14px; font-weight:600; color:{C['text']};'>"
                f"    {tgt_icon} {tgt_label}"
                f"  </span>"
                f"</div>"
                f"<div style='font-size:13px; color:{C['neutral']}; line-height:1.5;'>{just}</div>"
                f"</div>"
            )

    elif nodes:
        _html(
            f"<div style='font-size:13px; color:{C['neutral']}; margin-bottom:12px;'>"
            f"The following findings were extracted. No direct relationships between them "
            f"are documented in this clinical note.</div>"
        )
        for node in nodes:
            icon  = type_icons.get(node.get("type", ""), "📌")
            _raw_label = node.get("label", "")
            if isinstance(_raw_label, dict):
                _raw_label = _raw_label.get("test_name", _raw_label.get("name", str(_raw_label)))
            label = _label_str(str(_raw_label))
            # Smarter type label — override if node looks like a condition
            raw_ntype = node.get("type", "")
            _node_label = node.get("label", "")
            # label can sometimes be a dict if LLM schema drifts — coerce to string
            if isinstance(_node_label, dict):
                _node_label = _node_label.get("test_name", _node_label.get("name", str(_node_label)))
            node_label_lower = str(_node_label).lower()
            CONDITION_KEYWORDS = {
                "hypertension", "anemia", "anaemia", "diabetes", "obesity",
                "infection", "disease", "syndrome", "disorder", "failure",
                "cancer", "tumor", "tumour", "sepsis", "insufficiency"
            }
            if any(kw in node_label_lower for kw in CONDITION_KEYWORDS):
                raw_ntype = "condition"
            ntype = raw_ntype.replace("_", " ").capitalize() or "Finding"
            _html(
                f"<div style='background:{C['bg']}; border:1px solid {C['border']}; "
                f"border-radius:8px; padding:12px 16px; margin-bottom:8px; "
                f"display:flex; justify-content:space-between; align-items:center;'>"
                f"  <span style='font-size:14px; color:{C['text']};'>{icon} {label}</span>"
                f"  <span style='font-size:11px; color:{C['neutral']}; background:{C['border']}; "
                f"padding:2px 10px; border-radius:12px;'>{ntype}</span>"
                f"</div>"
            )
    else:
        _html(
            f"<div style='color:{C['neutral']}; font-size:13px;'>"
            f"No findings extracted for relationship mapping.</div>"
        )

    # ── Medical References (renamed, no score shown to doctors) ──
    _divider()
    _section_header("📚", "Medical References Used")
    _html(
        f"<div style='font-size:13px; color:{C['neutral']}; margin-bottom:12px;'>"
        f"The explanation above was cross-referenced against the following "
        f"publicly available medical sources.</div>"
    )

    if sources:
        seen_urls_clin  = set()
        seen_terms_clin = set()
        for src in sources:
            url   = src.get("source_url", "")
            term  = src.get("term", "Reference")
            defn  = src.get("definition", "")
            band  = src.get("confidence_band", "")

            term_key = term.strip().lower()
            # Normalise "blood urea nitrogen (bun)" → "bun" to catch near-duplicates
            import re as _re3
            base_term = _re3.sub(r'\(.*?\)', '', term_key).strip()
            # Also strip common prefixes like "serum ", "blood ", "plasma "
            for prefix in ['serum ', 'blood ', 'plasma ', 'total ', 'quantitative ']:
                base_term = base_term.replace(prefix, '').strip()

            if url in seen_urls_clin or base_term in seen_terms_clin:
                continue
            seen_urls_clin.add(url)
            seen_terms_clin.add(base_term)
            if len(seen_terms_clin) > 5:
                break

            band_color = {
                "Established Practice": C["High"],
                "Commonly Observed":    C["Moderate"],
                "Context Dependent":    C["neutral"],
            }.get(band, C["neutral"])

            link_html = (
                f"<a href='{url}' target='_blank' style='font-size:12px; "
                f"color:{C['accent']}; text-decoration:none;'>↗ View Source</a>"
                if url.startswith("http") else ""
            )

            _html(
                f"<div style='background:{C['bg']}; border:1px solid {C['border']}; "
                f"border-radius:10px; padding:16px; margin-bottom:10px;'>"
                f"<div style='display:flex; justify-content:space-between; "
                f"align-items:flex-start; margin-bottom:6px;'>"
                f"  <div style='font-size:14px; font-weight:600; color:{C['text']};'>{term}</div>"
                f"  {link_html}"
                f"</div>"
                f"<div style='font-size:13px; color:{C['neutral']}; line-height:1.6; margin-bottom:8px;'>"
                f"{defn}</div>"
                f"<span style='font-size:11px; color:{band_color}; "
                f"background:{band_color}22; border:1px solid {band_color}55; "
                f"border-radius:12px; padding:2px 10px;'>{band}</span>"
                f"</div>"
            )
    else:
        st.info("No medical references were retrieved for this input.")


# ─────────────────────────────────────────────────────────────
# LAYER 3 — ADVANCED VIEW (hidden, for engineers / auditors)
# Not shown in the main UI. Rendered inside a collapsed expander
# at the very bottom of main.py, invisible to patients and doctors.
# ─────────────────────────────────────────────────────────────

def render_advanced_view(result: dict):
    """
    Raw JSON output for engineers, auditors, and AI safety reviewers.
    Called from main.py inside a deeply collapsed expander after the footer.
    """
    with st.expander("🔍 Raw System Output (Structured Extraction)"):
        st.json(result.get("structured_data", {}))

    with st.expander("📚 Raw Grounding Sources"):
        st.json(result.get("grounding_sources", []))

    with st.expander("📊 Raw Confidence Package"):
        st.json(result.get("confidence", {}))

    with st.expander("🕸 Raw Reason Graph — Nodes & Edges"):
        st.json(result.get("reason_graph", {}))

    with st.expander("📝 Raw LLM Output"):
        st.json(result.get("explanation", {}))

    with st.expander("🔒 Safety Classifier Result"):
        st.json(result.get("safety", {}))



# ─────────────────────────────────────────────────────────────
# CHAT — Contextual Q&A grounded in the processed report
# ─────────────────────────────────────────────────────────────

def render_chat(result: dict, is_doctor: bool = False):
    """
    Simple contextual Q&A grounded in the processed report.
    Plain text input + Ask button. No floating bar, no suggested questions.
    """
    from app.chat_engine import ChatEngine

    _divider()

    _txt = C["text"]
    _neu = C["neutral"]
    _bg  = C["bg"]
    _bdr = C["border"]
    _sub = C["subtext"]
    _acc = C["accent"]

    _html(
        f"<h4 style='color:{_txt}; margin-bottom:4px;'>Ask About Your Report</h4>"
        f"<div style='font-size:13px; color:{_neu}; margin-bottom:16px;'>"
        f"Have a question about your results? Type below and click Ask. "
        f"Answers are based only on your specific report.</div>"
    )

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "chat_engine" not in st.session_state:
        st.session_state.chat_engine = ChatEngine()

    engine = st.session_state.chat_engine

    # Chat history bubbles
    for turn in st.session_state.chat_history:
        if turn["role"] == "user":
            _html(
                f"<div style='display:flex; justify-content:flex-end; margin-bottom:10px;'>"
                f"<div style='background:#1f3a5f; color:{_txt}; border-radius:16px 16px 4px 16px; "
                f"padding:10px 16px; max-width:75%; font-size:14px; line-height:1.6;'>"
                f"{turn['content']}</div></div>"
            )
        else:
            answer = turn.get("content", "")
            note   = turn.get("safety_note", "")
            _html(
                f"<div style='display:flex; justify-content:flex-start; margin-bottom:4px;'>"
                f"<div style='background:{_bg}; border:1px solid {_bdr}; color:{_sub}; "
                f"border-radius:16px 16px 16px 4px; "
                f"padding:10px 16px; max-width:80%; font-size:14px; line-height:1.7;'>"
                f"{answer}</div></div>"
            )
            if note:
                _html(f"<div style='font-size:11px; color:{_neu}; margin:2px 0 10px 8px;'>Note: {note}</div>")

    # Input row
    placeholder = "Ask a clinical question..." if is_doctor else "Ask something about your results..."
    
    col_in, col_btn = st.columns([5, 1])
    with col_in:
        user_input = st.text_input(
            label="q",
            placeholder=placeholder,
            label_visibility="collapsed",
            key="chat_input"
        )
    with col_btn:
        ask_clicked = st.button("Ask", key="ask_btn", use_container_width=True)

    # Process only when Ask button is clicked
    if ask_clicked and user_input and user_input.strip():
        question = user_input.strip()
        st.session_state.chat_history.append({"role": "user", "content": question})
        history_for_engine = [
            {"role": t["role"], "content": t["content"]}
            for t in st.session_state.chat_history[:-1]
        ]
        with st.spinner("Thinking..."):
            response = engine.ask(
                question=question,
                result=result,
                history=history_for_engine
            )
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": response["answer"],
            "safety_note": response.get("safety_note", "")
        })
        # Clear the input field by deleting the key from session state
        if "chat_input" in st.session_state:
            del st.session_state["chat_input"]
        st.experimental_rerun()

# ─────────────────────────────────────────────────────────────
# Blocked / Error states
# ─────────────────────────────────────────────────────────────

def render_blocked(result: dict):
    st.error("🚫 This input could not be processed.")
    _html(
        f"<div style='background:{C['bg']}; border:1px solid {C['Low']}; "
        f"border-radius:10px; padding:18px; margin-top:10px;'>"
        f"<div style='font-size:15px; color:{C['subtext']}; line-height:1.7; margin-bottom:12px;'>"
        f"SpashtaAI only processes <strong>clinical documents</strong> such as lab reports, "
        f"discharge summaries, and clinical notes.<br><br>"
        f"It cannot process requests for medical advice, emergency guidance, or non-medical content."
        f"</div>"
        f"<div style='font-size:13px; color:{C['neutral']}; border-top:1px solid {C['border']}; "
        f"padding-top:10px;'>"
        f"Reason: {result.get('safety', {}).get('reason', 'Input not accepted.')}"
        f"</div>"
        f"</div>"
    )


def render_error(result: dict):
    st.error("❗ A system error occurred.")
    _html(
        f"<div style='background:{C['bg']}; border:1px solid {C['Low']}; "
        f"border-radius:10px; padding:18px; margin-top:10px;'>"
        f"<div style='font-size:14px; color:{C['neutral']};'>"
        f"{result.get('message', 'An unknown error occurred. Please try again.')}"
        f"</div>"
        f"</div>"
    )


# ─────────────────────────────────────────────────────────────
# Helper kept for backward compat — not used in render paths
# ─────────────────────────────────────────────────────────────

def COLORS_STATUS(kind: str) -> str:
    return C["Moderate"] if kind == "above" else C["High"]
