import os, base64, re, secrets
import requests as http_requests
from datetime import datetime, timedelta
from flask import Flask, redirect, url_for, session, request, jsonify, render_template
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google.genai import Client
from dotenv import load_dotenv
import email.mime.text, email.mime.multipart

load_dotenv()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # local dev only

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/contacts.readonly',
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
]

def get_services():
    if 'credentials' not in session:
        return None, None, None, None
    creds = Credentials(**session['credentials'])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        session['credentials'] = creds_to_dict(creds)
    return (build('gmail','v1',credentials=creds), build('sheets','v4',credentials=creds),
            build('calendar','v3',credentials=creds), build('people','v1',credentials=creds))

def creds_to_dict(c):
    return {'token':c.token,'refresh_token':c.refresh_token,'token_uri':c.token_uri,
            'client_id':c.client_id,'client_secret':c.client_secret,'scopes':c.scopes}

def extract_section(text, header):
    if header not in text: return ""
    after = text.split(header,1)[1]
    return after.split("###",1)[0].strip() if "###" in after else after.strip()

def parse_score(text):
    for tok in extract_section(text,"### LEAD SCORE").split():
        tok=tok.strip(".,/()")
        if tok.isdigit() and 1<=int(tok)<=10: return int(tok)
    return 5

def parse_days(text):
    m=re.search(r'(\d+)\s*(day|week|month)',extract_section(text,"### FOLLOW-UP DATE").lower())
    if m:
        n,u=int(m.group(1)),m.group(2)
        return n*(7 if u=="week" else 30 if u=="month" else 1)
    return 3

GOOGLE_CLIENT_SECRETS = None

def get_client_secrets():
    global GOOGLE_CLIENT_SECRETS
    if not GOOGLE_CLIENT_SECRETS:
        import json
        
        # Try environment variable first (for production/Vercel)
        creds_json = os.getenv('GOOGLE_CREDENTIALS')
        if creds_json:
            try:
                data = json.loads(creds_json)
                GOOGLE_CLIENT_SECRETS = data.get('web', data.get('installed'))
                return GOOGLE_CLIENT_SECRETS
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid GOOGLE_CREDENTIALS JSON: {e}")
        
        # Fall back to local file (for local development)
        try:
            with open('credentials.json') as f:
                data = json.load(f)
            GOOGLE_CLIENT_SECRETS = data.get('web', data.get('installed'))
        except FileNotFoundError:
            raise ValueError("credentials.json not found and GOOGLE_CREDENTIALS environment variable not set")
    
    return GOOGLE_CLIENT_SECRETS

@app.route('/')
def index():
    return render_template('index.html', logged_in='credentials' in session,
                           user_name=session.get('user_name',''), user_email=session.get('user_email',''))

@app.route('/authorize')
def authorize():
    secrets_data = get_client_secrets()
    state = secrets.token_urlsafe(16)
    redirect_uri = url_for('oauth2callback', _external=True)
    params = {
        'client_id': secrets_data['client_id'],
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
    }
    session['state'] = state
    session['redirect_uri'] = redirect_uri
    auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + '&'.join(f"{k}={v}" for k,v in params.items())
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    if request.args.get('state') != session.get('state'):
        return 'State mismatch', 400

    secrets_data = get_client_secrets()
    code = request.args.get('code')

    token_response = http_requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': secrets_data['client_id'],
        'client_secret': secrets_data['client_secret'],
        'redirect_uri': session.get('redirect_uri'),
        'grant_type': 'authorization_code',
    })

    tokens = token_response.json()
    if 'error' in tokens:
        return jsonify(tokens), 400

    session['credentials'] = {
        'token': tokens['access_token'],
        'refresh_token': tokens.get('refresh_token'),
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': secrets_data['client_id'],
        'client_secret': secrets_data['client_secret'],
        'scopes': SCOPES,
    }

    try:
        info = http_requests.get('https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization': f"Bearer {tokens['access_token']}"}).json()
        session['user_name']  = info.get('name', '')
        session['user_email'] = info.get('email', '')
    except: pass

    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'credentials' not in session: return redirect(url_for('index'))
    return render_template('dashboard.html',
                           user_name=session.get('user_name',''), user_email=session.get('user_email',''))

