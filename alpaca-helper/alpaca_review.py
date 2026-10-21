#!/usr/bin/env python3
"""
Trading Practice Academy - Alpaca PAPER trade reviewer (READ-ONLY).

What it does
  * Reads your Alpaca PAPER API keys from environment variables or a local .env file:
        ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY
  * Talks ONLY to https://paper-api.alpaca.markets (anything else is refused).
  * Sends ONLY HTTP GET requests (no code path exists that can place, change or cancel orders).
  * Pulls /v2/account, /v2/account/activities/FILL (paginated) and, if possible,
    /v2/orders?status=all&nested=true to find the stop-loss price of bracket/OTO orders.
  * Pairs fills into round-trip trades (FIFO, flat -> flat per symbol), computes stats,
    and writes trades.json for the Academy's "Import Alpaca paper trades" button.

Usage (Windows cmd):   python alpaca_review.py            (or double-click run_review.bat)
Options:               --after 2026-09-01   only fills after this date
                       --out trades.json    output path
                       --demo               no network: build a sample trades.json from fake fills
Standard library only (Python 3.8+). Educational tool, not financial advice.
"""
import argparse, json, os, sys, urllib.parse, urllib.request, urllib.error, datetime, random
from collections import defaultdict, deque

PAPER_URL = "https://paper-api.alpaca.markets"

