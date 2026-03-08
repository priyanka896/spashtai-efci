import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv()
import io
from datetime import datetime
import streamlit as st
from ingestion.document_processor import extract_text_from_document
from orchestrator.efci_orchestrator import EFCIOrchestrator
from app.ui_renderer import (
    render_patient_view, render_clinical_view, render_advanced_view,
    render_chat, render_blocked, render_error,
)

st.set_page_config(page_title="SpashtaAI", layout="wide", initial_sidebar_state="expanded")

def generate_pdf_summary(result: dict) -> bytes:
    """Generate a patient-friendly PDF summary using reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.units import cm
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []
    
    # ── Title ──────────────────────────────────────────────────
    title_style = ParagraphStyle('title', parent=styles['Title'],
                                  fontSize=20, textColor=colors.HexColor('#ff9900'),
                                  spaceAfter=4)
    sub_style = ParagraphStyle('sub', parent=styles['Normal'],
                                fontSize=11, textColor=colors.HexColor('#555555'),
                                spaceAfter=2)
    story.append(Paragraph("SpashtaAI", title_style))
    story.append(Paragraph("Medical Information & Clinical Report Explainer", sub_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%d %B %Y, %I:%M %p')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#ff9900')))
    story.append(Spacer(1, 0.3*cm))
    
    # ── Lab Findings ───────────────────────────────────────────
    lab_header = ParagraphStyle('lh', parent=styles['Heading2'],
                                  fontSize=14, textColor=colors.HexColor('#1a1a2e'),
                                  spaceBefore=10, spaceAfter=6)
    story.append(Paragraph("Lab Findings", lab_header))
    
    lab_values = result.get("structured_data", {}).get("lab_values", [])
    if lab_values:
        table_data = [["Test", "Your Result", "Normal Range"]]
        for lv in lab_values:
            if isinstance(lv, dict):
                # Clean test name - remove comma-separated Textract artifacts
                test_name = str(lv.get("test_name", ""))
                if "," in test_name:
                    test_name = test_name.split(",")[0].strip()
                test_name = test_name[:40]  # truncate if still too long
                
                value = f"{lv.get('value', '')} {lv.get('unit', '')}".strip()
                ref = str(lv.get("reference_range", "—"))
                table_data.append([test_name, value, ref])
        
        t = Table(table_data, colWidths=[7*cm, 5*cm, 5*cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#ff9900')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f9f9f9'), colors.white]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#dddddd')),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("No specific lab values were extracted.", styles['Normal']))
    
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#cccccc')))
    
    # ── What This Means ────────────────────────────────────────
    story.append(Paragraph("What This Means", lab_header))
    explanation = result.get("explanation", {})
    plain = (explanation.get("plain_language_explanation") or
             explanation.get("clinical_reasoning_summary") or
             "No explanation was generated.")
    
    body_style = ParagraphStyle('body', parent=styles['Normal'],
                                  fontSize=11, leading=16,
                                  textColor=colors.HexColor('#222222'))
    for line in plain.split("\n"):
        if line.strip():
            story.append(Paragraph(line.strip(), body_style))
            story.append(Spacer(1, 0.15*cm))
    
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#cccccc')))
    
    # ── Confidence ─────────────────────────────────────────────
    story.append(Paragraph("Explanation Confidence", lab_header))
    conf = result.get("confidence", {})
    overall = conf.get("overall_confidence", "—")
    conf_style = ParagraphStyle('conf', parent=styles['Normal'], fontSize=11)
    story.append(Paragraph(f"<b>Overall Confidence:</b> {overall}", conf_style))
    story.append(Spacer(1, 0.3*cm))
    
    # ── Footer ─────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#ff9900')))
    footer_style = ParagraphStyle('footer', parent=styles['Normal'],
                                    fontSize=9, textColor=colors.HexColor('#888888'),
                                    spaceBefore=6)
    story.append(Paragraph("⚠ This document is for educational purposes only. "
                           "It does not constitute medical advice, diagnosis, or treatment. "
                           "Always consult a qualified healthcare provider.",
                           footer_style))
    story.append(Paragraph("SpashtaAI | Powered by AWS Bedrock & Amazon Textract", footer_style))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.read()

st.markdown("""
<style>
/* ── Kill white header flash ── */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
header[data-testid="stHeader"] {
    background-color: #0d1117 !important;
}

