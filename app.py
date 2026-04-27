import os
import os.path
import base64
from datetime import datetime, timedelta
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
    'https://www.googleapis.com/auth/gmail.send',
    # ── NEW SCOPES ──
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/contacts.readonly',
]

# ──────────────────────────────────────────────
# AUTH  (single credential object, all APIs)
# ──────────────────────────────────────────────

def get_credentials():
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
    return creds


def get_services():
    """Build and return all Google API service clients."""
    creds = get_credentials()
    gmail     = build('gmail',   'v1',     credentials=creds)
    sheets    = build('sheets',  'v4',     credentials=creds)
    calendar  = build('calendar','v3',     credentials=creds)
    people    = build('people',  'v1',     credentials=creds)
    return gmail, sheets, calendar, people


# ──────────────────────────────────────────────
# EMAIL FETCHING
# ──────────────────────────────────────────────

def fetch_emails(service, mode="inbound", target_email=None, max_results=5):
    """
    mode='inbound'  → emails received FROM target (or full inbox)
    mode='outbound' → emails YOU sent TO target (or full sent folder)
    """
    if mode == "inbound":
        query     = f"from:{target_email}" if target_email else "label:INBOX"
        label_ids = ["INBOX"]
    else:
        query     = f"to:{target_email}" if target_email else "in:sent"
        label_ids = ["SENT"]

    results = service.users().messages().list(
        userId='me', q=query, maxResults=max_results, labelIds=label_ids
    ).execute()

    messages = results.get('messages', [])
    if not messages:
        return [], ""

    email_list    = []
    email_content = ""

    for msg in messages:
        m = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        headers = {h['name']: h['value'] for h in m['payload'].get('headers', [])}
        subject = headers.get('Subject', '(No Subject)')
        sender  = headers.get('From', 'Unknown')
        to      = headers.get('To',   'Unknown')
        date    = headers.get('Date', 'Unknown')
        snippet = m.get('snippet', '')

        email_list.append({
            'id': msg['id'], 'subject': subject,
            'from': sender,  'to': to,
            'date': date,    'snippet': snippet
        })
        email_content += (
            f"\n--- Email ---"
            f"\nDate: {date}\nFrom: {sender}\nTo: {to}"
            f"\nSubject: {subject}\nSnippet: {snippet}\n"
        )

    return email_list, email_content


# ──────────────────────────────────────────────
# GOOGLE PEOPLE API — auto-resolve contact name
# ──────────────────────────────────────────────

def lookup_contact_name(people_service, email_address):
    """
    Search Google Contacts for a contact matching the given email address.
    Returns their display name, or None if not found.
    """
    try:
        results = people_service.people().searchContacts(
            query=email_address,
            readMask='names,emailAddresses'
        ).execute()

        for person in results.get('results', []):
            p = person.get('person', {})
            emails = p.get('emailAddresses', [])
            for e in emails:
                if e.get('value', '').lower() == email_address.lower():
                    names = p.get('names', [])
                    if names:
                        return names[0].get('displayName')
    except Exception as ex:
        print(f"  ⚠️  Could not fetch contact: {ex}")
    return None


# ──────────────────────────────────────────────
# AI ANALYSIS (Gemini)
# ──────────────────────────────────────────────

def analyze_and_draft(email_history, mode, lead_name="the contact", lead_email=""):
    client = Client(api_key=os.getenv("GEMINI_API_KEY"))

    if mode == "inbound":
        task_prompt = f"""
You are analyzing INBOUND emails (received FROM {lead_name}).

Tasks:
1. SUMMARY: Briefly summarize what {lead_name} has been saying/asking (2-3 sentences).
2. SENTIMENT: What is their tone? (positive / neutral / negative / urgent)
3. LEAD SCORE: Rate this lead from 1–10 based on their engagement and intent (10 = highly interested).
4. ACTION NEEDED: What does {lead_name} need or expect from you?
5. FOLLOW-UP DATE: Suggest a follow-up date (e.g., "3 days", "1 week"). Be specific.
6. DRAFT REPLY: Write a professional, friendly reply (3-5 sentences) that directly addresses their latest message.
        """
    else:
        task_prompt = f"""
You are analyzing OUTBOUND emails (sent BY YOU to {lead_name}).

Tasks:
1. SUMMARY: Briefly summarize what you have been communicating to {lead_name} (2-3 sentences).
2. LAST ACTION: What was the last thing you asked or offered {lead_name}?
3. LEAD SCORE: Rate this lead from 1–10 based on the conversation momentum (10 = great progress).
4. FOLLOW-UP NEEDED: Has enough been communicated, or is a follow-up needed? Why?
5. FOLLOW-UP DATE: Suggest a follow-up date (e.g., "3 days", "1 week"). Be specific.
6. DRAFT FOLLOW-UP: Write a concise, friendly follow-up email (3-5 sentences) referencing a specific detail from the history. Do NOT be pushy.
        """

    prompt = f"""
Context: You are a smart email assistant for a startup founder.

Email History:
{email_history}

Lead/Contact Name: {lead_name}
Lead/Contact Email: {lead_email}

{task_prompt}

Format your response with these EXACT headers (no extra text before the first header):
### SUMMARY
### SENTIMENT
### LEAD SCORE
### ACTION NEEDED
### FOLLOW-UP DATE
### DRAFT EMAIL
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text


# ──────────────────────────────────────────────
# PARSE HELPERS
# ──────────────────────────────────────────────

def _extract_section(analysis: str, header: str) -> str:
    """Pull the text under a ### HEADER up to the next ### header."""
    if header not in analysis:
        return ""
    after = analysis.split(header, 1)[1]
    # stop at next ### section
    if "###" in after:
        after = after.split("###", 1)[0]
    return after.strip()


