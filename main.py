import boto3
import re
import pandas as pd
import streamlit as st
from word2number import w2n
from dateutil import parser

# AWS Configuration
bucket_name = "cheque-upload-akshit"
s3_output_path = "processed/cheque_details.xlsx"
excel_file_name = "cheque_details.xlsx"

# Initialize AWS clients
s3 = boto3.client("s3", region_name="ap-south-1")
textract = boto3.client("textract", region_name="ap-south-1")

# Streamlit UI
st.title("Cheque Information Extractor")
st.write("Upload cheque images to extract payee, amount, date, and account details with Indian number formatting support.")

# File uploader
uploaded_files = st.file_uploader(
    "📤 Upload Cheque Images (JPG/PNG)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True,
)

def parse_indian_number(num_str):
    try:
        return int(num_str.replace(',', ''))
    except:
        return None

def extract_amount(text_lines):
    for line in text_lines:
        match = re.search(r'(?:₹|Rs?\.?)\s*([\d,]+(?:,\d{2})*)\s*(?:/-)?', line)
        if match:
            amount = parse_indian_number(match.group(1))
            if amount and 100 <= amount <= 1000000000:
                return amount
    
    for line in text_lines:
        match = re.search(
            r'(?:Rupees|Rs?\.?|INR)\s+([\w\s-]+?(?:lakh|crore|thousand|hundred)[\w\s-]*)(?:\s+only)?',
            line,
            re.IGNORECASE
        )
        if match:
            try:
                words = match.group(1).replace(' and ', ' ').replace('-', ' ').strip()
                return w2n.word_to_num(words)
            except:
                continue
    
    max_amount = 0
    for line in text_lines:
        numbers = re.findall(r'\b\d{1,3}(?:,\d{2,3})+\b|\b\d{4,}\b', line)
        for num in numbers:
            amount = parse_indian_number(num.replace('/-', ''))
            if amount and 1000 <= amount <= 1000000000 and amount > max_amount:
                max_amount = amount
    return max_amount if max_amount > 0 else None

def extract_account_number(text_lines):
    for line in text_lines:
        match = re.search(r'(?:A/c|Account)\s*No[.:]?\s*([A-Za-z]?\d{9,18})\b', line, re.IGNORECASE)
        if match:
            acc_num = match.group(1)
            if not re.search(r'[A-Z]{4}0[A-Z0-9]{6}', acc_num):
                return acc_num
    
    candidates = []
    for line in text_lines:
        matches = re.findall(r'\b([A-Za-z]?\d{9,18})\b', line)
        for num in matches:
            if not (re.fullmatch(r'\d{6,8}', num) or re.search(r'ICIC\d{4}', num)):
                candidates.append(num)
    
    return max(candidates, key=len) if candidates else None

def extract_cheque_details(file_name):
    response = textract.analyze_document(
        Document={"S3Object": {"Bucket": bucket_name, "Name": file_name}},
        FeatureTypes=["FORMS"],
    )
    
    lines = [item["Text"] for item in response["Blocks"] if item["BlockType"] == "LINE"]
    
    noise_terms = [
        'VALID FOR THREE MONTHS', 'Please sign above', 'n°', 
        'Shossigned', 'ODMMYYYY', 'RS CODE -', 'DATE'
    ]
    cleaned_lines = [line for line in lines if not any(term in line for term in noise_terms)]
    
    payee = None
    for i, line in enumerate(cleaned_lines):
        if re.search(r'Pay|PAY TO|Order of', line, re.IGNORECASE):
            for offset in range(1, 3):
                if i + offset < len(cleaned_lines):
                    name = cleaned_lines[i + offset].strip()
                    if "OR BEARER" not in name and 3 <= len(name.split()) <= 6:
                        payee = name
                        break
            if payee:
                break
    
    cheque_date = None
    for line in cleaned_lines:
        match = re.search(r'\b(\d{2}[/-]\d{2}[/-]\d{2,4})\b|\b(\d{8})\b', line)
        if match:
            date_str = match.group(1) or match.group(2)
            try:
                if len(date_str) == 8 and date_str.isdigit():
                    date_str = f"{date_str[:2]}/{date_str[2:4]}/{date_str[4:]}"
                cheque_date = parser.parse(date_str, dayfirst=True).strftime("%d-%m-%Y")
                break
            except:
                continue
    
    bank = next((line for line in cleaned_lines if re.search(r'Bank|BANK', line)), None)
    
    amount = extract_amount(cleaned_lines)
    account = extract_account_number(cleaned_lines)
    
    return {
        "file_name": file_name,
        "payee": payee or "Not found",
        "date": cheque_date or "Not found",
        "amount": f"₹ {amount:,}" if amount else "Not found",
        "bank": bank or "Not found",
        "account": account or "Not found"
    }

# Main processing
if uploaded_files:
    extracted_data = []
    
    for uploaded_file in uploaded_files:
        with st.spinner(f"Processing {uploaded_file.name}..."):
            # Upload to S3 under "uploads/" prefix
            upload_key = f"uploads/{uploaded_file.name}"
            s3.upload_fileobj(uploaded_file, bucket_name, upload_key)

            # Extract details using S3 path
            details = extract_cheque_details(upload_key)
            extracted_data.append(details)

            # Display results
            st.subheader(f"Results for {uploaded_file.name}")
            single_df = pd.DataFrame(details.items(), columns=["Field", "Value"])
            st.table(single_df)


    
    # Save to Excel
    if extracted_data:
        df = pd.DataFrame(extracted_data)
        df.to_excel(excel_file_name, index=False)

        # Upload to S3 and provide download
        try:
            s3.upload_file(excel_file_name, bucket_name, s3_output_path)
            st.success("All cheques processed successfully!")
            
            with open(excel_file_name, "rb") as f:
                st.download_button(
                    "⬇️ Download Excel Report",
                    f.read(),
                    file_name=excel_file_name,
                    mime="application/vnd.ms-excel"
                )
        except Exception as e:
            st.error(f"Error saving results: {str(e)}")