/* ── Base app ── */
.stApp { 
    background-color:#0d1117; 
    color:#e6edf3; 
    font-family:"Amazon Ember",sans-serif; 
}
.block-container { 
    padding-top:0.5rem; 
    padding-bottom:2rem; 
}

/* ── Sidebar: background + visible text ── */
section[data-testid="stSidebar"] { 
    background-color: #0d1117 !important; 
}
section[data-testid="stSidebar"] * {
    color: #e6edf3 !important;
}
section[data-testid="stSidebar"] .stMarkdown p {
    color: #e6edf3 !important;
}

/* ── Inputs ── */
textarea, input[type="text"] {
    border-radius:6px !important; 
    border:1px solid #30363d !important;
    background-color:#161b22 !important; 
    color:#e6edf3 !important;
}
hr { 
    border:1px solid #30363d !important; 
}
[data-testid="stFileUploader"] { 
    border:1px dashed #30363d; 
    border-radius:8px; 
    padding:12px; 
}

/* ── ALL buttons: orange like primary CTA ── */
div.stButton > button {
    width: 100%;
    border-radius: 8px !important;
    border: none !important;
    font-weight: 700 !important;
    font-size: 14px !important;
    padding: 0.5rem 1rem !important;
    transition: all 0.2s ease;
    background-color: #ff9900 !important;
    color: #000000 !important;
}

div.stButton > button:hover {
    background-color: #111111 !important;
    color: #ff9900 !important;
}

/* ── EXPLAIN THIS REPORT — larger primary CTA ── */
div.stButton > button[kind="primary"] {
    background-color: #ff9900 !important;
    color: #000000 !important;
    font-weight: 800 !important;
    font-size: 16px !important;
    padding: 0.75rem 2rem !important;
    box-shadow: 0 0 16px rgba(255,153,0,0.7) !important;
    letter-spacing: 0.5px !important;
}

div.stButton > button[kind="primary"]:hover {
    background-color: #111111 !important;
    color: #ff9900 !important;
    box-shadow: 0 0 22px rgba(255,153,0,1) !important;
}

/* ── DOWNLOAD PDF button ── */
div.stDownloadButton > button {
    background-color: #238636 !important;
    color: #ffffff !important;
    font-weight: 700 !important;
    font-size: 14px !important;
    border: none !important;
    border-radius: 8px !important;
    width: 100% !important;
    padding: 0.5rem 1rem !important;
}