@app.route('/api/lookup-contact', methods=['POST'])
def lookup_contact():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    email_addr = request.json.get('email','').strip()
    _,_,_,people = get_services()
    lead_name = ''
    if email_addr and people:
        try:
            res = people.people().searchContacts(query=email_addr, readMask='names,emailAddresses').execute()
            for person in res.get('results',[]):
                p = person.get('person',{})
                for e in p.get('emailAddresses',[]):
                    if e.get('value','').lower()==email_addr.lower():
                        names=p.get('names',[])
                        if names: lead_name=names[0].get('displayName','')
        except: pass
    return jsonify({'lead_name': lead_name})

def get_message_body(payload):
    """Recursively extract plain text body from a Gmail message payload."""
    if payload.get('mimeType') == 'text/plain':
        data = payload.get('body', {}).get('data', '')
        if data:
            return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='ignore')
    for part in payload.get('parts', []):
        result = get_message_body(part)
        if result:
            return result
    return ''

@app.route('/api/fetch-emails', methods=['POST'])
def fetch_emails():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    data = request.json
    target_email = data.get('email','').strip()
    mode = data.get('mode','inbound')
    max_results = int(data.get('max_results',5))
    gmail,_,_,_ = get_services()

    if mode == 'inbound':
        query = f"from:{target_email}"
    else:
        query = f"(to:{target_email} OR from:{target_email})"

    try:
        res = gmail.users().messages().list(
            userId='me', q=query, maxResults=max_results
        ).execute()
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500

    messages = res.get('messages', [])
    if not messages:
        return jsonify({'error': 'No emails found for this address and mode.'}), 404

    # Use however many emails actually exist (may be fewer than requested)
    messages = messages[:max_results]

    email_list, email_content = [], ""
    my_email = session.get('user_email', '').lower()

    for msg in messages:
        m = gmail.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        hdrs = {h['name']: h['value'] for h in m['payload'].get('headers', [])}
        from_addr = hdrs.get('From', 'Unknown')
        to_addr   = hdrs.get('To', 'Unknown')
        date      = hdrs.get('Date', 'Unknown')
        subject   = hdrs.get('Subject', '(No Subject)')
        snippet   = m.get('snippet', '')

        sender_tag = 'YOU' if my_email and my_email in from_addr.lower() else 'THEM'

        body = ''
        if mode == 'outbound':
            body = get_message_body(m['payload'])
            body = body[:600].strip() if body else snippet

        row = {
            'subject': subject,
            'from': from_addr,
            'to': to_addr,
            'date': date,
            'snippet': snippet,
            'direction': sender_tag
        }
        email_list.append(row)

        if mode == 'outbound':
            email_content += (
                f"\n--- [{sender_tag}] {date} ---\n"
                f"Subject: {subject}\n"
                f"From: {from_addr}\nTo: {to_addr}\n"
                f"Body: {body or snippet}\n"
            )
        else:
            email_content += (
                f"\n--- Email ---\n"
                f"Date: {date}\nFrom: {from_addr}\nTo: {to_addr}\n"
                f"Subject: {subject}\nSnippet: {snippet}\n"
            )

    return jsonify({'emails': email_list, 'email_content': email_content})