def parse_lead_score(analysis: str) -> int:
    """Return an integer 1-10 from the LEAD SCORE section."""
    section = _extract_section(analysis, "### LEAD SCORE")
    for token in section.split():
        token = token.strip(".,/")
        if token.isdigit():
            val = int(token)
            if 1 <= val <= 10:
                return val
    return 5  # default


def parse_follow_up_days(analysis: str) -> int:
    """Return the number of days until follow-up from FOLLOW-UP DATE section."""
    section = _extract_section(analysis, "### FOLLOW-UP DATE").lower()
    import re
    match = re.search(r'(\d+)\s*(day|week|month)', section)
    if match:
        num  = int(match.group(1))
        unit = match.group(2)
        if unit == "week":  return num * 7
        if unit == "month": return num * 30
        return num
    return 3  # default: 3 days


def parse_draft_email(analysis: str) -> str:
    return _extract_section(analysis, "### DRAFT EMAIL")


# ──────────────────────────────────────────────
# GOOGLE SHEETS — export lead data
# ──────────────────────────────────────────────

SHEET_NAME = "CRM Leads"
HEADERS    = ["Timestamp", "Lead Name", "Lead Email", "Mode",
              "Lead Score", "Summary", "Sentiment / Last Action",
              "Action Needed / Follow-Up Needed", "Follow-Up Date", "Draft Email"]


def get_or_create_sheet(sheets_service):
    """
    Look for an existing spreadsheet named CRM Leads in Drive.
    If not found, create one and add headers.
    Returns the spreadsheet ID.
    """
    # Store sheet ID locally so we reuse the same sheet across runs
    id_file = ".crm_sheet_id"
    if os.path.exists(id_file):
        with open(id_file) as f:
            return f.read().strip()

    # Create new spreadsheet
    body = {
        "properties": {"title": SHEET_NAME},
        "sheets": [{"properties": {"title": "Leads"}}]
    }
    sheet = sheets_service.spreadsheets().create(body=body).execute()
    sid   = sheet["spreadsheetId"]

    # Write headers
    sheets_service.spreadsheets().values().update(
        spreadsheetId=sid,
        range="Leads!A1",
        valueInputOption="RAW",
        body={"values": [HEADERS]}
    ).execute()

    with open(id_file, "w") as f:
        f.write(sid)

    print(f"\n📊 Created Google Sheet: https://docs.google.com/spreadsheets/d/{sid}")
    return sid


def export_to_sheet(sheets_service, lead_name, lead_email, mode, analysis):
    """Append one row of lead data to the CRM Google Sheet."""
    sid = get_or_create_sheet(sheets_service)

    score       = parse_lead_score(analysis)
    days        = parse_follow_up_days(analysis)
    follow_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    summary     = _extract_section(analysis, "### SUMMARY")
    sentiment   = (_extract_section(analysis, "### SENTIMENT")
                   or _extract_section(analysis, "### LAST ACTION"))
    action      = (_extract_section(analysis, "### ACTION NEEDED")
                   or _extract_section(analysis, "### FOLLOW-UP NEEDED"))
    draft       = parse_draft_email(analysis)

    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        lead_name, lead_email, mode,
        score, summary[:300], sentiment[:200],
        action[:300], follow_date, draft[:500]
    ]

    sheets_service.spreadsheets().values().append(
        spreadsheetId=sid,
        range="Leads!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]}
    ).execute()

    print(f"\n📊 Lead exported to Google Sheet (row added).")
    print(f"   🔗 https://docs.google.com/spreadsheets/d/{sid}")
    return sid, score, days


# ──────────────────────────────────────────────
# GOOGLE CALENDAR — create follow-up reminder
# ──────────────────────────────────────────────

