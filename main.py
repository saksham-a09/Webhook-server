import csv
import json
import os
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

import requests
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


load_dotenv()

app = FastAPI(title="TradingView Signal Relay", version="2.0.0")

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
TRIGGER_CSV_PATH = Path(os.getenv("TRIGGER_CSV_PATH", DATA_DIR / "trigger_watchlist.csv"))
TRADE_CSV_PATH = Path(os.getenv("TRADE_CSV_PATH", os.getenv("CSV_PATH", DATA_DIR / "trade_log.csv")))

WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_BASE = "https://api.telegram.org/bot"
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")

TRIGGER_COLUMNS = [
    "trigger_id",
    "ticker",
    "timeframe",
    "trigger_date",
    "trigger_close",
    "days_since_trigger",
    "status",
    "converted_to_trade_id",
    "notes",
    "telegram_sent",
    "telegram_error",
    "payload_json"
]

TRADE_COLUMNS = [
    "trade_id",
    "ticker",
    "timeframe",
    "grade",
    "entry_date",
    "entry_price",
    "sl",
    "tp1",
    "tp2",
    "tp3",
    "tp_main",
    "rr_planned",
    "exit_date",
    "exit_price",
    "exit_reason",
    "days_in_trade",
    "days_to_tp1",
    "days_to_tp2",
    "days_to_tp3",
    "days_to_target",
    "outcome",
    "r_multiple",
    "return_pct",
    "telegram_sent",
    "telegram_error",
    "notes",
    "payload_json"
]

csv_lock = threading.Lock()


class SignalEnvelope(BaseModel):
    token: str | None = Field(default=None, description="Shared secret for webhook authentication")
    type: str | None = None
    signal_type: str | None = None
    symbol: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    timeframe: str | None = None
    grade: str | None = None
    price: float | None = None
    trigger_close: float | None = None
    entry_price: float | None = None
    sl: float | None = None
    tp_main: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    comment: str | None = None
    side: str | None = None
    action: str | None = None
    strategy: str | None = None
    quantity: float | None = None
    trade_id: str | None = None
    trigger_id: str | None = None
    message: str | None = None
    notes: str | None = None
    source: str | None = Field(default="pinescript")


def current_time_str() -> str:
    # Formatted for easy Google Sheets / CSV parsing (GMT-4 / Eastern)
    return datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d %H:%M:%S")


def parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str[:19] if "T" not in fmt else date_str, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(date_str)
    except Exception:
        return None


def calculate_days_between(start_str: str, end_str: str) -> str:
    d1 = parse_date(start_str)
    d2 = parse_date(end_str)
    if d1 and d2:
        diff = (d2.date() - d1.date()).days
        return str(max(0, diff))
    return ""


def calculate_rr_planned(entry: Any, sl: Any, tp_main: Any) -> str:
    try:
        e, s, t = float(entry), float(sl), float(tp_main)
        risk = abs(e - s)
        reward = abs(t - e)
        if risk > 0:
            return f"{round(reward / risk, 2)}R"
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    return ""


def calculate_r_multiple(entry: Any, sl: Any, exit_p: Any) -> str:
    try:
        e, s, x = float(entry), float(sl), float(exit_p)
        risk = abs(e - s)
        if risk > 0:
            r = (x - e) / risk
            return f"{round(r, 2)}R"
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    return ""


def calculate_return_pct(entry: Any, exit_p: Any) -> str:
    try:
        e, x = float(entry), float(exit_p)
        if e > 0:
            pct = ((x - e) / e) * 100
            return f"{round(pct, 2)}%"
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    return ""


def determine_outcome(exit_reason: str, r_multiple_str: str) -> str:
    reason_upper = exit_reason.upper()
    if any(k in reason_upper for k in ("TP1", "TP2", "TP3", "TP", "TARGET", "WIN")):
        return "Win"
    if any(k in reason_upper for k in ("BE", "BREAKEVEN")):
        return "Breakeven"
    if any(k in reason_upper for k in ("SL", "STOP", "LOSS")):
        return "Loss"
    
    try:
        cleaned_r = r_multiple_str.replace("R", "").strip()
        if cleaned_r:
            r_val = float(cleaned_r)
            if r_val > 0.05:
                return "Win"
            elif r_val < -0.05:
                return "Loss"
            else:
                return "Breakeven"
    except Exception:
        pass
    return "Closed"