@app.route('/api/analyze', methods=['POST'])
def analyze():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    data = request.json
    email_content = data.get('email_content','')
    mode = data.get('mode','inbound')
    lead_name = data.get('lead_name','the contact')
    lead_email = data.get('lead_email','')
    task = f"""
You are analyzing {'INBOUND emails (received FROM' if mode=='inbound' else 'a full email CONVERSATION WITH'} {lead_name}).
{"Emails tagged [YOU] were sent by you. Emails tagged [THEM] were sent by the contact." if mode=='outbound' else ""}

1. SUMMARY: Write exactly 3 bullet points (each starting with "• ") summarizing the email history. Each bullet max 15 words.

2. {'SENTIMENT: What is their overall tone? (positive/neutral/negative/urgent). One word + one sentence explanation.' if mode=='inbound' else 'LAST ACTION: What was the last thing YOU asked, offered, or promised in your most recent email? Be specific.'}

3. LEAD SCORE: Rate 1-10 (10 = highly interested/great progress). Just the number.

4. KEY POINTS: Write exactly 3 bullet points (each starting with "• ") covering:
   - Current status of the relationship / deal
   - The most important unresolved topic
   - The single best next action to take

5. FOLLOW-UP DATE: Suggest a follow-up date (e.g. "3 days", "1 week").

6. DRAFT EMAIL: {'Professional, friendly reply (3-5 sentences).' if mode=='inbound' else 'Write a concise follow-up email (3-5 sentences). Reference a specific detail from the conversation. Sound natural, not pushy. Pick up exactly where the last email left off.'}
"""
    prompt = f"""Context: Smart email assistant for a startup founder.
Email History:\n{email_content}
Contact: {lead_name} <{lead_email}>
{task}
Respond with EXACTLY these headers:
### SUMMARY
### SENTIMENT
### LEAD SCORE
### KEY POINTS
### FOLLOW-UP DATE
### DRAFT EMAIL
"""
    try:
        client = Client(api_key=os.getenv("GEMINI_API_KEY"))
        analysis = client.models.generate_content(model="gemini-2.5-flash",contents=prompt).text
    except Exception as ex: return jsonify({'error':str(ex)}),500
    days = parse_days(analysis)
    return jsonify({
        'analysis': analysis,
        'summary':  extract_section(analysis,"### SUMMARY"),
        'sentiment': extract_section(analysis,"### SENTIMENT") or extract_section(analysis,"### LAST ACTION"),
        'lead_score': parse_score(analysis),
        'key_points': extract_section(analysis,"### KEY POINTS"),
        'action': extract_section(analysis,"### KEY POINTS"),
        'follow_up_days': days,
        'follow_up_date': (datetime.now()+timedelta(days=days)).strftime("%Y-%m-%d"),
        'draft_email': extract_section(analysis,"### DRAFT EMAIL"),
    })

@app.route('/api/save-draft', methods=['POST'])
def save_draft():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    data = request.json
    gmail,_,_,_ = get_services()
    msg = email.mime.multipart.MIMEMultipart()
    msg['to'] = data.get('to','')
    msg['subject'] = data.get('subject','')
    msg.attach(email.mime.text.MIMEText(data.get('body',''),'plain'))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        draft = gmail.users().drafts().create(userId='me',body={'message':{'raw':raw}}).execute()
        return jsonify({'success':True,'draft_id':draft.get('id')})
    except Exception as ex: return jsonify({'error':str(ex)}),500

# ── NEW: Send email directly ──────────────────────────────────────────────────
@app.route('/api/send-email', methods=['POST'])
def send_email():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    data = request.json
    gmail,_,_,_ = get_services()
    msg = email.mime.multipart.MIMEMultipart()
    msg['to'] = data.get('to','')
    msg['subject'] = data.get('subject','')
    msg.attach(email.mime.text.MIMEText(data.get('body',''),'plain'))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        sent = gmail.users().messages().send(userId='me', body={'raw': raw}).execute()
        return jsonify({'success': True, 'message_id': sent.get('id')})
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500

@app.route('/api/create-reminder', methods=['POST'])
def create_reminder():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    data = request.json
    _,_,calendar,_ = get_services()
    action = "Reply to" if data.get('mode')=='inbound' else "Follow up with"
    event = {
        "summary": f"📧 {action} {data.get('lead_name','Contact')}",
        "description": f"CRM Reminder auto-created for {data.get('lead_email','')}.",
        "start": {"date": data.get('date','')},
        "end":   {"date": data.get('date','')},
        "reminders": {"useDefault":False,"overrides":[{"method":"popup","minutes":30}]},
    }
    try:
        created = calendar.events().insert(calendarId='primary',body=event).execute()
        return jsonify({'success':True,'link':created.get('htmlLink')})
    except Exception as ex: return jsonify({'error':str(ex)}),500

HEADERS = [
    "Timestamp", "Lead Name", "Lead Email", "Direction", "Lead Score",
    "Summary", "Sentiment / Last Action", "Key Points", "Follow-Up Date"
]

