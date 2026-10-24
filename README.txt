PREMIUM BOT MAKER / FamGateway

Local: pip install -r requirements.txt ; python app.py
Production: gunicorn app:app
Environment: FAMGATEWAY_API_KEY, ADMIN_PASSWORD, SECRET_KEY, PUBLIC_URL, UPI_ID

SECURITY: Change compromised API key. No secrets are bundled.
WARNING: SQLite and local screenshot uploads are NOT persistent on Vercel. Use persistent Postgres/object storage before Vercel deployment. This package targets persistent Python hosting, not production Vercel.
The FamGateway API contract is based on user-provided docs; live integration not verified.
