# get_refresh_token.py

from google_auth_oauthlib.flow import InstalledAppFlow

def main():
    SCOPES = ['https://www.googleapis.com/auth/calendar']
    flow = InstalledAppFlow.from_client_secrets_file(
        'credentials.json', SCOPES)
    creds = flow.run_local_server(port=0)
    print('\nCopy this refresh token into your .env as GOOGLE_REFRESH_TOKEN:\n')
    print(creds.refresh_token)

if __name__ == '__main__':
    main()
