"""Email notifications via Gmail SMTP."""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

TO      = "chockumail@gmail.com"
FROM    = "chockumail@gmail.com"
SUBJECT = "QQQ Trader"


def _send(subject: str, body: str) -> None:
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        log.warning("GMAIL_APP_PASSWORD not set — skipping email")
        return
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"]    = FROM
    msg["To"]      = TO
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(FROM, password)
            server.sendmail(FROM, TO, msg.as_string())
        log.info("Email sent: %s", subject)
    except Exception as e:
        log.error("Failed to send email: %s", e)


def notify_started(signal_ticker: str, signal_alloc: str, mr_score: int, equity: float) -> None:
    now = datetime.now().strftime("%I:%M %p")
    _send(
        subject=f"[QQQ Trader] Started — {signal_ticker} {signal_alloc}",
        body=f"""Pipeline started at {now}

Signal:   {signal_ticker} {signal_alloc}
MR Score: {mr_score}/5
Equity:   ${equity:,.2f}

Orders will be submitted shortly.
""",
    )


def notify_completed(
    ticker: str,
    alloc: str,
    delta: int,
    order_id: str | None,
    exec_price: float,
    equity: float,
    current_shares: float,
    target_shares: int,
) -> None:
    now    = datetime.now().strftime("%I:%M %p")
    action = "No trade needed" if delta == 0 else f"{'BUY' if delta > 0 else 'SELL'} {abs(delta)} shares"
    _send(
        subject=f"[QQQ Trader] Done — {ticker} {alloc} | {action}",
        body=f"""Pipeline completed at {now}

Signal:        {ticker} {alloc}
Action:        {action}
Price:         ${exec_price:.2f}
Shares:        {int(current_shares)} → {target_shares}
Equity:        ${equity:,.2f}
Order ID:      {order_id or "none"}
""",
    )


def notify_error(stage: str, error: str) -> None:
    now = datetime.now().strftime("%I:%M %p")
    _send(
        subject=f"[QQQ Trader] ERROR at {stage}",
        body=f"""Pipeline failed at {now}

Stage: {stage}
Error: {error}

Check scheduler.log for details.
""",
    )
