# CRM Mail

An AI-powered email CRM that helps you analyze leads, draft intelligent replies, schedule follow-ups, and export everything to Google Sheets — all powered by Google Gemini.

![CRM Mail](https://img.shields.io/badge/Status-Active-brightgreen) ![Python](https://img.shields.io/badge/Python-3.8%2B-blue) ![Flask](https://img.shields.io/badge/Framework-Flask-lightgrey)

## Features

✨ **Smart Email Analysis**
- Analyzes both inbound and outbound emails with AI scoring
- Gemini-powered lead qualification (1-10 scale)
- Automatic sentiment detection and key insights extraction

✍️ **Draft Reply Generation**
- One-click AI-generated email drafts
- Save directly to Gmail Drafts for quick review
- Context-aware responses based on email analysis

📅 **Follow-up Management**
- Intelligent follow-up date suggestions
- One-click Google Calendar reminders
- Automatic meeting scheduling with customizable timing

📊 **Google Sheets Export**
- Export all analyzed leads to a formatted Google Sheet
- Professional styling with color-coded lead scores
- Automatic formatting, headers, and column sizing
- Share-ready spreadsheets

🔐 **Secure Authentication**
- OAuth 2.0 integration with Google
- Multi-service permissions (Gmail, Calendar, Sheets, Contacts)
- Automatic token refresh management

## Tech Stack

**Backend:**
- Flask (Python web framework)
- Google APIs (Gmail, Sheets, Calendar, Contacts)
- Google Generative AI (Gemini)
- Flask-Session for authentication

**Frontend:**
- HTML5 with responsive CSS
- JavaScript (vanilla)
- Google Fonts (DM Sans, DM Mono)

**Services:**
- Google Cloud Console (OAuth, APIs)
- Google Gemini API
- Google Workspace (Gmail, Sheets, Calendar)

## Prerequisites

- Python 3.8 or higher
- Google Cloud account with OAuth 2.0 credentials
- Google Gemini API key
- A modern web browser

## Installation

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd crm-mail
```

### 2. Create a Virtual Environment

```bash
python -m venv venv

# On Windows
venv\Scripts\activate

# On macOS/Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install flask google-auth-oauthlib google-auth-httplib2 google-api-python-client google-genai python-dotenv requests
```

### 4. Set Up Google Cloud Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable these APIs:
   - Gmail API
   - Google Sheets API
   - Google Calendar API
   - People API
4. Create OAuth 2.0 credentials (Desktop application)
5. Download the credentials as JSON and save as `credentials.json` in the project root

### 5. Configure Environment Variables

Create a `.env` file in the project root:

```env
FLASK_SECRET_KEY=your-secret-key-here
GOOGLE_CREDENTIALS=<optional: for production deployment>
GEMINI_API_KEY=your-gemini-api-key
```

For production (e.g., Vercel), set `GOOGLE_CREDENTIALS` as a JSON string of your OAuth credentials.

### 6. Run the Application

```bash
python app.py
```

The app will be available at `http://localhost:5000`

## Project Structure

```
crm-mail/
├── app.py                 # Main Flask application
├── index.html             # Landing/login page
├── dashboard.html         # Main CRM dashboard
├── credentials.json       # Google OAuth credentials (not included)
├── .env                   # Environment variables (not included)
├── .gitignore            # Git ignore file
└── README.md             # This file
```

## Usage

### 1. Sign In

1. Visit `http://localhost:5000`
2. Click "Sign in with Google"
3. Authorize the app to access your Gmail, Calendar, and Sheets

### 2. Analyze Emails

**Inbound Mode:**
- Enter a contact's email address
- The app fetches emails from that contact
- Gemini analyzes each email for lead score, sentiment, and key points

**Outbound Mode:**
- Analyze emails you've sent to a contact
- Track follow-up opportunities
- View conversation history with scoring

### 3. Generate Email Drafts

- Click "Generate Reply" on any email
- AI creates a contextual response
- Review and save to Gmail Drafts
- Send directly from Gmail

### 4. Schedule Follow-ups

- Click "Create Reminder" on any email
- Set follow-up date based on AI recommendations
- Automatic Google Calendar event creation
- 30-minute popup reminder before the event

### 5. Export to Google Sheets

- Click "Export to Sheets" in the dashboard
- Creates a new, formatted Google Sheet with:
  - All analyzed leads
  - Contact info and lead scores
  - Color-coded scoring (🔥 Hot, ⚡ Warm, ❄️ Cold)
  - Summaries and key points
  - Follow-up dates
- Spreadsheet is instantly shared with you

### 6. Manage Your Lead Queue

- View all analyzed leads in the left sidebar
- Click to view details
- Delete leads with the X button
- Clear the entire queue with "Clear Queue" button

## API Endpoints

### Authentication
- `GET /` - Landing page
- `GET /authorize` - Initiate OAuth flow
- `GET /oauth2callback` - OAuth callback
- `GET /logout` - Clear session

### Dashboard
- `GET /dashboard` - Main dashboard (requires auth)

### API Routes (all require authentication)
- `POST /api/fetch-emails` - Fetch and analyze emails
- `POST /api/generate-reply` - Generate AI reply draft
- `POST /api/lookup-contact` - Search contacts by email
- `POST /api/create-reminder` - Create calendar reminder
- `POST /api/create-sheet` - Export leads to Google Sheet

## Configuration

### Gemini Prompts

The app uses custom prompts to analyze emails. Modify these in `app.py`:

```python
SYSTEM_PROMPT = """You are an expert sales analyst..."""
```

### Follow-up Date Parsing

Adjust the default follow-up window in `app.py`:

```python
def parse_days(text):
    # Returns number of days for follow-up
    return 3  # default
```

### Google Sheets Formatting

Customize the sheet appearance:
- Column widths (line 518)
- Color schemes (line 447-504)
- Header styling (line 442-460)

## Environment Variables

| Variable | Description |
|----------|-------------|
| `FLASK_SECRET_KEY` | Secret key for Flask sessions |
| `GOOGLE_CREDENTIALS` | JSON string of OAuth credentials (for production) |
| `GEMINI_API_KEY` | API key for Google Gemini |

## Troubleshooting

### "credentials.json not found" error
- Ensure you've downloaded OAuth credentials from Google Cloud Console
- Save as `credentials.json` in the project root
- Or set `GOOGLE_CREDENTIALS` environment variable

### "Invalid GOOGLE_CREDENTIALS JSON" error
- Verify the JSON is valid and complete
- Make sure it's a string (not already parsed)
- Check that it contains `client_id` and `client_secret`

### Emails not fetching
- Verify Gmail API is enabled in Google Cloud
- Check that OAuth scopes include `gmail.readonly`
- Ensure you're signed in with the correct Google account

### Gemini API errors
- Verify `GEMINI_API_KEY` is set and valid
- Check your API quota and rate limits
- Ensure the Gemini API is enabled in your Google Cloud project

### Calendar reminders not creating
- Verify Google Calendar API is enabled
- Check that OAuth scope includes `calendar`
- Ensure the date format is valid (YYYY-MM-DD)

## Deployment

### Vercel (Recommended)

1. Push your code to GitHub
2. Connect your repository to Vercel
3. Add environment variables:
   - `FLASK_SECRET_KEY`
   - `GOOGLE_CREDENTIALS` (as JSON string)
   - `GEMINI_API_KEY`
4. Deploy!

Note: Change `OAUTHLIB_INSECURE_TRANSPORT` to `0` for production.

### Other Platforms (Heroku, AWS, etc.)

1. Ensure all environment variables are set
2. Update OAuth redirect URI in Google Cloud Console
3. Set `OAUTHLIB_INSECURE_TRANSPORT = '0'` in production
4. Use a production WSGI server (Gunicorn, uWSGI)

## Security Considerations

⚠️ **Local Development Only**: The app currently sets `OAUTHLIB_INSECURE_TRANSPORT = '1'` for local development. This **must be changed to `0`** for production.

- Store credentials securely using environment variables
- Never commit `credentials.json` or `.env` files
- Use HTTPS in production
- Implement rate limiting for API endpoints
- Validate all user inputs

## Rate Limits

Be aware of these limits:
- **Gmail API**: 500 requests/user/day
- **Sheets API**: 500 requests/100s/user
- **Gemini API**: Based on your plan

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Roadmap

- [ ] Email template library
- [ ] Custom lead scoring rules
- [ ] Team collaboration features
- [ ] Advanced analytics dashboard
- [ ] Email scheduling
- [ ] CRM integration (Salesforce, HubSpot)
- [ ] Mobile app

## License

This project is licensed under the MIT License — see the LICENSE file for details.

## Support

For issues, feature requests, or questions:
- Create an issue on GitHub
- Check existing documentation
- Review API logs for debugging

## Acknowledgments

- Built with [Flask](https://flask.palletsprojects.com/)
- Powered by [Google Gemini](https://ai.google.dev/)
- Styled with [DM Sans](https://fonts.google.com/specimen/DM+Sans) and [DM Mono](https://fonts.google.com/specimen/DM+Mono)

---

**Made with ❤️ for sales teams and email enthusiasts**
