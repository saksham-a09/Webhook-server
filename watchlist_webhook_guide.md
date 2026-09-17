# Simple Guide: Setting Up TradingView Watchlist Webhook Alerts

This guide shows you how to set up TradingView alerts for an entire watchlist and send the signals automatically to your webhook server and Google Sheets (`Trigger Watchlist` & `Trade Log`).

---

## 1. Prerequisites

Before starting, make sure you have:
1. **A TradingView Paid Plan:** (Premium, Expert, or Ultimate). Watchlist Alerts is a TradingView feature for these tiers.
2. **Your Webhook Server URL:** (e.g., `http://your-server-ip:8000/webhook` or your public domain URL).
3. **Your Webhook Token:** The secret password configured in your [.env](file:///e:/Codes/Sand/.env) file under `WEBHOOK_TOKEN` (optional).

---

## 2. Step-by-Step Configuration

### Step 1: Open your Watchlist
1. Open any chart on TradingView.
2. In the right-hand sidebar, open the **Watchlist** panel.
3. Choose the watchlist you want to automate.

### Step 2: Create the Watchlist Alert
1. Click the **three-dot menu (`...`)** next to your watchlist name.
2. Select **"Add alert on list..."** (or **"Create alert on list"**).
3. Define your alert condition (e.g., when price crosses a moving average or when a specific indicator triggers). This single alert will monitor every stock/crypto in your watchlist.

### Step 3: Set up the Webhook Notification
1. Switch to the **Notifications** tab inside the alert pop-up window.
2. Check the box for **Webhook URL**.
3. Paste your server URL: 
   ```text
   http://<your-server-ip>:8000/webhook
   ```
   *(Replace `<your-server-ip>` with your actual server IP or domain address).*

---

## 3. Signal Message Templates (JSON)

Go to the **Settings** tab in the alert window, scroll to the **Message** box, and paste the appropriate JSON template below:

### Option A: Watchlist Setup Trigger Alert
*Use this alert to log potential setups to the **`Trigger Watchlist`** sheet when a watchlist indicator condition triggers.*

```json
{
  "token": "YOUR_WEBHOOK_TOKEN",
  "type": "TRIGGER",
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "trigger_close": {{close}},
  "notes": "Watchlist setup trigger"
}
```

---

### Option B: Trade Entry Alert
*Use this alert when a trade is entered. It logs to the **`Trade Log`** sheet and automatically marks the setup in **`Trigger Watchlist`** as **`Converted`**.*

```json
{
  "token": "YOUR_WEBHOOK_TOKEN",
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "side": "LONG",
  "grade": "A+",
  "entry_price": {{close}},
  "sl": 198.5,
  "tp1": 220.0,
  "tp2": 232.0,
  "tp3": 248.0,
  "tp_main": 240.0,
  "notes": "Executed entry"
}
```

---

### Option C: Trade Exit Alert (Take Profit / Stop Loss / Close)
*Use this alert to close an open trade in the **`Trade Log`** sheet. The sheet automatically calculates Days in Trade, Win/Loss Outcome, Realized R-Multiple, and % Return.*

```json
{
  "token": "YOUR_WEBHOOK_TOKEN",
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "side": "EXIT",
  "exit_price": {{close}},
  "comment": "Tp2"
}
```
*(Common comments: `"Tp1"`, `"Tp2"`, `"Tp3"`, `"Target"`, `"Sl"`, `"Be"`, `"Manual"`).*

---

## 4. Troubleshooting Checklist

- **No alert received?** Make sure the `"token"` in your message matches what is configured in your [.env](file:///e:/Codes/Sand/.env) file (leave blank if not using token auth).
- **No Telegram messages?** Check that your server is running and your `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set correctly in your [.env](file:///e:/Codes/Sand/.env) configuration.
- **Formatting errors?** Make sure the message box has no missing quotes or extra commas.
