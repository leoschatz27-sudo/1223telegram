Deployment and Render instructions

1) Ensure repository contains these files at repo root:
- `pythonanywhere_app.py` (Flask webhook app)
- `set_webhook.py`
- `requirements.txt`
- optional: `bot.py`

2) Push the repo to GitHub (example):

```bash
git init
git add .
git commit -m "Add Telegram bot webhook app"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo>.git
git push -u origin main
```

3) On Render.com:
- Create a new Web Service → Connect GitHub → choose this repo
- Branch: `main`
- Start Command: `gunicorn pythonanywhere_app:app`
- Add Environment variables:
  - `TELEGRAM_BOT_TOKEN` = <your bot token>
  - `WEBHOOK_SECRET` = `cbc-webhook`
  - `GMAIL_ADDRESS` = <your Gmail address>
  - `GMAIL_APP_PASSWORD` = <your Google App Password>

4) After successful deploy, get the public URL (e.g. `https://your-service.onrender.com`) and run locally to set webhook:

```bash
python set_webhook.py https://your-service.onrender.com
```

5) Verify webhook info:

```bash
python -c "import requests,os; print(requests.get(f'https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN')}/getWebhookInfo').json())"
```

Notes:
- `requirements.txt` already contains `Flask`, `requests`, `gunicorn`, and `python-telegram-bot`.
- If you prefer Replit, upload same files to a public Repl and use the Repl URL as webhook target.
- Keep `TELEGRAM_BOT_TOKEN` secret; do not commit it to GitHub.
