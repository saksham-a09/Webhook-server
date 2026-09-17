# TradingView Signal Relay & Setup Tracker

FastAPI server that receives TradingView / Pine Script webhook alerts, forwards formatted notifications to Telegram, and logs setups across dedicated CSV files and Google Sheets matching the **Setup Tracker** architecture.

---

## System Overview

The system manages trade signals across two dedicated tracking sheets:

1. **`Trigger Watchlist` Sheet / CSV**:
   - Logs incoming `TRIGGER` alerts when a setup first fires.
   - Dynamically calculates `Days Since Trigger`.
   - Maintains setup status (`Watching`, `Converted`, `Expired - No Entry`).
   - Automatically cross-links `Converted to Trade ID` when a trade entry occurs.

2. **`Trade Log` Sheet / CSV**:
   - Logs executed entries (`LONG` / `BUY`) with setup conviction `Grade` (`A++`, `A+`, `A`, `B`), `Entry Price`, `Stop Loss`, and multi-tier profit targets (`TP1`, `TP2`, `TP3`, `Target (Main)`).
   - Computes planned Risk/Reward: `(Target - Entry) / (Entry - SL)`.
   - Logs `EXIT` executions, computes `Days in Trade`, milestone durations (`Days to TP1..3`, `Days to Target`), `Outcome` (`Win`, `Loss`, `Breakeven`), realized `R Multiple`, and `% Return`.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Service status and version information |
| GET | `/health` | Health check endpoint |
| GET | `/docs` | Interactive Swagger API documentation |
| POST | `/webhook` | Accepts standard TradingView / Pine Script JSON webhook payloads |
| POST | `/webhook/signal` | Accepts structured `SignalEnvelope` JSON payloads |

---

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

---

## Google Sheets Setup (Apps Script)

Follow these steps to deploy the Google Apps Script webhook:

1. Open your Google Sheet (or use your template with `Trigger Watchlist`, `Trade Log`, `Setup Performance`, and `Legend`).
2. In the top menu, go to **Extensions** $\rightarrow$ **Apps Script**.
3. Clear any existing code and paste the script below:

```javascript
/**
 * TradingView Signal Relay - Google Apps Script Webhook
 * Supports separate 'Trigger Watchlist' and 'Trade Log' sheets with automatic conversion linking.
 */

function getOrCreateSheet(ss, name, headers) {
  var sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    if (headers && headers.length > 0) {
      sheet.appendRow(headers);
      sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold").setBackground("#f3f3f3");
      sheet.setFrozenRows(1);
    }
  } else if (sheet.getLastRow() === 0 && headers && headers.length > 0) {
    sheet.appendRow(headers);
    sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold").setBackground("#f3f3f3");
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    
    var triggerHeaders = [
      "Trigger ID", "Ticker", "Timeframe", "Trigger Date", "Trigger Close",
      "Days Since Trigger", "Status", "Converted to Trade ID", "Notes"
    ];
    
    var tradeHeaders = [
      "Trade ID", "Ticker", "Timeframe", "Grade", "Entry Date", "Entry Price",
      "Stop Loss", "TP1", "TP2", "TP3", "Target (Main)", "R:R Planned",
      "Exit Date", "Exit Price", "Exit Reason", "Days in Trade",
      "Days to TP1", "Days to TP2", "Days to TP3", "Days to Target",
      "Outcome", "R Multiple", "% Return"
    ];
    
    var triggerSheet = getOrCreateSheet(ss, "Trigger Watchlist", triggerHeaders);
    var tradeSheet = getOrCreateSheet(ss, "Trade Log", tradeHeaders);
    
    var nowStr = Utilities.formatDate(new Date(), "GMT-4", "yyyy-MM-dd HH:mm:ss");
    var todayDateStr = Utilities.formatDate(new Date(), "GMT-4", "yyyy-MM-dd");
    
    var msgType = (payload.type || payload.signal_type || "").toString().toUpperCase();
    var side = (payload.side || "").toString().toUpperCase();
    var action = (payload.action || "").toString().toLowerCase();
    var comment = payload.comment || payload.reason || "";
    var ticker = (payload.ticker || payload.symbol || "").toString().trim();
    var timeframe = payload.timeframe || "";
    
    var isTrigger = msgType === "TRIGGER";
    var isExit = side === "EXIT" || action === "sell" || action === "close" || comment.indexOf("Sl") !== -1 || comment.indexOf("Tp") !== -1 || payload.exit_price !== undefined;
    var isEntry = !isTrigger && !isExit && (side === "LONG" || side === "SHORT" || action === "buy" || action === "entry" || payload.entry_price !== undefined);
    
    if (isTrigger) {
      var triggerId = payload.trigger_id || payload.trade_id || Utilities.getUuid();
      var triggerClose = payload.trigger_close || payload.price || "";
      var notes = payload.notes || payload.comment || payload.message || "";
      var nextRow = triggerSheet.getLastRow() + 1;
      
      var rowData = [
        triggerId,
        ticker,
        timeframe,
        todayDateStr,
        triggerClose,
        '=IF(D' + nextRow + '="","",TODAY()-INT(D' + nextRow + '))',
        '=IF(D' + nextRow + '="","",IF(H' + nextRow + '<>"","Converted",IF(F' + nextRow + '>25,"Expired - No Entry","Watching")))',
        "",
        notes
      ];
      triggerSheet.appendRow(rowData);
    }
    else if (isEntry) {
      var tradeId = payload.trade_id || Utilities.getUuid();
      var grade = payload.grade || "A";
      var entryPrice = payload.entry_price || payload.price || "";
      var sl = payload.sl || "";
      var tp1 = payload.tp1 || "";
      var tp2 = payload.tp2 || "";
      var tp3 = payload.tp3 || "";
      var tpMain = payload.tp_main || "";
      var nextRow = tradeSheet.getLastRow() + 1;
      
      // Update matching Watching row in Trigger Watchlist
      var triggerData = triggerSheet.getDataRange().getValues();
      for (var i = triggerData.length - 1; i >= 1; i--) {
        var tTicker = triggerData[i][1];
        var tConverted = triggerData[i][7];
        if (tTicker === ticker && !tConverted) {
          triggerSheet.getRange(i + 1, 8).setValue(tradeId);
          break;
        }
      }
      
      var rowData = [
        tradeId,
        ticker,
        timeframe,
        grade,
        todayDateStr,
        entryPrice,
        sl,
        tp1,
        tp2,
        tp3,
        tpMain,
        '=IFERROR((K' + nextRow + '-F' + nextRow + ')/(F' + nextRow + '-G' + nextRow + '),"")',
        "", // Exit Date
        "", // Exit Price
        "", // Exit Reason
        '=IF(OR(M' + nextRow + '="",E' + nextRow + '=""),"",INT(M' + nextRow + ')-INT(E' + nextRow + '))',
        "", // Days to TP1
        "", // Days to TP2
        "", // Days to TP3
        "", // Days to Target
        '=IF(O' + nextRow + '="","Open",IF(OR(REGEXMATCH(TO_TEXT(O' + nextRow + '),"(?i)tp")),"Win",IF(REGEXMATCH(TO_TEXT(O' + nextRow + '),"(?i)be"),"Breakeven","Loss")))',
        '=IFERROR(IF(N' + nextRow + '="","",(N' + nextRow + '-F' + nextRow + ')/(F' + nextRow + '-G' + nextRow + ')),"")',
        '=IFERROR(IF(N' + nextRow + '="","",(N' + nextRow + '-F' + nextRow + ')/F' + nextRow + '),"")'
      ];
      tradeSheet.appendRow(rowData);
    }
    else if (isExit) {
      var tradeData = tradeSheet.getDataRange().getValues();
      var updated = false;
      var exitPrice = payload.exit_price || payload.price || "";
      var exitReason = comment || payload.reason || "Exit signal";
      
      for (var j = tradeData.length - 1; j >= 1; j--) {
        var rowTicker = tradeData[j][1];
        var rowExitDate = tradeData[j][12];
        var rowOutcome = tradeData[j][20];
        
        if (rowTicker === ticker && (!rowExitDate || rowOutcome === "Open")) {
          var rowIndex = j + 1;
          tradeSheet.getRange(rowIndex, 13).setValue(todayDateStr); // Exit Date
          tradeSheet.getRange(rowIndex, 14).setValue(exitPrice);    // Exit Price
          tradeSheet.getRange(rowIndex, 15).setValue(exitReason);   // Exit Reason
          
          var reasonUpper = exitReason.toUpperCase();
          var entryDateVal = tradeData[j][4];
          var daysInTrade = "";
          if (entryDateVal) {
            var diffMs = (new Date()).getTime() - (new Date(entryDateVal)).getTime();
            daysInTrade = Math.max(0, Math.floor(diffMs / (1000 * 60 * 60 * 24)));
          }
          
          if (reasonUpper.indexOf("TP1") !== -1) {
            tradeSheet.getRange(rowIndex, 17).setValue(daysInTrade);
          } else if (reasonUpper.indexOf("TP2") !== -1) {
            tradeSheet.getRange(rowIndex, 18).setValue(daysInTrade);
          } else if (reasonUpper.indexOf("TP3") !== -1) {
            tradeSheet.getRange(rowIndex, 19).setValue(daysInTrade);
          } else if (reasonUpper.indexOf("TARGET") !== -1 || reasonUpper.indexOf("TP_MAIN") !== -1) {
            tradeSheet.getRange(rowIndex, 20).setValue(daysInTrade);
          }
          
          updated = true;
          break;
        }
      }
      
      if (!updated) {
        var nextRow = tradeSheet.getLastRow() + 1;
        var orphanRow = [
          payload.trade_id || Utilities.getUuid(),
          ticker,
          timeframe,
          "",
          "",
          "",
          "",
          "",
          "",
          "",
          "",
          "",
          todayDateStr,
          exitPrice,
          exitReason,
          "",
          "",
          "",
          "",
          "",
          "Orphaned Exit",
          "",
          ""
        ];
        tradeSheet.appendRow(orphanRow);
      }
    }
    
    return ContentService.createTextOutput(JSON.stringify({ status: "success" }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
```

