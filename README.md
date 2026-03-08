# SpashtaAI — Explainability-First Clinical Intelligence (EFCI)

**Making clinical reports understandable for 700 million Indians**

[![AWS AI for Bharat Hackathon](https://img.shields.io/badge/AWS-AI%20for%20Bharat%20-orange)](https://aws.amazon.com)
[![Live Demo](https://img.shields.io/badge/Live-Demo-green)](http://54.204.137.70:8501/)
[![Video Demo](https://img.shields.io/badge/Video-Demo-red)](https://youtu.be/sTgoSQuRjfs)

---

## 🎯 Problem Statement

In India, **700 million people** struggle to understand their medical reports. Lab results arrive filled with medical jargon, reference ranges, and technical terms that leave patients confused and anxious. This leads to:

- **Delayed treatment** due to misunderstanding urgency
- **Unnecessary anxiety** from not knowing what results mean
- **Poor health literacy** across rural and urban populations
- **Doctor consultation bottlenecks** for simple explanations

**The gap:** Patients need immediate, trustworthy explanations in plain language — but doctors don't have time to explain every report in detail.

---

## 💡 Solution Overview

**SpashtaAI** is an AI-powered clinical document explainability system that transforms complex lab reports into clear, patient-friendly explanations. Built on AWS Bedrock with a novel **Explainability-First Clinical Intelligence (EFCI)** architecture, it provides:

✅ **Instant plain-language explanations** of lab results  
✅ **Dual-mode interface** (Patient & Doctor views)  
✅ **Safety-first design** with gender-aware medical interpretation  
✅ **3-axis confidence scoring** for transparency  
✅ **NIH/WHO-grounded answers** with source citations  
✅ **PDF export** for sharing with doctors  
✅ **Multi-turn chat** for follow-up questions  

---

## 🚀 Key Features

### 1. **Patient Mode** 👨‍👩‍👧‍👦
- Plain language explanations (zero medical jargon)
- Color-coded lab value cards (green = normal, yellow = attention needed)
- "Should I Be Concerned?" section with actionable guidance
- Patient-friendly confidence indicators

### 2. **Doctor Mode** 🩺
- Structured clinical detail view
- Lab values table with reference ranges
- Clinical reasoning summary
- 3-axis confidence breakdown (extraction, grounding, reasoning)
- Documented conditions, medications, follow-up actions

### 3. **Safety Classifier** 🛡️
- Blocks non-medical content, emergency scenarios, and personal advice requests
- **Gender-aware medical interpretation** (e.g., never mentions pregnancy for male patients even if HCG is elevated)
- Exponential backoff with automatic model fallback (Nova Micro → Nova Lite)

### 4. **3-Axis Confidence Calibration** 📊
- **Extraction Confidence:** How well structured data was extracted
- **Grounding Confidence:** Quality of medical evidence retrieved
- **Reasoning Confidence:** Coherence of clinical explanation
- **Overall Confidence:** High / Moderate / Low with patient-friendly messaging

### 5. **Medical RAG with Trust Scoring** 📚
- DynamoDB-cached medical glossary (NIH, WHO, CDC sources)
- Trust-scored retrieval (source authority + semantic relevance)
- Inline source citations in explanations

### 6. **PDF Export** 📄
- One-click download of patient-friendly summary
- Lab findings table, plain language explanation, confidence level
- Educational disclaimer footer

### 7. **Multi-turn Chat** 💬
- Ask follow-up questions about your report
- Grounded in your specific lab values
- Context-aware responses with safety guardrails

---

## ☁️ AWS Services Used

| Service | Purpose | Why We Chose It |
|---------|---------|-----------------|
| **Amazon Bedrock** | LLM inference (Nova Micro, Lite, Pro) | Cost-effective, low-latency, no infrastructure management |
| **Amazon Nova Micro** | Safety classification | Cheapest model for simple classification tasks |
| **Amazon Nova Lite** | Structured extraction & explanation | Balanced cost/performance for medical reasoning |
| **Amazon Nova Pro** | Complex multi-turn chat | High-quality responses for nuanced questions |
| **Amazon Textract** | OCR for PDF/image lab reports | Best-in-class medical document OCR |
| **Amazon DynamoDB** | Medical glossary cache | Sub-10ms lookups, serverless scaling |
| **Amazon EC2** | Streamlit app hosting | Simple deployment for hackathon demo |
| **Amazon CloudWatch** | Audit logging | PHI-safe compliance logging |
| **Amazon S3** | Temporary document storage | Secure, scalable file storage |

---

## 🏗️ Architecture Overview

**EFCI 6-Stage Pipeline:**

```
┌─────────────────┐
│ 1. Document     │  AWS Textract OCR
│    Processor    │  Extracts text from PDF/images
└────────┬────────┘
         │
┌────────▼────────┐
│ 2. Safety       │  Nova Micro (3 retries + fallback)
│    Classifier   │  Blocks non-clinical, emergency, advice requests
└────────┬────────┘
         │
┌────────▼────────┐
│ 3. EFCI         │  Nova Lite (structured extraction)
│    Orchestrator │  Lab values, conditions, medications
└────────┬────────┘
         │
┌────────▼────────┐
│ 4. Medical RAG  │  DynamoDB + semantic search
│    Grounding    │  NIH/WHO/CDC trusted sources
└────────┬────────┘
         │
┌────────▼────────┐
│ 5. Confidence   │  3-axis calibration
│    Calibrator   │  Extraction, Grounding, Reasoning
└────────┬────────┘
         │
┌────────▼────────┐
│ 6. Reason Graph │  Clinical reasoning chain
│    Builder      │  Transparent decision path
└─────────────────┘
```

**Key Design Decisions:**
- **3-tier model cascade:** Micro (safety) → Lite (extraction) → Pro (chat)
- **Exponential backoff:** 3 retries with 1s, 2s, 4s delays + automatic fallback
- **DynamoDB caching:** 95% cache hit rate, <10ms lookups
- **Gender-aware prompts:** Prevents dangerous medical misinterpretations
- **PHI-safe logging:** Audit logs to CloudWatch, no PII stored

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed technical design.

---

## 🛠️ Setup Instructions

### Prerequisites
- Python 3.9+
- AWS Account with Bedrock access (Nova models enabled)
- AWS CLI configured with credentials

### 1. Clone Repository
```bash
git clone https://github.com/YOUR_USERNAME/spashtai-efci.git
cd spashtai-efci
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment
Create a `.env` file in the project root:
```bash
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key

# DynamoDB
DYNAMODB_TABLE_NAME=efci-rag-cache

# S3
S3_BUCKET_NAME=your-bucket-name

# CloudWatch
CLOUDWATCH_LOG_GROUP=/spashtai/efci/audit

# Bedrock Models
SAFETY_MODEL_ARN=amazon.nova-micro-v1:0
EXTRACTION_MODEL_ARN=amazon.nova-lite-v1:0
CHAT_MODEL_ARN=amazon.nova-pro-v1:0
```

### 4. Create AWS Resources
```bash
# DynamoDB table
aws dynamodb create-table \
    --table-name efci-rag-cache \
    --attribute-definitions AttributeName=term,AttributeType=S \
    --key-schema AttributeName=term,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region us-east-1

# S3 bucket
aws s3 mb s3://your-bucket-name --region us-east-1

# CloudWatch log group
aws logs create-log-group --log-group-name /spashtai/efci/audit --region us-east-1
```

### 5. Load Medical Glossary
```bash
python -c "from retrieval.medical_rag import MedicalRAG; MedicalRAG().load_glossary_to_dynamodb('data/medical_glossary.json')"
```

### 6. Run Application
```bash
streamlit run app/main.py
```

The app will be available at `http://localhost:8501`

---

## 🎥 Demo

**Live Application:** [http://54.204.137.70:8501/](http://54.204.137.70:8501/)  
**Video Demo:** [https://youtu.be/sTgoSQuRjfs](https://youtu.be/sTgoSQuRjfs)

---

## 👥 Team

**Team Size:** 2 Developers

Built for the **AWS AI for Bharat Hackathon**

---

## 📋 Project Structure

```
spashtai-efci/
├── app/
│   ├── main.py              # Streamlit UI entry point
│   ├── ui_renderer.py       # Patient/Doctor view rendering
│   └── chat_engine.py       # Multi-turn chat logic
├── orchestrator/
│   └── efci_orchestrator.py # 6-stage EFCI pipeline
├── safety/
│   └── safety_classifier.py # Input safety gate
├── llm/
│   └── bedrock_client.py    # Bedrock API wrapper
├── ingestion/
│   └── document_processor.py # Textract OCR
├── retrieval/
│   └── medical_rag.py       # DynamoDB RAG
├── reasoning/
│   ├── confidence_calibrator.py # 3-axis confidence
│   └── reason_graph_builder.py  # Clinical reasoning
├── governance/
│   └── audit_logger.py      # CloudWatch logging
├── data/
│   └── medical_glossary.json # NIH/WHO/CDC sources
├── requirements.txt
├── README.md
├── ARCHITECTURE.md
├── DESIGN.md
└── REQUIREMENTS.md
```

---

## ⚠️ Disclaimer

**SpashtaAI is for educational purposes only.**

This application:
- Does NOT provide medical advice, diagnosis, or treatment
- Does NOT replace consultation with a qualified healthcare provider
- Should NOT be used for medical emergencies
- Is a demonstration project for the AWS AI for Bharat Hackathon

**Always consult a qualified healthcare provider for medical decisions.**

---

## 📄 License

This project is submitted for the AWS AI for Bharat Hackathon.

---

## 🙏 Acknowledgments

- **AWS Bedrock Team** for Nova model access
- **NIH, WHO, CDC** for public medical glossaries
- **AWS AI for Bharat Hackathon** organizers

---

**Built with ❤️ for 700 million Indians who deserve to understand their health**
