# Alpaca Paper-Trade Review Helper (read-only)

This small tool reads your **Alpaca PAPER** account and turns your fills into a `trades.json` that the Trading Practice Academy imports for its **Review Gate** (the Coach Wick report card).

**Safety by design**
- It only sends **GET** requests. There's no code that places, changes or cancels orders.
- It only talks to `https://paper-api.alpaca.markets`. Any other URL, including the live one, is refused.
- Keys are read from environment variables or a local `.env` file and are never written to `trades.json`. The Academy never asks for keys.
- It uses only the Python standard library, so there's nothing to `pip install`.

## Windows steps (Command Prompt)

1. **Install Python 3** from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** during setup.
2. Unzip the Academy and open this folder (`trading-academy\alpaca-helper`) in File Explorer.
3. Get your **paper** API keys: log in to Alpaca, switch to the **Paper** account, then generate API keys on the dashboard.
4. Create your `.env`. In Command Prompt:
   ```
   cd /d "%USERPROFILE%\Downloads\trading-academy\alpaca-helper"
   copy .env.example .env
   notepad .env
   ```
   Paste your paper Key ID and Secret, then save. Never share this file.
5. Run the review. Either double-click **`run_review.bat`**, or run:
   ```
   python alpaca_review.py
   ```
   Optional: `python alpaca_review.py --after 2026-09-01` limits it to recent fills.
6. A **`trades.json`** file appears in this folder. In the Academy, open **Review Gate**, choose **Import Alpaca paper trades**, and pick `trades.json`.
7. Write a journal note for every imported trade (or link it to a simulator journal entry), then generate your report card.

**No keys yet?** Run `python alpaca_review.py --demo` to create a sample `trades.json` and see how the Review Gate works. Demo data won't unlock anything meaningful, so use your real paper trades for that.

## What it calculates
- Round-trip trades: fills are paired FIFO per symbol, from flat back to flat. A position flip closes one trade and opens another.
- Win rate, average win and loss ($), expectancy ($ per trade), profit factor, max drawdown (% from peak).
- **R-multiples:** if your entries were **bracket/OTO orders**, the stop leg's price is used (1R = |entry − stop| × shares). Without stop data, R is estimated as 1R = your average losing trade, and the method is labelled in the output.
- Estimated risk % per trade = stop risk ÷ equity before the trade (needs bracket stops). Bracket orders make your review far more accurate.

## Troubleshooting
- `REFUSED: this tool only talks to ...`: remove `ALPACA_BASE_URL` from `.env` or set it to the paper URL.
- `HTTP 401/403`: the keys are wrong, or they're live keys. Generate **paper** keys.
- `python is not recognized`: reinstall Python with "Add to PATH" ticked, then open a new Command Prompt.

Educational tool only, not financial advice.
