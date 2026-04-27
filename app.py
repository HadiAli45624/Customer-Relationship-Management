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
        with open('credentials.json') as f:
            data = json.load(f)
        GOOGLE_CLIENT_SECRETS = data.get('web', data.get('installed'))
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

@app.route('/api/fetch-emails', methods=['POST'])
def fetch_emails():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    data = request.json
    target_email = data.get('email','').strip()
    mode = data.get('mode','inbound')
    max_results = int(data.get('max_results',5))
    gmail,_,_,_ = get_services()
    query = f"from:{target_email}" if mode=='inbound' else f"to:{target_email}"
    label_ids = ["INBOX"] if mode=='inbound' else ["SENT"]
    try:
        res = gmail.users().messages().list(userId='me',q=query,maxResults=max_results,labelIds=label_ids).execute()
    except Exception as ex: return jsonify({'error':str(ex)}),500
    messages = res.get('messages',[])
    if not messages: return jsonify({'error':'No emails found for this address and mode.'}),404
    email_list, email_content = [], ""
    for msg in messages:
        m = gmail.users().messages().get(userId='me',id=msg['id'],format='full').execute()
        hdrs = {h['name']:h['value'] for h in m['payload'].get('headers',[])}
        row = {'subject':hdrs.get('Subject','(No Subject)'),'from':hdrs.get('From','Unknown'),
               'to':hdrs.get('To','Unknown'),'date':hdrs.get('Date','Unknown'),'snippet':m.get('snippet','')}
        email_list.append(row)
        email_content += f"\n--- Email ---\nDate: {row['date']}\nFrom: {row['from']}\nTo: {row['to']}\nSubject: {row['subject']}\nSnippet: {row['snippet']}\n"
    return jsonify({'emails':email_list,'email_content':email_content})

@app.route('/api/analyze', methods=['POST'])
def analyze():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    data = request.json
    email_content = data.get('email_content','')
    mode = data.get('mode','inbound')
    lead_name = data.get('lead_name','the contact')
    lead_email = data.get('lead_email','')
    task = f"""
You are analyzing {'INBOUND emails (received FROM' if mode=='inbound' else 'OUTBOUND emails (sent BY YOU to'} {lead_name}).
1. SUMMARY: 2-3 sentence summary of the email history.
2. {'SENTIMENT: Their tone? (positive/neutral/negative/urgent)' if mode=='inbound' else 'LAST ACTION: What was the last thing you asked or offered?'}
3. LEAD SCORE: Rate 1-10 (10 = highly interested/great progress).
4. {'ACTION NEEDED: What does ' + lead_name + ' need or expect from you?' if mode=='inbound' else 'FOLLOW-UP NEEDED: Is a follow-up needed? Why?'}
5. FOLLOW-UP DATE: Suggest a follow-up date (e.g. "3 days", "1 week").
6. DRAFT EMAIL: {'Professional, friendly reply (3-5 sentences).' if mode=='inbound' else 'Concise follow-up (3-5 sentences), reference a specific detail. Not pushy.'}
"""
    prompt = f"""Context: Smart email assistant for a startup founder.
Email History:\n{email_content}
Contact: {lead_name} <{lead_email}>
{task}
Respond with EXACTLY these headers:
### SUMMARY\n### SENTIMENT\n### LEAD SCORE\n### ACTION NEEDED\n### FOLLOW-UP DATE\n### DRAFT EMAIL
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
        'action': extract_section(analysis,"### ACTION NEEDED") or extract_section(analysis,"### FOLLOW-UP NEEDED"),
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

HEADERS = ["Timestamp","Lead Name","Lead Email","Mode","Lead Score",
           "Summary","Sentiment / Last Action","Action Needed / Follow-Up Needed","Follow-Up Date","Draft Email"]

@app.route('/api/create-sheet', methods=['POST'])
def create_sheet():
    if 'credentials' not in session: return jsonify({'error':'Not authenticated'}),401
    leads = request.json.get('leads',[])
    _,sheets,_,_ = get_services()
    try:
        sp = sheets.spreadsheets().create(body={
            "properties":{"title":f"CRM Leads – {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
            "sheets":[{"properties":{"title":"Leads"}}]
        }).execute()
        sid = sp["spreadsheetId"]
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,range="Leads!A1",valueInputOption="RAW",body={"values":[HEADERS]}).execute()
        rows=[[datetime.now().strftime("%Y-%m-%d %H:%M"),
               l.get('lead_name',''),l.get('lead_email',''),l.get('mode',''),l.get('lead_score',''),
               l.get('summary','')[:400],l.get('sentiment','')[:200],l.get('action','')[:400],
               l.get('follow_up_date',''),l.get('draft_email','')[:600]] for l in leads]
        if rows:
            sheets.spreadsheets().values().append(
                spreadsheetId=sid,range="Leads!A1",valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",body={"values":rows}).execute()
        return jsonify({'success':True,'link':f"https://docs.google.com/spreadsheets/d/{sid}"})
    except Exception as ex: return jsonify({'error':str(ex)}),500

if __name__ == '__main__':
    app.run(debug=True, port=5000)