def score_label(score):
    try:
        s = int(score)
        if s >= 8: return f"{s}/10 🔥 Hot"
        if s >= 5: return f"{s}/10 ⚡ Warm"
        return f"{s}/10 ❄️ Cold"
    except:
        return str(score)

def format_bullets(text):
    """Ensure bullet points are clean and newline-separated for sheets."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    bullets = []
    for line in lines:
        if not line.startswith('•'):
            line = '• ' + line
        bullets.append(line)
    return '\n'.join(bullets)

@app.route('/api/create-sheet', methods=['POST'])
def create_sheet():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    leads = request.json.get('leads',[])
    _,sheets,_,_ = get_services()
    try:
        sp = sheets.spreadsheets().create(body={
            "properties": {"title": f"CRM Leads – {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
            "sheets": [{"properties": {"title": "Leads"}}]
        }).execute()
        sid = sp["spreadsheetId"]
        sheet_id = sp["sheets"][0]["properties"]["sheetId"]

        sheets.spreadsheets().values().update(
            spreadsheetId=sid, range="Leads!A1", valueInputOption="RAW",
            body={"values": [HEADERS]}
        ).execute()

        rows = [[
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            l.get('lead_name',''),
            l.get('lead_email',''),
            '📥 Inbound' if l.get('mode') == 'inbound' else '📤 Outbound',
            score_label(l.get('lead_score', '')),
            format_bullets(l.get('summary', '')[:500]),
            l.get('sentiment',''),
            format_bullets(l.get('key_points', l.get('action',''))[:500]),
            l.get('follow_up_date',''),
        ] for l in leads]

        if rows:
            sheets.spreadsheets().values().append(
                spreadsheetId=sid, range="Leads!A1", valueInputOption="RAW",
                insertDataOption="INSERT_ROWS", body={"values": rows}
            ).execute()

        num_rows = len(rows) + 1
        requests_body = []

        requests_body.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.11, "green": 0.13, "blue": 0.16},
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {"red": 0.91, "green": 0.91, "blue": 0.94},
                            "fontSize": 10
                        },
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"
            }
        })

        requests_body.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": num_rows},
                "cell": {
                    "userEnteredFormat": {
                        "wrapStrategy": "WRAP",
                        "verticalAlignment": "TOP",
                        "textFormat": {"fontSize": 9}
                    }
                },
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,textFormat)"
            }
        })

        for i in range(1, num_rows):
            color = {"red": 0.96, "green": 0.97, "blue": 0.99} if i % 2 == 0 else {"red": 1, "green": 1, "blue": 1}
            requests_body.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": i, "endRowIndex": i + 1},
                    "cell": {"userEnteredFormat": {"backgroundColor": color}},
                    "fields": "userEnteredFormat(backgroundColor)"
                }
            })

        for col in [0, 3, 4, 8]:
            requests_body.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": num_rows,
                               "startColumnIndex": col, "endColumnIndex": col + 1},
                    "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                    "fields": "userEnteredFormat(horizontalAlignment)"
                }
            })

        for i, lead in enumerate(leads):
            try:
                s = int(lead.get('lead_score', 5))
                if s >= 8:
                    bg = {"red": 0.85, "green": 0.96, "blue": 0.88}
                elif s >= 5:
                    bg = {"red": 0.99, "green": 0.95, "blue": 0.82}
                else:
                    bg = {"red": 0.99, "green": 0.87, "blue": 0.87}
                requests_body.append({
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": i + 1, "endRowIndex": i + 2,
                                   "startColumnIndex": 4, "endColumnIndex": 5},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": bg,
                            "textFormat": {"bold": True}
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)"
                    }
                })
            except: pass

        col_widths = [140, 130, 200, 100, 100, 280, 160, 280, 120]
        for i, width in enumerate(col_widths):
            requests_body.append({
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                               "startIndex": i, "endIndex": i + 1},
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize"
                }
            })

        requests_body.append({
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"
            }
        })

        requests_body.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS",
                           "startIndex": 1, "endIndex": num_rows},
                "properties": {"pixelSize": 90},
                "fields": "pixelSize"
            }
        })

        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": requests_body}
        ).execute()

        return jsonify({'success': True, 'link': f"https://docs.google.com/spreadsheets/d/{sid}"})
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)