def load_env():
    """Load KEY=VALUE lines from a .env next to this script or in the current folder (does not override real env vars)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, ".env"), os.path.join(os.getcwd(), ".env")):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            break

def check_base_url():
    base = os.environ.get("ALPACA_BASE_URL", PAPER_URL).strip().rstrip("/")
    if base.endswith("/v2"):
        base = base[:-3]
    if base != PAPER_URL:
        sys.exit(f"REFUSED: this tool only talks to {PAPER_URL} (paper trading). Got: {base}\n"
                 "Remove ALPACA_BASE_URL from your .env or set it to the paper URL.")
    return base

class ReadOnlyClient:
    def __init__(self, base, key, secret):
        self.base, self.h = base, {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}
    def get(self, path, params=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        if not url.startswith(PAPER_URL + "/"):
            raise RuntimeError("Refusing non-paper URL")
        req = urllib.request.Request(url, headers=self.h, method="GET")   # GET only, by design
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:300]
            if e.code in (401, 403):
                sys.exit(f"Alpaca rejected the keys (HTTP {e.code}). Make sure they are PAPER keys. {body}")
            raise RuntimeError(f"HTTP {e.code} on {path}: {body}")

def fetch_fills(c, after=None):
    fills, token = [], None
    while True:
        p = {"direction": "asc", "page_size": 100}
        if after: p["after"] = after
        if token: p["page_token"] = token
        page = c.get("/v2/account/activities/FILL", p)
        if not page: break
        fills.extend(page)
        token = page[-1]["id"]
        if len(page) < 100: break
    return fills

def fetch_stops(c, after=None):
    """Map entry order id -> stop price from bracket/OTO legs. Best effort; returns {} on any error."""
    stops, cursor = {}, after or "2015-01-01T00:00:00Z"
    try:
        for _ in range(50):
            page = c.get("/v2/orders", {"status": "all", "nested": "true", "direction": "asc", "limit": 500, "after": cursor})
            if not page: break
            for o in page:
                for leg in (o.get("legs") or []):
                    if leg.get("stop_price"):
                        stops[o["id"]] = float(leg["stop_price"])
            if len(page) < 500: break
            cursor = page[-1]["submitted_at"]
    except Exception as e:
        print("  (could not read orders for stop prices; R will be estimated:", e, ")")
    return stops

def pair_trades(fills, stops):
    """FIFO pairing per symbol. A trade = position cycle from flat to flat (a flip closes one trade and opens another)."""
    by = defaultdict(list)
    for f in fills:
        by[f["symbol"]].append(f)
    trades = []
    for sym, fs in by.items():
        fs.sort(key=lambda f: f["transaction_time"])
        lots, cur = deque(), None   # lots: [signed_qty, price]
        for f in fs:
            q, px = float(f["qty"]), float(f["price"])
            signed = q if f["side"] == "buy" else -q
            while signed != 0:
                pos = sum(l[0] for l in lots)
                if pos == 0 or (pos > 0) == (signed > 0):          # opening / adding
                    if pos == 0:
                        cur = {"symbol": sym, "side": "Long" if signed > 0 else "Short", "open": f["transaction_time"],
                               "entryQty": 0.0, "entryCost": 0.0, "exitQty": 0.0, "exitValue": 0.0, "pnl": 0.0,
                               "stop": stops.get(f.get("order_id")), "fills": 0}
                    lots.append([signed, px]); cur["entryQty"] += abs(signed); cur["entryCost"] += abs(signed) * px; cur["fills"] += 1
                    signed = 0
                else:                                              # reducing / closing (FIFO)
                    lot = lots[0]; take = min(abs(lot[0]), abs(signed))
                    direction = 1 if lot[0] > 0 else -1
                    cur["pnl"] += (px - lot[1]) * take * direction
                    cur["exitQty"] += take; cur["exitValue"] += take * px; cur["fills"] += 1
                    lot[0] -= take * direction; signed += take * direction
                    if abs(lot[0]) < 1e-9: lots.popleft()
                    if not lots:
                        cur["close"] = f["transaction_time"]; trades.append(cur); cur = None
    out = []
    for t in sorted(trades, key=lambda t: t["close"]):
        entry, exit_ = t["entryCost"] / t["entryQty"], t["exitValue"] / t["exitQty"]
        risk = abs(entry - t["stop"]) * t["entryQty"] if t["stop"] else None
        out.append({"symbol": t["symbol"], "side": t["side"], "open": t["open"], "close": t["close"], "qty": round(t["entryQty"], 4),
                    "entry": round(entry, 4), "exit": round(exit_, 4), "pnl": round(t["pnl"], 2), "stop": t["stop"],
                    "risk": round(risk, 2) if risk else None, "R": round(t["pnl"] / risk, 3) if risk else None, "fills": t["fills"]})
    return out

def summarize(trades, equity_now):
    n = len(trades)
    total = sum(t["pnl"] for t in trades)
    start_eq = (equity_now - total) if equity_now else 5000.0
    eq, peak, mdd = start_eq, start_eq, 0.0
    for t in trades:
        t["equityBefore"] = round(eq, 2)
        t["riskPct"] = round(t["risk"] / eq * 100, 3) if t.get("risk") and eq > 0 else None
        eq += t["pnl"]; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0)
    wins, losses = [t for t in trades if t["pnl"] > 0], [t for t in trades if t["pnl"] < 0]
    avg_w = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_l = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    with_r = [t for t in trades if t["R"] is not None]
    if with_r and len(with_r) >= max(1, n // 2):
        avg_r, r_method = sum(t["R"] for t in with_r) / len(with_r), "stop-based (from bracket/OTO stop legs)"
    elif losses:
        unit = abs(avg_l)   # $-based fallback: 1R ~= average losing trade
        avg_r, r_method = (total / n) / unit, "estimated ($-based: 1R = average loss)"
    else:
        avg_r, r_method = None, "unavailable"
    gl = abs(sum(t["pnl"] for t in losses))
    return {"trades": n, "wins": len(wins), "losses": len(losses), "winRate": round(len(wins) / n, 4) if n else 0,
            "avgWin": round(avg_w, 2), "avgLoss": round(avg_l, 2), "expectancyDollars": round(total / n, 2) if n else 0,
            "avgR": round(avg_r, 3) if avg_r is not None else None, "rMethod": r_method,
            "profitFactor": round(sum(t["pnl"] for t in wins) / gl, 3) if gl else None,
            "maxDrawdownPct": round(mdd * 100, 2), "netPnl": round(total, 2), "startEquityEst": round(start_eq, 2),
            "maxRiskPct": max([t["riskPct"] for t in trades if t.get("riskPct") is not None], default=None)}

def demo_fills():
    r, fills, t0 = random.Random(7), [], datetime.datetime(2026, 9, 1, 14, 0)
    for k in range(34):
        sym = r.choice(["NVDA", "AAPL", "MSFT", "SPY", "QQQ", "META"]); px = r.uniform(150, 450)
        stop = round(px * 0.994, 2); oid = f"demo-{k}"; q = max(1, int(45 / (px - stop)))  # ~0.9% risk on ~$5,000
        win = r.random() < 0.47; exit_ = px + (px - stop) * (r.uniform(1.2, 2.6) if win else -1.0)
        ts = t0 + datetime.timedelta(days=k, minutes=20)
        fills.append({"id": f"{k}a", "symbol": sym, "side": "buy", "qty": str(q), "price": f"{px:.2f}", "order_id": oid, "transaction_time": ts.isoformat() + "Z"})
        fills.append({"id": f"{k}b", "symbol": sym, "side": "sell", "qty": str(q), "price": f"{exit_:.2f}", "order_id": oid + "x", "transaction_time": (ts + datetime.timedelta(minutes=35)).isoformat() + "Z"})
    stops = {f"demo-{k}": round(float(fills[2 * k]["price"]) * 0.994, 2) for k in range(34)}
    return fills, stops

def main():
    ap = argparse.ArgumentParser(description="Read-only Alpaca PAPER trade review for the Trading Practice Academy")
    ap.add_argument("--after", help="only fills after this date, e.g. 2026-09-01")
    ap.add_argument("--out", default="trades.json")
    ap.add_argument("--demo", action="store_true", help="offline sample data, no keys needed")
    a = ap.parse_args()
    account = {}
    if a.demo:
        fills, stops = demo_fills(); account = {"status": "DEMO"}
        print("DEMO mode: no network calls.")
    else:
        load_env(); base = check_base_url()
        key, sec = os.environ.get("ALPACA_API_KEY_ID", ""), os.environ.get("ALPACA_API_SECRET_KEY", "")
        if not key or not sec:
            sys.exit("Missing keys. Copy .env.example to .env and paste your PAPER key id and secret (see README.md).")
        c = ReadOnlyClient(base, key, sec)
        acct = c.get("/v2/account")
        account = {k: acct.get(k) for k in ("status", "currency", "equity", "last_equity", "cash", "buying_power", "multiplier")}
        account["account_number"] = "****" + str(acct.get("account_number", ""))[-4:]
        print(f"Paper account {account['account_number']}  equity ${float(account['equity'] or 0):,.2f}")
        fills = fetch_fills(c, a.after); print(f"Fetched {len(fills)} fills")
        stops = fetch_stops(c, a.after)
    trades = pair_trades(fills, stops)
    if a.demo:
        account["equity"] = account["cash"] = round(5000 + sum(t["pnl"] for t in trades), 2)  # demo starts at $5,000
    s = summarize(trades, float(account.get("equity") or 0) or None)
    out = {"source": "demo" if a.demo else "alpaca-paper", "generated": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"), "account": account, "summary": s, "trades": trades}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"\nRound-trip trades: {s['trades']}   win rate {s['winRate']*100:.1f}%   avg win ${s['avgWin']}   avg loss ${s['avgLoss']}")
    print(f"Expectancy ${s['expectancyDollars']}/trade   avg R {s['avgR']} ({s['rMethod']})   profit factor {s['profitFactor']}   max drawdown {s['maxDrawdownPct']}%")
    print(f"\nWrote {os.path.abspath(a.out)}  -> in the Academy: Review Gate > Import Alpaca paper trades")

if __name__ == "__main__":
    main()