div.stDownloadButton > button:hover {
    background-color: #2ea043 !important;
}
</style>
""", unsafe_allow_html=True)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# SIDEBAR
with st.sidebar:
    st.markdown("## SpashtaAI")
    st.caption("Clinical Explainability System")
    st.markdown("---")
    st.markdown("<p style='color:#8b949e; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:6px;'>Who are you?</p>", unsafe_allow_html=True)
    view_mode = st.radio("view", ["Patient / Family Member", "Doctor / Clinician"], index=0, label_visibility="collapsed")
    st.markdown("---")

    st.markdown("<p style='color:#8b949e; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:8px;'>Actions</p>", unsafe_allow_html=True)
    
    if st.button("New Report", key="new_report_btn"):
        for k in ["_efci_last_result", "chat_history", "chat_engine"]:
            st.session_state.pop(k, None)
        st.experimental_rerun()

    if "chat_history" in st.session_state and st.session_state.chat_history:
        if st.button("Clear Chat", key="clear_chat_btn"):
            st.session_state.chat_history = []
            st.experimental_rerun()

    st.markdown("---")
    with st.expander("About SpashtaAI", expanded=False):
        st.markdown("Reads clinical documents and explains findings in plain language. Does NOT diagnose, treat or replace your doctor.")
    st.info("Educational use only. Always consult a healthcare provider.")

# HEADER
st.markdown("<h1 style='text-align:center;color:#e6edf3;margin-bottom:2px;'>SpashtaAI</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;color:#8b949e;font-size:15px;margin-top:0;'>Medical Information & Clinical Report Explainer</p>", unsafe_allow_html=True)
st.markdown("<div style='height:3px;background:linear-gradient(90deg,#ff9900,#ffa31a);border-radius:2px;margin:4px 0 10px;'></div>", unsafe_allow_html=True)

vc = "#2ea043" if "Patient" in view_mode else "#d29922"
vl = "Patient Mode - Plain language explanation." if "Patient" in view_mode else "Clinical Mode - Structured detail and references."
st.markdown(f"<div style='font-size:13px;color:{vc};border-left:3px solid {vc};padding:6px 14px;background:#161b22;border-radius:0 6px 6px 0;margin-bottom:10px;'>{vl}</div>", unsafe_allow_html=True)

# UPLOAD SCREEN
if "_efci_last_result" not in st.session_state:
    # ── Compact hero ──────────────────────────────────────────
    st.markdown("""
    <div style='text-align:center; padding:6px 0 10px;'>
        <div style='font-size:24px; font-weight:700; color:#e6edf3; margin-bottom:4px;'>
            🏥 Upload Your Lab Report
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # ── Trust badges ─────────────────────────────────────────
    b1, b2, b3, b4 = st.columns(4)
    badges = [
        (b1, "🔒", "No data stored"),
        (b2, "🏛️", "NIH/WHO grounded"),
        (b3, "⚡", "~10s analysis"),
        (b4, "🩺", "Patient + Doctor mode"),
    ]
    for col, icon, label in badges:
        with col:
            st.markdown(
                f"<div style='text-align:center; background:#161b22; border:1px solid #30363d; "
                f"border-radius:10px; padding:8px 6px;'>"
                f"<div style='font-size:18px;'>{icon}</div>"
                f"<div style='font-size:11px; color:#8b949e; margin-top:3px;'>{label}</div>"
                f"</div>",
                unsafe_allow_html=True
            )
    
    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    
    # ── File uploader ─────────────────────────────────────────
    uploaded_file = st.file_uploader(
        "Upload your report (PDF, PNG, JPG)",
        type=["pdf","png","jpg","jpeg"],
        label_visibility="visible"
    )
    clinical_text = ""
    if uploaded_file:
        try:
            with st.spinner("Reading your document..."):
                clinical_text = extract_text_from_document(uploaded_file)
            st.success("✅ Document read successfully. Ready to analyse.")
            with st.expander("View / edit extracted text", expanded=False):
                clinical_text = st.text_area("text", value=clinical_text, height=150, key="extracted_edit", label_visibility="collapsed")
        except Exception as e:
            st.error(f"Could not read document: {e}")
            st.stop()
    else:
        st.markdown(
            "<div style='text-align:center; color:#8b949e; font-size:13px; margin:6px 0;'>"
            "— or paste text directly —</div>",
            unsafe_allow_html=True
        )
        clinical_text = st.text_area(
            "Paste clinical note:",
            height=60,
            placeholder="Example: What is anemia?\nOr paste lab results: HCG 11.00 mIU/mL  Reference: <5.00 mIU/mL",
            label_visibility="collapsed"
        )

    # ── BIG visible CTA — always in view ─────────────────────
    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    explain_clicked = st.button("🚀 Explain This Report", key="explain_btn", type="primary")

    if explain_clicked:
        if not clinical_text.strip():
            st.error("Please upload a file or paste a clinical note.")
            st.stop()
        
        with st.spinner("Analysing your report..."):
            result = EFCIOrchestrator().process(clinical_text.strip())
        if result["status"] == "blocked":
            render_blocked(result); st.stop()
        if result["status"] == "error":
            render_error(result); st.stop()
        
        # If no lab values were extracted but user typed a general question,
        # generate a plain language explanation directly via Bedrock
        explanation = result.get("explanation", {})
        has_plain = bool(
            explanation.get("plain_language_explanation") or
            explanation.get("clinical_reasoning_summary")
        )
        lab_values = result.get("structured_data", {}).get("lab_values", [])
        
        if not has_plain and not lab_values and not uploaded_file:
            try:
                from llm.bedrock_client import BedrockClient
                _client = BedrockClient()
                _prompt = (
                    f"A user asked: '{clinical_text.strip()}'\n\n"
                    "Provide a clear, simple explanation in plain language suitable for a patient "
                    "with no medical background. Keep it under 150 words. "
                    "Do not diagnose or prescribe. End with: 'Always consult your doctor for personalised advice.'"
                )
                _answer, _ = _client._call_with_resilience(_prompt, max_tokens=300, label="general_explain")
                if _answer:
                    if "explanation" not in result or not result["explanation"]:
                        result["explanation"] = {}
                    result["explanation"]["plain_language_explanation"] = _answer
            except Exception as e:
                # If Bedrock call fails, add a fallback message
                if "explanation" not in result or not result["explanation"]:
                    result["explanation"] = {}
                result["explanation"]["plain_language_explanation"] = (
                    "Unable to generate explanation at this time. Please try again."
                )
        
        st.session_state["_efci_last_result"] = result
        st.session_state["chat_history"] = []
        st.experimental_rerun()