4. Click **Deploy** $\rightarrow$ **New deployment**.
5. Set **Select type** to **Web app**.
6. Set **Execute as** to **Me**.
7. Set **Who has access** to **Anyone**.
8. Click **Deploy**, authorize permissions, and copy the **Web app URL**.
9. Set `GOOGLE_SHEET_URL` in your `.env` file.

---

## TradingView Alert JSON Templates

### 1. Watchlist Trigger Alert (Logs to `Trigger Watchlist`)

```json
{
  "token": "your-shared-secret",
  "type": "TRIGGER",
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "trigger_close": {{close}},
  "notes": "Setup trigger alert"
}
```

### 2. Trade Entry Alert (Logs to `Trade Log` & converts trigger)

```json
{
  "token": "your-shared-secret",
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "side": "LONG",
  "grade": "A+",
  "entry_price": {{close}},
  "sl": 198.5,
  "tp1": 220.0,
  "tp2": 232.0,
  "tp3": 248.0,
  "tp_main": 240.0
}
```

### 3. Trade Exit Alert (Closes trade in `Trade Log`)

```json
{
  "token": "your-shared-secret",
  "ticker": "{{ticker}}",
  "side": "EXIT",
  "exit_price": {{close}},
  "comment": "Tp2"
}
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_TOKEN` | | Shared secret for auth (optional) |
| `TELEGRAM_BOT_TOKEN` | | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | | Target chat ID |
| `GOOGLE_SHEET_URL` | | Google Apps Script Web App URL |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |
| `DATA_DIR` | `data` | Directory for CSV logs |
| `TRIGGER_CSV_PATH` | `data/trigger_watchlist.csv` | Trigger Watchlist CSV path |
| `TRADE_CSV_PATH` | `data/trade_log.csv` | Trade Log CSV path |