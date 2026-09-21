INDEX VALUATION TRACKER - MULTI-INDEX EMAIL ALERTS
==================================================

The authoritative email system is handled by multi_index_tracker.py.
Every eligible index shown on the dashboard is monitored independently.

DEFAULT TRIGGERS
----------------
An index triggers when ANY of these occurs:
1. BUY -> HOLD P/E boundary changes by at least 0.10x, OR
2. HOLD -> SELL P/E boundary changes by at least 0.10x, OR
3. the corresponding threshold index level changes by at least 0.5%, OR
4. the actual model signal changes between BUY / HOLD / SELL.

If several indices trigger on the same refresh, the tracker sends one consolidated email.
A newly eligible index first receives a baseline and does not immediately create a normal alert.

STATE FILE
----------
docs/data/multi_index_alert_state.json

The old docs/data/threshold_alert_state.json belongs to the superseded NIFTY-only alert system and should not be used.

GMAIL SETUP
-----------
GitHub repository -> Settings -> Secrets and variables -> Actions -> Repository secrets:

SMTP_USERNAME     Gmail address
SMTP_PASSWORD     Google App Password, not the normal Gmail password
ALERT_EMAIL_TO    destination email address

Optional Actions variables:

ALERT_PE_DELTA          default 0.10
ALERT_INDEX_DELTA_PCT   default 0.005
SMTP_HOST               default smtp.gmail.com
SMTP_PORT               default 465
SMTP_FROM               defaults to SMTP_USERNAME

TEST EMAIL
----------
Actions -> Refresh Index Valuation Tracker and deploy Pages -> Run workflow
Enable "Send a test multi-index valuation email on this run".

The manual test uses NIFTY 50 as the sample if there is no real trigger.

SECURITY
--------
Never commit Gmail passwords or app passwords to the repository.
