#!/usr/bin/env python3
"""
One-time local OAuth flow for Gmail API access.

Run this once on your local machine to generate token.json, then
base64-encode it and store the result as the GMAIL_TOKEN_JSON GitHub secret.

Usage:
  1. Download your OAuth client credentials from Google Cloud Console
     (type: Desktop app) and save as crm/credentials.json
  2. pip install google-auth-oauthlib
  3. python3 crm/auth_gmail.py
  4. A browser window opens — sign in and grant access
  5. token.json is written to crm/token.json
  6. Run: base64 -i crm/token.json | tr -d '\n'
  7. Paste the output as the GMAIL_TOKEN_JSON secret in GitHub
"""
import os
import base64
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
HERE = os.path.dirname(__file__)
CREDS_FILE = os.path.join(HERE, 'credentials.json')
TOKEN_FILE = os.path.join(HERE, 'token.json')

if not os.path.exists(CREDS_FILE):
    raise FileNotFoundError(
        f"credentials.json not found at {CREDS_FILE}\n"
        "Download it from Google Cloud Console → APIs & Services → Credentials → "
        "your OAuth 2.0 Client ID → Download JSON"
    )

flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_FILE, 'w') as f:
    f.write(creds.to_json())

print(f"\ntoken.json written to: {TOKEN_FILE}")
print("\nBase64-encode it for the GitHub secret:")
print("  base64 -i crm/token.json | tr -d '\\n'")
print("\nOr copy the value below and paste it as GMAIL_TOKEN_JSON in GitHub Settings → Secrets:\n")

with open(TOKEN_FILE, 'rb') as f:
    print(base64.b64encode(f.read()).decode())