def create_calendar_reminder(calendar_service, lead_name, lead_email, days_from_now, mode):
    """Create a follow-up Calendar event N days from today."""
    follow_up_date = datetime.now() + timedelta(days=days_from_now)
    date_str       = follow_up_date.strftime("%Y-%m-%d")

    action_word = "reply to" if mode == "inbound" else "follow up with"
    title       = f"📧 {action_word.capitalize()} {lead_name}"
    description = (
        f"CRM Reminder: {action_word} {lead_name} ({lead_email}).\n"
        f"Auto-created by CRM Email Assistant on {datetime.now().strftime('%Y-%m-%d')}."
    )

    event = {
        "summary":     title,
        "description": description,
        "start": {"date": date_str},
        "end":   {"date": date_str},
        "reminders": {
            "useDefault": False,
            "overrides":  [{"method": "email", "minutes": 9 * 60},
                           {"method": "popup", "minutes": 30}]
        },
        "attendees": [{"email": lead_email}]
    }

    created = calendar_service.events().insert(
        calendarId='primary', body=event
    ).execute()

    print(f"\n📅 Calendar reminder created for {date_str}.")
    print(f"   🔗 {created.get('htmlLink')}")
    return created


# ──────────────────────────────────────────────
# SEND / DRAFT EMAIL
# ──────────────────────────────────────────────

def send_email(service, to_email, subject, body):
    """Save a draft to Gmail Drafts folder."""
    import email.mime.text
    import email.mime.multipart

    message = email.mime.multipart.MIMEMultipart()
    message['to']      = to_email
    message['subject'] = subject
    message.attach(email.mime.text.MIMEText(body, 'plain'))

    raw   = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId='me', body={'message': {'raw': raw}}
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

    # ── Step 1: Build all services ──
    print("\n⏳ Connecting to Google APIs...")
    gmail, sheets, calendar, people = get_services()
    print("✅ Connected to Gmail, Sheets, Calendar & People APIs.")

    # ── Step 2: Choose mode ──
    print("\nWhat would you like to do?")
    print("  [1] Analyze INBOUND emails (emails received FROM someone)")
    print("  [2] Analyze OUTBOUND emails (emails YOU sent TO someone)")
    mode_choice = input("\nEnter 1 or 2: ").strip()

    mode = "inbound" if mode_choice == "1" else "outbound"
    print(f"\n✅ Mode: {mode.upper()}")

    # ── Step 3: Get target email ──
    target = input("\nEnter the contact's email address (or leave blank for full folder): ").strip()

    # ── Step 4: Auto-resolve name from Google Contacts ──
    lead_name = None
    if target:
        print("🔍 Looking up contact in Google Contacts...")
        lead_name = lookup_contact_name(people, target)
        if lead_name:
            print(f"   ✅ Found: {lead_name}")
        else:
            print("   ℹ️  Not found in Contacts.")

    if not lead_name:
        lead_name = input("Enter their name (or leave blank): ").strip() or "the contact"

    # ── Step 5: How many emails ──
    try:
        max_results = int(input("How many recent emails to fetch? (default 5): ").strip() or "5")
    except ValueError:
        max_results = 5

    # ── Step 6: Fetch emails ──
    print(f"\n⏳ Fetching {mode} emails...")
    email_list, email_content = fetch_emails(
        gmail, mode=mode,
        target_email=target if target else None,
        max_results=max_results
    )

    if not email_list:
        print("\n❌ No emails found. Try a different email or mode.")
        return

    print_emails(email_list, mode)

    # ── Step 7: AI analysis ──
    print("🤖 Analyzing with Gemini AI...\n")
    analysis = analyze_and_draft(email_content, mode, lead_name, target)

    print("=" * 55)
    print("           AI ANALYSIS & DRAFT")
    print("=" * 55)
    print(analysis)
    print("=" * 55)

    # ── Step 8: Export to Google Sheets ──
    export_choice = input("\n📊 Export lead data to Google Sheets? (y/n): ").strip().lower()
    days_for_calendar = 3  # fallback
    if export_choice == 'y':
        try:
            _, score, days_for_calendar = export_to_sheet(
                sheets, lead_name, target or "N/A", mode, analysis
            )
            print(f"   Lead Score: {score}/10  |  Follow-up in {days_for_calendar} days")
        except Exception as ex:
            print(f"   ⚠️  Sheets export failed: {ex}")

    # ── Step 9: Create Calendar reminder ──
    if target:
        cal_choice = input("\n📅 Create a follow-up reminder in Google Calendar? (y/n): ").strip().lower()
        if cal_choice == 'y':
            try:
                create_calendar_reminder(
                    calendar, lead_name, target, days_for_calendar, mode
                )
            except Exception as ex:
                print(f"   ⚠️  Calendar reminder failed: {ex}")

    # ── Step 10: Save Gmail draft ──
    if target:
        save = input("\n💾 Save the drafted email as a Gmail Draft? (y/n): ").strip().lower()
        if save == 'y':
            draft_body = parse_draft_email(analysis)
            subject_input = input("Enter email subject: ").strip()
            result = send_email(gmail, target, subject_input, draft_body)
            print(f"\n✅ Draft saved! Draft ID: {result.get('id')}")
            print("   Check your Gmail Drafts folder.")

    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()