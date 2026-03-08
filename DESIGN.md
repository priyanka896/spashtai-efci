# SpashtaAI Design Document

**Design Philosophy & Implementation Decisions**

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Dual-Mode UI Design](#dual-mode-ui-design)
3. [Safety-First Architecture](#safety-first-architecture)
4. [Confidence Display Strategy](#confidence-display-strategy)
5. [PDF Export Design](#pdf-export-design)
6. [Chat Grounding Strategy](#chat-grounding-strategy)
7. [Technology Choices](#technology-choices)
8. [UX Design Decisions](#ux-design-decisions)

---

## Design Philosophy

### Core Principles

**1. Explainability First**
- Every AI decision must be traceable
- Confidence scores on 3 axes (extraction, grounding, reasoning)
- Source citations for all medical claims
- Transparent reasoning chains

**2. Safety First**
- Multiple safety gates prevent dangerous outputs
- Gender-aware medical interpretation
- Never provide personal medical advice
- Block emergency scenarios immediately

**3. Accessibility First**
- Plain language for patients (zero jargon)
- Structured detail for clinicians
- Visual hierarchy with color coding
- Mobile-responsive design

**4. Trust First**
- NIH/WHO/CDC grounded answers only
- Clear disclaimers (educational use only)
- No data storage (privacy by design)
- Audit logging for compliance

---

## Dual-Mode UI Design

### Problem Statement

**Challenge:** Same medical report needs to serve two vastly different audiences:
- **Patients:** Need plain language, reassurance, actionable guidance
- **Doctors:** Need structured data, clinical reasoning, source citations

**Solution:** Dual-mode interface with context-aware rendering

### Patient Mode Design

**Visual Language:**
- Large, friendly typography
- Color-coded lab value cards (green = normal, yellow = attention)
- Emoji indicators for emotional context
- "Should I Be Concerned?" section with clear guidance

**Content Strategy:**
```
Plain Language Explanation
├── What These Results Mean (no jargon)
├── Should I Be Concerned? (yes/no + why)
├── What You Can Do (actionable steps)
└── When to See a Doctor (clear triggers)
```

**Example Transformation:**
```
Clinical: "Elevated HCG (11.00 mIU/mL vs. ref <5.00)"
Patient:  "Your HCG level is higher than the normal range. 
           This hormone is often associated with pregnancy, 
           but can have other causes. Talk to your doctor 
           about what this means for you."
```

### Doctor Mode Design

**Visual Language:**
- Compact, information-dense layout
- Tabular lab values with reference ranges
- Clinical reasoning summary
- 3-axis confidence breakdown

**Content Strategy:**
```
Clinical View
├── Lab Values Table (test, value, unit, reference)
├── Clinical Reasoning Summary
├── Documented Conditions
├── Current Medications
├── Follow-up Actions
└── Confidence Breakdown (extraction, grounding, reasoning)
```

**Why Not Separate Apps?**
- Single deployment reduces maintenance
- Shared backend logic (DRY principle)
- Easy mode switching for family members consulting with doctors
- Consistent safety guardrails across both modes

---

## Safety-First Architecture

### Multi-Layer Safety Design

```
┌─────────────────────────────────────────────────────────┐
│  LAYER 1: INPUT SAFETY CLASSIFIER                       │
│  ─────────────────────────────────────────────────────  │
│  • Blocks non-medical content                           │
│  • Blocks emergency scenarios                           │
│  • Blocks personal advice requests                      │
│  • Allows clinical documentation + general questions    │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 2: GENDER-AWARE PROMPTS                          │
│  ─────────────────────────────────────────────────────  │
│  • Extracts patient gender from clinical text           │
│  • Injects gender-specific safety rules                 │
│  • CRITICAL: Never mention pregnancy for male patients  │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 3: GROUNDED GENERATION                           │
│  ─────────────────────────────────────────────────────  │
│  • All claims must cite NIH/WHO/CDC sources             │
│  • No speculation without evidence                      │
│  • Confidence scores for transparency                   │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 4: OUTPUT VALIDATION                             │
│  ─────────────────────────────────────────────────────  │
│  • Disclaimer injection (educational use only)          │
│  • No personal treatment recommendations                │
│  • Always suggest consulting a doctor                   │
└─────────────────────────────────────────────────────────┘
```

### Gender-Aware Safety: A Critical Design Decision

**The Problem:**
- Elevated HCG in males can indicate testicular cancer
- Generic AI models might mention pregnancy for any elevated HCG
- This is dangerous medical misinformation

**The Solution:**
```python
# Extract gender from clinical text
gender_match = re.search(r'gender[:\s]+([a-zA-Z]+)', clinical_text, re.IGNORECASE)
patient_gender = gender_match.group(1).strip().upper() if gender_match else "UNKNOWN"

# Gender-specific instruction
if patient_gender == "MALE":
    gender_instruction = (
        "CRITICAL: The patient is MALE. Under NO circumstances mention pregnancy, "
        "even if HCG is elevated. In males, elevated HCG may relate to other "
        "physiological factors. Do not speculate on cause."
    )
```

**Why This Matters:**
- Prevents dangerous misinterpretations
- Demonstrates responsible AI design
- Shows understanding of medical context

### Safety Classifier Evolution

**Initial Design (Too Restrictive):**
- Blocked "what is anemia" as medical_advice_request
- Users couldn't ask general medical questions
- Defeated the purpose of educational tool

**Current Design (Balanced):**
```
ALLOW:
- Lab reports with test values
- General medical questions ("what is X", "explain Y")
- Educational queries about conditions

BLOCK:
- Personal treatment advice without clinical context
- Emergency scenarios
- Completely non-medical content
```

**Key Insight:** "What is anemia?" is educational, not advice-seeking. The classifier now distinguishes between:
- ❌ "Should I take iron supplements?" (personal advice)
- ✅ "What is anemia?" (educational query)

---

## Confidence Display Strategy

### 3-Axis Confidence Model

**Design Rationale:**
- Single confidence score is opaque (how was it calculated?)
- 3-axis model shows WHERE uncertainty lies
- Helps users understand limitations

**Axis 1: Extraction Confidence**
```
What it measures: How well structured data was extracted from document
High:     All fields present (test, value, unit, reference)
Moderate: Some fields missing
Low:      Minimal structured data extracted
```

**Axis 2: Grounding Confidence**
```
What it measures: Quality of medical evidence retrieved
High:     Multiple NIH/WHO sources, high semantic match
Moderate: Some sources found, moderate match
Low:      Few or no trusted sources found
```

**Axis 3: Reasoning Confidence**
```
What it measures: Coherence of clinical explanation
High:     Explanation cites sources, mentions lab values, logical flow
Moderate: Some grounding, but gaps in reasoning
Low:      Minimal grounding, short explanation
```

### Patient-Friendly Confidence Messaging

**Design Challenge:** Technical confidence scores confuse patients

**Solution:** Context-aware messaging

```python
if overall_confidence == "High":
    patient_message = (
        "We found clear information about your results in trusted medical sources. "
        "This explanation is well-supported."
    )
elif overall_confidence == "Moderate":
    patient_message = (
        "We found some information about your results, but recommend discussing "
        "with your doctor for complete clarity."
    )
else:  # Low
    patient_message = (
        "We had difficulty finding detailed information about these specific results. "
        "Please consult your doctor for a thorough explanation."
    )
```

**Key Insight:** Never hide uncertainty. Transparency builds trust.

---

## PDF Export Design

### Problem Statement

**User Need:** Patients want to share AI explanations with their doctors

**Constraints:**
- Must be printable (A4 size)
- Must be professional (not a screenshot)
- Must include disclaimer (legal protection)
- Must be patient-friendly (no technical jargon)

### PDF Structure Design

```
┌─────────────────────────────────────────────────────────┐
│  HEADER                                                 │
│  • SpashtaAI logo/title                                 │
│  • Subtitle: "Medical Information & Clinical Report    │
│    Explainer"                                           │
│  • Generation timestamp                                 │
│  • Orange accent line (brand color)                     │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  LAB FINDINGS TABLE                                     │
│  ┌──────────────┬──────────────┬──────────────┐        │
│  │ Test         │ Your Result  │ Normal Range │        │
│  ├──────────────┼──────────────┼──────────────┤        │
│  │ HCG          │ 11.00 mIU/mL │ <5.00 mIU/mL │        │
│  │ Creatinine   │ 1.1 mg/dL    │ 0.7-1.2 mg/dL│        │
│  └──────────────┴──────────────┴──────────────┘        │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  WHAT THIS MEANS                                        │
│  • Plain language explanation                           │
│  • Paragraph format (easy to read)                      │
│  • No medical jargon                                    │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  CONFIDENCE LEVEL                                       │
│  • Overall confidence: High / Moderate / Low            │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  FOOTER (DISCLAIMER)                                    │
│  ⚠ This document is for educational purposes only.      │
│  It does not constitute medical advice, diagnosis, or   │
│  treatment. Always consult a qualified healthcare       │
│  provider.                                              │
│                                                         │
│  SpashtaAI | Powered by AWS Bedrock & Amazon Textract  │
└─────────────────────────────────────────────────────────┘
```

### Technical Implementation

**Library Choice:** ReportLab
- Industry standard for Python PDF generation
- Precise layout control
- Professional typography
- No external dependencies (pure Python)

**Key Design Decisions:**

1. **Table Column Widths:** `[7cm, 5cm, 5cm]`
   - Test name gets more space (can be long)
   - Result and reference are compact

2. **Text Cleaning:**
   ```python
   # Textract sometimes returns comma-separated artifacts
   test_name = test_name.split(",")[0].strip()
   test_name = test_name[:40]  # truncate if still too long
   ```

3. **Color Scheme:**
   - Orange (#ff9900) for headers (brand color)
   - Black text on white background (printable)
   - Light gray (#f9f9f9) for alternating table rows

4. **Disclaimer Placement:**
   - Always at bottom (legal requirement)
   - Gray text (de-emphasized but readable)
   - Clear warning icon (⚠)

---

## Chat Grounding Strategy

### Problem Statement

**Challenge:** Multi-turn chat can drift from original lab report

**Risk:** AI might answer general medical questions unrelated to patient's results

**Solution:** Context-aware grounding with safety guardrails

### Chat Architecture

```
┌─────────────────────────────────────────────────────────┐
│  USER QUESTION                                          │
│  "What does elevated HCG mean?"                         │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  CONTEXT INJECTION                                      │
│  • Original lab values                                  │
│  • Patient gender                                       │
│  • Previous explanation                                 │
│  • Retrieved medical sources                            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  GROUNDED PROMPT                                        │
│  "Based on THIS patient's lab report showing HCG       │
│   11.00 mIU/mL (ref <5.00), and considering the        │
│   patient is [GENDER], explain..."                      │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  NOVA PRO RESPONSE                                      │
│  • Grounded in patient's specific values                │
│  • Gender-aware                                         │
│  • Cites sources                                        │
│  • Ends with "consult your doctor"                      │
└─────────────────────────────────────────────────────────┘
```

### Chat Safety Rules

**Always Inject:**
1. Patient's specific lab values
2. Patient gender (for gender-aware responses)
3. Previous explanation (for consistency)
4. Disclaimer reminder (educational use only)

**Never Allow:**
1. Personal treatment recommendations
2. Diagnosis without clinical context
3. Speculation beyond grounded sources
4. Emergency medical advice

### Model Choice: Nova Pro

**Why Nova Pro for chat (not Nova Lite)?**
- Multi-turn conversations require nuance
- Users ask complex follow-up questions
- Higher quality responses justify cost
- Only 5% of total Bedrock spend (most users don't chat)

**Cost Optimization:**
- Chat is opt-in (not automatic)
- Context window limited to last 5 messages
- Grounded prompts reduce hallucination (fewer retries)

---

## Technology Choices

### Streamlit: The Right Tool for the Job

**Why Streamlit?**

✅ **Rapid Prototyping**
- Hackathon-friendly (built in 3 days)
- Pure Python (no frontend/backend split)
- Hot reload for fast iteration

✅ **Built-in UI Components**
- File uploader (PDF/image support)
- Text areas, buttons, expanders
- Sidebar, columns, tabs
- No CSS wrestling (mostly)

✅ **Session State Management**
- Built-in session state for chat history
- Persistent results across reruns
- Simple state management

✅ **Deployment Simplicity**
- Single command: `streamlit run app/main.py`
- No build step, no bundling
- Works on EC2 out of the box

❌ **Limitations (Acknowledged)**
- Not production-grade for scale
- Limited customization (CSS hacks required)
- Rerun model can be confusing
- No native authentication

**Alternative Considered:** React + FastAPI
- **Rejected because:** 2x development time, overkill for hackathon
- **Future migration path:** Streamlit proves concept, React for production

### AWS Bedrock: Cost-Effective LLM Access

**Why Bedrock over OpenAI?**

✅ **Cost:** Nova models 10x cheaper than GPT-4
✅ **Latency:** Regional deployment (us-east-1)
✅ **No Ops:** Serverless, auto-scaling
✅ **Compliance:** AWS infrastructure (HIPAA-ready)

**Why Nova Family?**
- **Nova Micro:** Cheapest ($0.035/1M tokens) for simple classification
- **Nova Lite:** Balanced ($0.06/1M tokens) for extraction + explanation
- **Nova Pro:** Premium ($0.80/1M tokens) for complex chat

**3-Tier Cascade Savings:**
```
1000 reports with Nova cascade:  $0.22
1000 reports with Claude 3.5:    $9.00
Savings:                         97.6%
```

### DynamoDB: Sub-10ms Medical Glossary Lookups

**Why DynamoDB over RDS?**

✅ **Latency:** <10ms reads (critical for UX)
✅ **Serverless:** No ops, auto-scaling
✅ **Cost:** Pay-per-request ($1.25/1M reads)
✅ **No Cold Starts:** Always warm

**Schema Design:**
```python
{
    "term": "HCG",  # Partition key (O(1) lookup)
    "definition": "...",
    "source_url": "...",
    "source_authority": "NIH"
}
```

**Cache Hit Rate:** 95% (most lab tests are common)

**Alternative Considered:** Elasticsearch
- **Rejected because:** Overkill for 500 terms, requires ops, higher cost

### Textract: Best-in-Class Medical OCR

**Why Textract over Tesseract?**

✅ **Accuracy:** Optimized for medical documents
✅ **Quality Handling:** Works on poor scans (common in India)
✅ **No Training:** Pre-trained, no model management
✅ **Serverless:** Pay-per-page ($1.50/1000)

**Alternative Considered:** Tesseract (open source)
- **Rejected because:** Lower accuracy on poor scans, requires tuning

---

## UX Design Decisions

### Color Coding for Lab Values

**Design Goal:** Instant visual understanding (no reading required)

**Color Scheme:**
- 🟢 **Green:** Normal range (reassuring)
- 🟡 **Yellow:** Outside range (attention needed, not panic)
- ⚪ **Gray:** No reference range available

**Why Not Red?**
- Red = panic (bad UX for patients)
- Yellow = caution (more appropriate)
- Medical context matters (slightly elevated ≠ emergency)

### Button Design: Orange Brand Color

**Design Decision:** All buttons orange (#ff9900) with black text

**Rationale:**
- AWS brand color (hackathon context)
- High contrast (accessible)
- Energetic, friendly (not clinical blue)

**Hover State:** Inverted (black background, orange text)
- Clear interaction feedback
- Maintains brand consistency

### "New Report" Button: Always Visible

**Problem:** Users didn't know how to analyze a second report

**Solution:** "New Report" button in sidebar
- Always visible (not hidden in menu)
- Disabled when no report loaded (clear affordance)
- Clears all state (chat history, results)

### Chat Input: Button-Only (No Enter Key)

**Initial Design:** Enter key submits chat message

**Problem:** Users accidentally submitted incomplete messages

**Solution:** Removed Enter key handling, button-only submission
- More deliberate interaction
- Prevents accidental submissions
- Clearer affordance (button = action)

### Processed Banner: Green Success State

**Design Goal:** Reassure users that safety checks passed

**Visual Language:**
- Green background (#0f2a1a)
- Green border (#2ea043)
- Checkmark icon (✅)
- Clear message: "Safety checks passed"

**Why This Matters:**
- Users worry about AI accuracy
- Explicit safety confirmation builds trust
- Green = safe (universal color language)

### System Details: Clearly Labeled "For Engineers"

**Problem:** Technical JSON output confused patients

**Solution:** Visual separation + clear labeling
```
🔧 System Details
Raw pipeline output — for engineering review only. Not for clinical use.
```

**Design Decisions:**
- Collapsed by default (expander)
- Gray background (de-emphasized)
- Clear warning (not for clinical use)
- Useful for debugging, not for patients

---

## Design Lessons Learned

### 1. Simplicity Beats Features

**Initial Design:** Complex reasoning graph visualization

**Reality:** Patients don't care about graph theory

**Final Design:** Plain English narrative ("Here's what we found...")

**Lesson:** Know your audience. Patients want answers, not algorithms.

### 2. Safety Can't Be an Afterthought

**Initial Approach:** Build features first, add safety later

**Problem:** Gender-awareness bug discovered late (pregnancy for males)

**Final Approach:** Safety-first architecture from day 1

**Lesson:** Medical AI requires safety by design, not safety by patch.

### 3. Confidence Transparency Builds Trust

**Initial Design:** Hide confidence scores (might confuse users)

**User Feedback:** "How do I know if this is accurate?"

**Final Design:** 3-axis confidence with patient-friendly messaging

**Lesson:** Users trust AI more when it admits uncertainty.

### 4. PDF Export Was Underestimated

**Initial Thought:** "Nice to have" feature

**Reality:** Most requested feature (users want to share with doctors)

**Lesson:** Understand the full user journey (AI explanation → doctor visit).

---

**Design is never done. This document captures decisions as of March 2025.**