def ensure_csv_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRIGGER_CSV_PATH.exists():
        with TRIGGER_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRIGGER_COLUMNS)
            writer.writeheader()

    if not TRADE_CSV_PATH.exists():
        with TRADE_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
            writer.writeheader()


def normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    return {"message": str(payload)}


def authenticate(payload: dict[str, Any]) -> None:
    if not WEBHOOK_TOKEN:
        return
    token = str(payload.get("token") or "")
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid webhook token")


def classify_signal(payload: dict[str, Any]) -> str:
    msg_type = (str(payload.get("type") or payload.get("signal_type") or "")).strip().upper()
    side = str(payload.get("side") or "").strip().upper()
    action = str(payload.get("action") or "").strip().lower()
    comment = str(payload.get("comment") or "")

    if msg_type == "TRIGGER":
        return "TRIGGER"
    if side == "EXIT" or action in ["sell", "close"] or "Sl" in comment or "Tp" in comment or payload.get("exit_price") is not None:
        return "EXIT"
    if side in ["LONG", "SHORT"] or action in ["buy", "entry"] or payload.get("entry_price") is not None:
        return "ENTRY"
    return "UNKNOWN"


def telegram_message(payload: dict[str, Any]) -> str:
    sig_type = classify_signal(payload)
    ticker = payload.get("ticker") or payload.get("symbol") or ""
    timeframe = payload.get("timeframe") or ""
    grade = payload.get("grade") or ""
    comment = payload.get("comment") or payload.get("notes") or ""

    if sig_type == "TRIGGER":
        trigger_close = payload.get("trigger_close") or payload.get("price") or ""
        msg = f"🚨 *TRIGGER ALERT* 🚨\n*Ticker:* `{ticker}`\n*Timeframe:* `{timeframe}`\n*Trigger Close:* `{trigger_close}`"
        if grade:
            msg += f"\n*Grade:* `{grade}`"
        if comment:
            msg += f"\n*Notes:* {comment}"
        return msg

    elif sig_type == "ENTRY":
        entry_price = payload.get("entry_price") or payload.get("price") or ""
        sl = payload.get("sl") or ""
        tp1 = payload.get("tp1") or ""
        tp2 = payload.get("tp2") or ""
        tp3 = payload.get("tp3") or ""
        tp_main = payload.get("tp_main") or ""
        rr = calculate_rr_planned(entry_price, sl, tp_main)

        msg = f"🟢 *LONG ENTRY* 🟢\n*Ticker:* `{ticker}`\n*Timeframe:* `{timeframe}`\n*Entry Price:* `{entry_price}`"
        if grade:
            msg += f"\n*Grade:* `{grade}`"
        if sl:
            msg += f"\n*Stop Loss:* `{sl}`"
        if tp1:
            msg += f"\n*TP1:* `{tp1}`"
        if tp2:
            msg += f"\n*TP2:* `{tp2}`"
        if tp3:
            msg += f"\n*TP3:* `{tp3}`"
        if tp_main:
            msg += f"\n*Target (Main):* `{tp_main}`"
        if rr:
            msg += f"\n*Planned R:R:* `{rr}`"
        return msg

    elif sig_type == "EXIT":
        exit_price = payload.get("exit_price") or payload.get("price") or ""
        reason = comment or payload.get("reason") or "Exit signal"
        return f"🔴 *EXIT SIGNAL* 🔴\n*Ticker:* `{ticker}`\n*Exit Price:* `{exit_price}`\n*Reason:* `{reason}`"

    else:
        parts = ["📡 *TradingView Signal Received*"]
        for k, v in payload.items():
            if k not in ["token", "payload_json"]:
                parts.append(f"*{k}:* `{v}`")
        return "\n".join(parts)


