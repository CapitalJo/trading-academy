#!/usr/bin/env python3
"""Regenerate STRATEGY_LOG.md from strategies.json:  python strategy_log.py"""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = {"PROCEED": 0, "CANDIDATE": 1, "PARKED": 2, "REJECTED": 3}


def main():
    d = json.load(open(os.path.join(HERE, "strategies.json")))
    md = ["# Strategy log", "",
          "Every BTC strategy considered for this bot, its status, and the evidence behind the decision. Machine-readable copy: `strategies.json`.",
          "The bot stamps each row of `bot_log_<id>.csv` with the strategy id, version and fingerprint, so live results always trace back to an entry here.", "",
          "Status: **PROCEED** = paper trade it · **CANDIDATE** = passed tests, needs your decision · **PARKED** = sound but blocked · **REJECTED** = failed, do not trade.", "",
          "**Being paper traded:** " + ", ".join(f"`{a}`" for a in (d["active"] if isinstance(d["active"], list) else [d["active"]])), "",
          "| ID | Strategy | Status | Decided | Key evidence | Next step |", "|---|---|---|---|---|---|"]
    for s in sorted(d["strategies"], key=lambda s: (ORDER[s["status"]], s["id"])):
        md.append(f"| {s['id']} | {s['name']} | **{s['status']}** | {s['decided']} | {s['evidence']} | {s['next']} |")
    md += ["", "## Rules tested and rejected inside the Flow Regime strategy", ""] + [f"- {r}" for r in d["rejected_rules"]]
    md += ["", "## Adding a new strategy", "",
           "1. Add an entry to `strategies.json` with status `CANDIDATE` and its source.",
           "2. Test it the same way: choose settings on 2020-22 only, report 2023-26 untouched, include costs, and show win rate, average win/loss and Monte Carlo drawdown.",
           "3. Record the verdict and evidence, then regenerate this file: `python strategy_log.py`.",
           "4. Add it to `VARIANTS` in `btc_bot/strategy.py`; only a signed-off fingerprint (`python signoff.py --strategy <id> --approve`) can trade."]
    open(os.path.join(HERE, "STRATEGY_LOG.md"), "w").write("\n".join(md) + "\n")
    print("Wrote STRATEGY_LOG.md")


if __name__ == "__main__":
    main()
