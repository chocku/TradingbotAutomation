"""Email notifications via Gmail SMTP."""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

TO   = "chockumail@gmail.com"
FROM = "chockumail@gmail.com"

_COLOR_STARTED = "#1a6fd1"  # blue
_COLOR_BUY     = "#1a7f37"  # green
_COLOR_SELL    = "#b06a00"  # amber
_COLOR_NEUTRAL = "#5b5f66"  # gray — no trade needed
_COLOR_ERROR   = "#c0341d"  # red

_STATUS_LABELS = {
    "filled":            "Filled",
    "partially_filled":  "Partially filled",
    "accepted":          "Accepted (not yet filled)",
    "pending_new":       "Pending",
    "new":               "Accepted (not yet filled)",
    "canceled":          "Canceled",
    "expired":           "Expired (not filled)",
    "rejected":          "Rejected",
    "not_submitted":     "Not submitted (below share threshold)",
    "dry_run":           "Not submitted (simulated)",
    "failed":            "Submission failed",
}


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


def _timestamp() -> str:
    return datetime.now().strftime("%b %d, %I:%M %p")


def _dashboard_url() -> str | None:
    bucket = os.environ.get("S3_BUCKET")
    return f"https://{bucket}.s3.amazonaws.com/dashboard.html" if bucket else None


def _html(title: str, color: str, rows: list[tuple[str, str]], link: str | None) -> str:
    row_html = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#666;white-space:nowrap;vertical-align:top;">{label}</td>'
        f'<td style="padding:4px 0;font-weight:600;">{value}</td></tr>'
        for label, value in rows
    )
    link_html = (
        f'<p style="margin:16px 0 0;"><a href="{link}" style="color:{color};">View dashboard &rarr;</a></p>'
        if link else ""
    )
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:480px;">
  <div style="background:{color};color:#fff;padding:12px 16px;border-radius:6px 6px 0 0;font-size:16px;font-weight:600;">
    {title}
  </div>
  <div style="border:1px solid #e0e0e0;border-top:none;padding:16px;border-radius:0 0 6px 6px;">
    <table style="border-collapse:collapse;font-size:14px;">{row_html}</table>
    {link_html}
  </div>
</div>
"""


def _send(subject: str, plain_body: str, html_body: str) -> None:
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        log.warning("GMAIL_APP_PASSWORD not set — skipping email")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM
    msg["To"]      = TO
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(FROM, password)
            server.sendmail(FROM, TO, msg.as_string())
        log.info("Email sent: %s", subject)
    except Exception as e:
        log.error("Failed to send email: %s", e)


def notify_started(
    signal_ticker: str,
    signal_alloc: str,
    mr_score: int,
    equity: float,
    signal_detail: dict | None = None,
    dry_run: bool = False,
) -> None:
    now  = _timestamp()
    tag  = " [DRY RUN]" if dry_run else ""
    link = _dashboard_url()

    why = None
    if signal_detail:
        from signal_engine.engine import describe_signal_detail
        why = describe_signal_detail(signal_detail)

    subject = f"[QQQ Trader]{tag} Started: {signal_ticker} {signal_alloc}"

    plain_lines = [f"Pipeline started at {now}{tag}", ""]
    plain_lines.append(f"Signal:   {signal_ticker} {signal_alloc}")
    if why:
        plain_lines.append(f"Why:      {why}")
    plain_lines.append(f"MR Score: {mr_score}/5")
    plain_lines.append(f"Equity:   ${equity:,.2f}")
    plain_lines.append("")
    plain_lines.append("Orders will be submitted shortly.")
    if link:
        plain_lines.append(f"Dashboard: {link}")

    rows = [("Signal", f"{signal_ticker} {signal_alloc}")]
    if why:
        rows.append(("Why", why))
    rows += [("MR score", f"{mr_score}/5"), ("Equity", f"${equity:,.2f}"), ("Time", now)]

    _send(
        subject,
        "\n".join(plain_lines) + "\n",
        _html(f"Started{tag} &mdash; {signal_ticker} {signal_alloc}", _COLOR_STARTED, rows, link),
    )


def notify_completed(
    ticker: str,
    alloc: str,
    delta: int,
    order_id: str | None,
    order_status: str,
    exec_price: float,
    equity: float,
    current_shares: float,
    target_shares: int,
    signal_detail: dict | None = None,
    position_before: dict | None = None,
    dry_run: bool = False,
) -> None:
    now  = _timestamp()
    tag  = " [DRY RUN]" if dry_run else ""
    link = _dashboard_url()
    status_label = _status_label(order_status)

    why = None
    if signal_detail:
        from signal_engine.engine import describe_signal_detail
        why = describe_signal_detail(signal_detail)

    switched_from = None
    if position_before and position_before.get("ticker") != ticker:
        switched_from = f"{position_before['shares']} sh {position_before['ticker']}"

    if delta == 0:
        subject = f"[QQQ Trader]{tag} No trade — holding {ticker} {alloc}"
        action  = "No trade needed"
        color   = _COLOR_NEUTRAL
    else:
        side    = "BUY" if delta > 0 else "SELL"
        subject = f"[QQQ Trader]{tag} {side} {abs(delta)} {ticker} — {status_label}"
        action  = f"{side} {abs(delta)} shares"
        color   = _COLOR_BUY if delta > 0 else _COLOR_SELL

    plain_lines = [f"Pipeline completed at {now}{tag}", ""]
    plain_lines.append(f"Signal:        {ticker} {alloc}")
    if why:
        plain_lines.append(f"Why:           {why}")
    plain_lines.append(f"Action:        {action}")
    if delta != 0:
        plain_lines.append(f"Order status:  {status_label}")
    if switched_from:
        plain_lines.append(f"Switched from: {switched_from}")
    plain_lines.append(f"Price:         ${exec_price:.2f}")
    plain_lines.append(f"Shares:        {int(current_shares)} → {target_shares}")
    plain_lines.append(f"Equity:        ${equity:,.2f}")
    plain_lines.append(f"Order ID:      {order_id or 'none'}")
    if link:
        plain_lines.append(f"Dashboard:     {link}")

    rows = [("Signal", f"{ticker} {alloc}")]
    if why:
        rows.append(("Why", why))
    rows.append(("Action", action))
    if delta != 0:
        rows.append(("Order status", status_label))
    if switched_from:
        rows.append(("Switched from", switched_from))
    rows += [
        ("Price", f"${exec_price:.2f}"),
        ("Shares", f"{int(current_shares)} &rarr; {target_shares}"),
        ("Equity", f"${equity:,.2f}"),
        ("Order ID", order_id or "none"),
        ("Time", now),
    ]

    _send(
        subject,
        "\n".join(plain_lines) + "\n",
        _html(f"{action}{tag} &mdash; {ticker}", color, rows, link),
    )


def notify_error(stage: str, error: str, dry_run: bool = False) -> None:
    now  = _timestamp()
    tag  = " [DRY RUN]" if dry_run else ""
    link = _dashboard_url()

    subject = f"[QQQ Trader]{tag} ERROR during {stage}"

    plain_lines = [
        f"Pipeline failed at {now}{tag}",
        "",
        f"Stage: {stage}",
        f"Error: {error}",
    ]
    if link:
        plain_lines.append("")
        plain_lines.append(f"Dashboard: {link}")

    rows = [("Stage", stage), ("Error", error), ("Time", now)]

    _send(
        subject,
        "\n".join(plain_lines) + "\n",
        _html(f"Error{tag} &mdash; {stage}", _COLOR_ERROR, rows, link),
    )