def send_telegram_alert(payload: dict[str, Any]) -> tuple[bool, str]:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "Telegram not configured"

    url = f"{TELEGRAM_API_BASE}{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": telegram_message(payload),
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if response.ok:
            return True, "sent"
        return False, response.text
    except Exception as exc:
        return False, str(exc)


def send_to_google_sheet(payload: dict[str, Any], telegram_sent: bool, telegram_error: str) -> None:
    if not GOOGLE_SHEET_URL:
        return
    try:
        data = dict(payload)
        data["telegram_sent"] = telegram_sent
        data["telegram_error"] = telegram_error
        requests.post(GOOGLE_SHEET_URL, json=data, timeout=12)
    except Exception:
        pass


def append_signal_records(payload: dict[str, Any], telegram_sent: bool, telegram_error: str) -> None:
    ensure_csv_files()
    sig_type = classify_signal(payload)
    now_str = current_time_str()
    ticker = payload.get("ticker") or payload.get("symbol") or ""
    timeframe = str(payload.get("timeframe") or "")
    payload_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    with csv_lock:
        if sig_type == "TRIGGER":
            trigger_id = str(payload.get("trigger_id") or payload.get("trade_id") or uuid.uuid4())
            trigger_close = str(payload.get("trigger_close") or payload.get("price") or "")
            notes = str(payload.get("notes") or payload.get("comment") or payload.get("message") or "")

            trigger_row = {col: "" for col in TRIGGER_COLUMNS}
            trigger_row.update({
                "trigger_id": trigger_id,
                "ticker": ticker,
                "timeframe": timeframe,
                "trigger_date": now_str,
                "trigger_close": trigger_close,
                "days_since_trigger": "0",
                "status": "Watching",
                "converted_to_trade_id": "",
                "notes": notes,
                "telegram_sent": str(telegram_sent),
                "telegram_error": telegram_error,
                "payload_json": payload_json
            })

            rows = []
            if TRIGGER_CSV_PATH.exists():
                with TRIGGER_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        rows = list(reader)
            rows.append(trigger_row)
            with TRIGGER_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TRIGGER_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)

        elif sig_type == "ENTRY":
            trade_id = str(payload.get("trade_id") or uuid.uuid4())
            grade = str(payload.get("grade") or "A")
            entry_price = str(payload.get("entry_price") or payload.get("price") or "")
            sl = str(payload.get("sl") or "")
            tp1 = str(payload.get("tp1") or "")
            tp2 = str(payload.get("tp2") or "")
            tp3 = str(payload.get("tp3") or "")
            tp_main = str(payload.get("tp_main") or "")
            rr_planned = calculate_rr_planned(entry_price, sl, tp_main)
            notes = str(payload.get("notes") or payload.get("comment") or payload.get("message") or "")

            # 1. Update matching watching trigger in Trigger Watchlist if present
            if TRIGGER_CSV_PATH.exists():
                t_rows = []
                with TRIGGER_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        t_rows = list(reader)
                
                for r in reversed(t_rows):
                    if r.get("ticker") == ticker and r.get("status") == "Watching":
                        r["converted_to_trade_id"] = trade_id
                        r["status"] = "Converted"
                        break
                
                with TRIGGER_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=TRIGGER_COLUMNS)
                    writer.writeheader()
                    writer.writerows(t_rows)

            # 2. Append new trade to Trade Log
            trade_row = {col: "" for col in TRADE_COLUMNS}
            trade_row.update({
                "trade_id": trade_id,
                "ticker": ticker,
                "timeframe": timeframe,
                "grade": grade,
                "entry_date": now_str,
                "entry_price": entry_price,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "tp_main": tp_main,
                "rr_planned": rr_planned,
                "outcome": "Open",
                "telegram_sent": str(telegram_sent),
                "telegram_error": telegram_error,
                "notes": notes,
                "payload_json": payload_json
            })

            rows = []
            if TRADE_CSV_PATH.exists():
                with TRADE_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        rows = list(reader)
            rows.append(trade_row)
            with TRADE_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)

        elif sig_type == "EXIT":
            exit_price = str(payload.get("exit_price") or payload.get("price") or "")
            exit_reason = str(payload.get("comment") or payload.get("reason") or "Exit signal")
            notes = str(payload.get("notes") or payload.get("message") or "")

            rows = []
            if TRADE_CSV_PATH.exists():
                with TRADE_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        rows = list(reader)

            updated = False
            for row in reversed(rows):
                if row.get("ticker") == ticker and (row.get("outcome") == "Open" or not row.get("exit_date")):
                    row["exit_date"] = now_str
                    row["exit_price"] = exit_price
                    row["exit_reason"] = exit_reason
                    
                    entry_d = row.get("entry_date", "")
                    days_in_trade = calculate_days_between(entry_d, now_str)
                    row["days_in_trade"] = days_in_trade

                    reason_upper = exit_reason.upper()
                    if "TP1" in reason_upper:
                        row["days_to_tp1"] = days_in_trade
                    if "TP2" in reason_upper:
                        row["days_to_tp2"] = days_in_trade
                    if "TP3" in reason_upper:
                        row["days_to_tp3"] = days_in_trade
                    if "TARGET" in reason_upper or "TP_MAIN" in reason_upper:
                        row["days_to_target"] = days_in_trade

                    entry_p = row.get("entry_price", "")
                    sl_p = row.get("sl", "")
                    r_mult = calculate_r_multiple(entry_p, sl_p, exit_price)
                    ret_pct = calculate_return_pct(entry_p, exit_price)
                    row["r_multiple"] = r_mult
                    row["return_pct"] = ret_pct
                    row["outcome"] = determine_outcome(exit_reason, r_mult)
                    updated = True
                    break

            if not updated:
                orphaned_row = {col: "" for col in TRADE_COLUMNS}
                orphaned_row.update({
                    "trade_id": str(payload.get("trade_id") or uuid.uuid4()),
                    "ticker": ticker,
                    "timeframe": timeframe,
                    "exit_date": now_str,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "outcome": "Orphaned Exit",
                    "telegram_sent": str(telegram_sent),
                    "telegram_error": telegram_error,
                    "notes": notes,
                    "payload_json": payload_json
                })
                rows.append(orphaned_row)

            with TRADE_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "status": "online",
        "service": "TradingView Signal Relay",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
        "webhook": "/webhook",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_pinescript(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    raw_body = await request.body()
    content_type = request.headers.get("content-type", "")

    if raw_body:
        if "application/json" in content_type.lower():
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc.msg}") from exc
        else:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                payload = {"message": raw_body.decode("utf-8", errors="replace")}
    else:
        payload = {}

    normalized = normalize_payload(payload)
    authenticate(normalized)

    if not normalized.get("message"):
        normalized["message"] = normalized.get("signal_type") or "TradingView signal"

    telegram_sent, telegram_status = send_telegram_alert(normalized)
    append_signal_records(normalized, telegram_sent=telegram_sent, telegram_error="" if telegram_sent else telegram_status)
    background_tasks.add_task(send_to_google_sheet, normalized, telegram_sent, "" if telegram_sent else telegram_status)

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "received_at": current_time_str(),
            "signal_type": classify_signal(normalized),
            "telegram_sent": telegram_sent,
            "telegram_status": telegram_status,
        },
    )


@app.post("/webhook/signal")
async def webhook_signal(signal: SignalEnvelope, background_tasks: BackgroundTasks) -> JSONResponse:
    payload = signal.model_dump(exclude_none=True)
    authenticate(payload)

    telegram_sent, telegram_status = send_telegram_alert(payload)
    append_signal_records(payload, telegram_sent=telegram_sent, telegram_error="" if telegram_sent else telegram_status)
    background_tasks.add_task(send_to_google_sheet, payload, telegram_sent, "" if telegram_sent else telegram_status)

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "received_at": current_time_str(),
            "signal_type": classify_signal(payload),
            "telegram_sent": telegram_sent,
            "telegram_status": telegram_status,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"status": "error", "detail": exc.detail})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")), reload=True)