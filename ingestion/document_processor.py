import os
import uuid
import time
import re
import boto3


# ------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------

TEMP_BUCKET = os.getenv("TEMP_S3_BUCKET")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

if not TEMP_BUCKET:
    raise ValueError("TEMP_S3_BUCKET not set in environment variables")


# ------------------------------------------------------------------
# AWS Clients
# ------------------------------------------------------------------

s3 = boto3.client("s3", region_name=AWS_REGION)
textract = boto3.client("textract", region_name=AWS_REGION)


# ------------------------------------------------------------------
# Text Cleaner (CRITICAL FIX)
# ------------------------------------------------------------------

def clean_extracted_text(text: str) -> str:
    """
    Cleans noisy Textract output from lab reports and discharge summaries.
    Removes headers, disclaimers, repeated boilerplate, and excessive whitespace.
    """

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    # Remove common lab boilerplate blocks
    remove_phrases = [
        "IMPORTANT INSTRUCTIONS",
        "End of report",
        "Regd. Office",
        "Web:",
        "CIN No.",
        "Page 1 of",
        "Page 2 of",
        "Sample drawn from outside source",
        "Tests conducted at Referral Lab",
        "Contact customer care",
        "The Courts/Forum at Delhi",
        "Test results are not valid for medico legal purposes",
    ]

    for phrase in remove_phrases:
        text = text.replace(phrase, "")

    # Remove watermark artifacts
    text = text.replace("Sample Report", "")

    # Trim again
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ------------------------------------------------------------------
# Main PDF/Text Extraction Function
# ------------------------------------------------------------------

def extract_text_from_document(uploaded_file) -> str:
    """
    Uploads PDF to S3, runs Textract async detection,
    collects text, cleans it, and deletes temp object.
    """

    # Generate unique S3 key
    temp_key = f"temp/{uuid.uuid4()}.pdf"

    # IMPORTANT: Reset pointer before upload
    uploaded_file.seek(0)

    # Upload to S3
    s3.upload_fileobj(uploaded_file, TEMP_BUCKET, temp_key)

    try:
        # Start async Textract job
        response = textract.start_document_text_detection(
            DocumentLocation={
                "S3Object": {
                    "Bucket": TEMP_BUCKET,
                    "Name": temp_key,
                }
            }
        )

        job_id = response["JobId"]

        # Poll for completion
        while True:
            job_status = textract.get_document_text_detection(JobId=job_id)

            status = job_status["JobStatus"]

            if status in ["SUCCEEDED", "FAILED"]:
                break

            time.sleep(2)

        if status != "SUCCEEDED":
            raise Exception("Textract job failed")

        # Collect all lines
        extracted_lines = []

        next_token = None

        while True:
            if next_token:
                job_status = textract.get_document_text_detection(
                    JobId=job_id,
                    NextToken=next_token
                )
            else:
                job_status = textract.get_document_text_detection(JobId=job_id)

            for block in job_status["Blocks"]:
                if block["BlockType"] == "LINE":
                    extracted_lines.append(block["Text"])

            next_token = job_status.get("NextToken")

            if not next_token:
                break

        raw_text = "\n".join(extracted_lines)

        # Clean noisy lab boilerplate
        cleaned_text = clean_extracted_text(raw_text)

        return cleaned_text

    finally:
        # Delete temp file from S3
        try:
            s3.delete_object(Bucket=TEMP_BUCKET, Key=temp_key)
        except Exception:
            pass