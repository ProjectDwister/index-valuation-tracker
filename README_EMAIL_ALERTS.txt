NIFTY 50 VALUATION TRACKER - EMAIL ALERT SETUP
==============================================

WHAT TRIGGERS AN EMAIL
----------------------
The weekday GitHub Action compares the newly calculated Signal Thresholds with
its previously recorded state.

By default it sends an email when ANY of these occurs:
1. BUY -> HOLD P/E boundary changes by at least 0.10x, OR
2. HOLD -> SELL P/E boundary changes by at least 0.10x, OR
3. The equivalent NIFTY threshold level changes by at least 0.5%, OR
4. The actual model signal changes (BUY/HOLD/SELL).

The comparison baseline is stored automatically in:
  docs/data/threshold_alert_state.json

The first normal run only creates the baseline and does not email.

GMAIL SETUP
-----------
1. Use a Google account with 2-Step Verification enabled.
2. In Google Account -> Security -> App passwords, create an app password for
   "NIFTY Tracker". Copy the 16-character app password.
3. In GitHub repository -> Settings -> Secrets and variables -> Actions ->
   Repository secrets, create:

   SMTP_USERNAME     your Gmail address, e.g. you@gmail.com
   SMTP_PASSWORD     the Google App Password (NOT your normal Gmail password)
   ALERT_EMAIL_TO    address that should receive the alerts

No code change is required.

OPTIONAL TUNING
---------------
In GitHub repository -> Settings -> Secrets and variables -> Actions ->
Variables, you may add:

   ALERT_PE_DELTA          default 0.10     (P/E multiple)
   ALERT_NIFTY_DELTA_PCT   default 0.005    (0.5% expressed as decimal)
   SMTP_HOST               default smtp.gmail.com
   SMTP_PORT               default 465
   SMTP_FROM               optional sender email; defaults to SMTP_USERNAME

TEST THE EMAIL
--------------
After uploading the two replacement files and creating the three secrets:

Actions -> Refresh NIFTY tracker and deploy Pages -> Run workflow
Tick/select "Send a test threshold email on this run" -> Run workflow.

The run should remain green even if email sending fails; inspect the
"Refresh NIFTY valuation model" step for the email status message.

SECURITY
--------
Never put your Gmail password or app password in the repository files.
The workflow reads credentials only from encrypted GitHub Actions secrets.
