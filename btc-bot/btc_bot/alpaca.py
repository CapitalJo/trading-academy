"""
Minimal Alpaca PAPER trading client (standard library only).

Safety: every request is checked against the paper URL. The live endpoint is refused, so this
code cannot trade real money. Going live should be a deliberate code change after a
long paper track record, not a config flag.
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error

PAPER_URL = "https://paper-api.alpaca.markets"
SYMBOL = "BTC/USD"
POS_SYMBOL = "BTCUSD"


def load_env(path):
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class PaperClient:
    def __init__(self, key, secret, base=PAPER_URL):
        base = base.rstrip("/").removesuffix("/v2")
        if base != PAPER_URL:
            sys.exit(f"REFUSED: this bot only talks to {PAPER_URL} (paper trading). Got: {base}")
        self.base = base
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                  "Accept": "application/json", "Content-Type": "application/json"}

    @classmethod
    def from_env(cls):
        key, sec = os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY")
        if not key or not sec:
            sys.exit("Missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY: put that strategy's PAPER keys in btc-bot/.env.S1 or .env.S2 (see README).")
        return cls(key, sec, os.environ.get("ALPACA_BASE_URL", PAPER_URL))

    def _req(self, method, path, params=None, body=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        if not url.startswith(PAPER_URL + "/"):
            raise RuntimeError("Refusing non-paper URL")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self.h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:400]
            if e.code == 404 and method == "GET" and path.startswith("/v2/positions/"):
                return None
            if e.code in (401, 403):
                sys.exit(f"Alpaca rejected the keys (HTTP {e.code}). Use PAPER keys. {msg}")
            raise RuntimeError(f"Alpaca {method} {path} -> HTTP {e.code}: {msg}")

    # --- reads
    def account(self): return self._req("GET", "/v2/account")
    def position(self): return self._req("GET", f"/v2/positions/{POS_SYMBOL}")
    def open_orders(self):
        return [o for o in self._req("GET", "/v2/orders", {"status": "open", "limit": 100})
                if o["symbol"].replace("/", "") == POS_SYMBOL]
    def order(self, oid): return self._req("GET", f"/v2/orders/{oid}")

    # --- writes (paper only)
    def market_buy_notional(self, usd, client_id):
        return self._req("POST", "/v2/orders", body={"symbol": SYMBOL, "side": "buy", "type": "market",
                         "notional": f"{usd:.2f}", "time_in_force": "gtc", "client_order_id": client_id})

    def market_sell_qty(self, qty, client_id):
        return self._req("POST", "/v2/orders", body={"symbol": SYMBOL, "side": "sell", "type": "market",
                         "qty": f"{qty:.9f}".rstrip("0").rstrip("."), "time_in_force": "gtc", "client_order_id": client_id})

    def stop_limit_sell(self, qty, stop, limit, client_id):
        return self._req("POST", "/v2/orders", body={"symbol": SYMBOL, "side": "sell", "type": "stop_limit",
                         "qty": f"{qty:.9f}".rstrip("0").rstrip("."), "stop_price": f"{stop:.2f}",
                         "limit_price": f"{limit:.2f}", "time_in_force": "gtc", "client_order_id": client_id})

    def cancel(self, oid): return self._req("DELETE", f"/v2/orders/{oid}")
    def close_position(self): return self._req("DELETE", f"/v2/positions/{POS_SYMBOL}")
