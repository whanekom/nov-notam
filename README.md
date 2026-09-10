# NHV NOTAM daily briefing

Logs into the NATS AIS UK site, regenerates the saved "NHV NOTAM" briefing,
downloads the PDF and emails it.

## Local test

    pip install -r requirements.txt
    playwright install chromium
    cp .env.example .env      # fill in
    DEBUG=1 NO_EMAIL=1 python nhv_notam.py

## Render cron job

- Runtime: python
- Build command: `pip install -r requirements.txt && playwright install chromium`
- Start command: `python nhv_notam.py`
- Region: frankfurt
- Schedule is in UTC.

Secrets (NATS_USER, NATS_PASS, SMTP_USER, SMTP_PASS) are set as Render
environment variables, never committed to this repo.
