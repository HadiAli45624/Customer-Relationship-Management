import os
import os.path
import base64
from datetime import datetime
from dotenv import load_dotenv

# Google Libraries
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Gemini
from google.genai import Client

load_dotenv()

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.send'
]

# ──────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=8080)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


# ──────────────────────────────────────────────
# EMAIL FETCHING
# ──────────────────────────────────────────────

def fetch_emails(service, mode="inbound", target_email=None, max_results=5):
    """
    mode='inbound'  → emails received FROM target (or full inbox)
    mode='outbound' → emails YOU sent TO target (or full sent folder)
    """
    if mode == "inbound":
        if target_email:
            query = f"from:{target_email}"
        else:
            query = "label:INBOX"
        label_ids = ["INBOX"]
    else:  # outbound
        if target_email:
            query = f"to:{target_email}"
        else:
            query = "in:sent"
        label_ids = ["SENT"]

    results = service.users().messages().list(
        userId='me',
        q=query,
        maxResults=max_results,
        labelIds=label_ids
    ).execute()

    messages = results.get('messages', [])
    if not messages:
        return [], ""

    email_list = []
    email_content = ""

    for msg in messages:
        m = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()

        headers = {h['name']: h['value'] for h in m['payload'].get('headers', [])}
        subject = headers.get('Subject', '(No Subject)')
        sender  = headers.get('From', 'Unknown')
        to      = headers.get('To', 'Unknown')
        date    = headers.get('Date', 'Unknown')
        snippet = m.get('snippet', '')

        email_list.append({
            'id': msg['id'],
            'subject': subject,
            'from': sender,
            'to': to,
            'date': date,
            'snippet': snippet
        })

        email_content += (
            f"\n--- Email ---"
            f"\nDate: {date}"
            f"\nFrom: {sender}"
            f"\nTo: {to}"
            f"\nSubject: {subject}"
            f"\nSnippet: {snippet}\n"
        )

    return email_list, email_content


# ──────────────────────────────────────────────
# AI ANALYSIS (GROQ)
# ──────────────────────────────────────────────

def analyze_and_draft(email_history, mode, lead_name="the contact", lead_email=""):
    client = Client(api_key=os.getenv("GEMINI_API_KEY"))

    if mode == "inbound":
        task_prompt = f"""
You are analyzing INBOUND emails (received FROM {lead_name}).

Tasks:
1. SUMMARY: Briefly summarize what {lead_name} has been saying/asking (2-3 sentences).
2. SENTIMENT: What is their tone? (positive / neutral / negative / urgent)
3. ACTION NEEDED: What does {lead_name} need or expect from you?
4. DRAFT REPLY: Write a professional, friendly reply (3-5 sentences) that directly addresses their latest message.
        """
    else:  # outbound
        task_prompt = f"""
You are analyzing OUTBOUND emails (sent BY YOU to {lead_name}).

Tasks:
1. SUMMARY: Briefly summarize what you have been communicating to {lead_name} (2-3 sentences).
2. LAST ACTION: What was the last thing you asked or offered {lead_name}?
3. FOLLOW-UP NEEDED: Has enough been communicated, or is a follow-up needed? Why?
4. DRAFT FOLLOW-UP: Write a concise, friendly follow-up email (3-5 sentences) referencing a specific detail from the history. Do NOT be pushy.
        """

    prompt = f"""
Context: You are a smart email assistant for a startup founder.

Email History:
{email_history}

Lead/Contact Name: {lead_name}
Lead/Contact Email: {lead_email}

{task_prompt}

Format your response clearly with these exact headers:
### SUMMARY
### SENTIMENT (inbound only) / ### LAST ACTION (outbound only)
### ACTION NEEDED / ### FOLLOW-UP NEEDED
### DRAFT EMAIL
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text


# ──────────────────────────────────────────────
# SEND EMAIL (optional)
# ──────────────────────────────────────────────

def send_email(service, to_email, subject, body):
    """Creates a draft or sends an email."""
    import email.mime.text
    import email.mime.multipart

    message = email.mime.multipart.MIMEMultipart()
    message['to'] = to_email
    message['subject'] = subject
    message.attach(email.mime.text.MIMEText(body, 'plain'))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    # Save as draft instead of sending directly (safer)
    draft = service.users().drafts().create(
        userId='me',
        body={'message': {'raw': raw}}
    ).execute()
    return draft


# ──────────────────────────────────────────────
# CLI DISPLAY HELPERS
# ──────────────────────────────────────────────

def print_header():
    print("\n" + "="*55)
    print("       📧  CRM Email Assistant  📧")
    print("="*55)

def print_emails(email_list, mode):
    direction = "RECEIVED" if mode == "inbound" else "SENT"
    print(f"\n📬 {direction} EMAILS ({len(email_list)} found):")
    print("-" * 55)
    for i, e in enumerate(email_list, 1):
        print(f"  [{i}] {e['subject'][:45]}")
        print(f"       From: {e['from'][:40]}")
        print(f"       Date: {e['date'][:30]}")
        print()


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    print_header()

    # Step 1: Choose mode
    print("\nWhat would you like to do?")
    print("  [1] Analyze INBOUND emails (emails received FROM someone)")
    print("  [2] Analyze OUTBOUND emails (emails YOU sent TO someone)")
    mode_choice = input("\nEnter 1 or 2: ").strip()

    if mode_choice == "1":
        mode = "inbound"
        print("\n✅ Mode: INBOUND")
    elif mode_choice == "2":
        mode = "outbound"
        print("\n✅ Mode: OUTBOUND")
    else:
        print("Invalid choice. Defaulting to inbound.")
        mode = "inbound"

    # Step 2: Get target
    target = input("\nEnter the contact's email address (or leave blank for full folder): ").strip()
    lead_name = input("Enter their name (or leave blank): ").strip() or "the contact"

    # Step 3: How many emails
    try:
        max_results = int(input("How many recent emails to fetch? (default 5): ").strip() or "5")
    except ValueError:
        max_results = 5

    # Step 4: Fetch
    print("\n⏳ Connecting to Gmail...")
    service = get_gmail_service()

    print(f"⏳ Fetching {mode} emails...")
    email_list, email_content = fetch_emails(
        service,
        mode=mode,
        target_email=target if target else None,
        max_results=max_results
    )

    if not email_list:
        print("\n❌ No emails found. Try a different email or mode.")
        return

    # Step 5: Display emails found
    print_emails(email_list, mode)

    # Step 6: AI Analysis
    print("🤖 Analyzing with AI...\n")
    analysis = analyze_and_draft(email_content, mode, lead_name, target)

    print("=" * 55)
    print("           AI ANALYSIS & DRAFT")
    print("=" * 55)
    print(analysis)
    print("=" * 55)

    # Step 7: Optionally save draft
    if target:
        save = input("\n💾 Save the drafted email as a Gmail Draft? (y/n): ").strip().lower()
        if save == 'y':
            # Extract just the draft part from the analysis
            draft_section = ""
            if "### DRAFT EMAIL" in analysis:
                draft_section = analysis.split("### DRAFT EMAIL")[-1].strip()

            subject_input = input("Enter email subject: ").strip()
            service_result = send_email(service, target, subject_input, draft_section)
            print(f"\n✅ Draft saved! Draft ID: {service_result.get('id')}")
            print("   Check your Gmail Drafts folder.")

    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()