# RESULTS VIEW
else:
    result = st.session_state["_efci_last_result"]
    is_doctor = "Doctor" in view_mode
    
    # ── Processed banner ──────────────────────────────────────
    st.markdown(
        "<div style='background:#0f2a1a; border:1px solid #2ea043; border-radius:8px; "
        "padding:10px 16px; margin-bottom:24px; font-size:14px; color:#2ea043; "
        "display:flex; align-items:center; gap:10px;'>"
        "✅ <strong>Your document has been processed. Safety checks passed.</strong>"
        "<span style='color:#8b949e; font-size:12px; margin-left:8px;'>"
        "Use New Report in the sidebar to analyse a different document.</span>"
        "</div>",
        unsafe_allow_html=True
    )
    
    # ── Download PDF button — always visible, top of results ──
    try:
        pdf_bytes = generate_pdf_summary(result)
        st.download_button(
            label="📄 Download My Report Summary (PDF)",
            data=pdf_bytes,
            file_name=f"SpashtaAI_Report_{datetime.now().strftime('%d%m%Y')}.pdf",
            mime="application/pdf",
            key="download_pdf_btn",
            use_container_width=True,
        )
    except Exception as e:
        pass  # Never break the UI for PDF generation failure
    
    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    
    if "Patient" in view_mode:
        render_patient_view(result)
    else:
        render_clinical_view(result)
    render_chat(result, is_doctor=is_doctor)
    
    # ── System Details — visually separated, clearly for engineers ──
    st.markdown("<div style='height:20px;'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='background:#161b22; border:1px solid #30363d; border-radius:8px; "
        "padding:10px 16px; margin-bottom:10px;'>"
        "<span style='font-size:11px; color:#8b949e; text-transform:uppercase; "
        "letter-spacing:1px; font-weight:700;'>🔧 System Details</span>"
        "<span style='font-size:12px; color:#6e7681; margin-left:10px;'>"
        "Raw pipeline output — for engineering review only. Not for clinical use.</span>"
        "</div>",
        unsafe_allow_html=True
    )
    render_advanced_view(result)

st.markdown("<div style='color:#555;font-size:12px;text-align:center;margin-top:20px;'>SpashtaAI - Educational use only. Not medical advice.</div>", unsafe_allow_html=True)
