#!/usr/bin/env python3
"""Side-by-side scoreboard for demo/sidebyside: time, tokens, cost, hidden tests and the 9 rule checks.

    demo/sidebyside/scoreboard.py [OUT] [--price USD_PER_MILLION_TOKENS]

Claude Code reports each session's cost (the API-equivalent price). Cursor doesn't, so for Cursor runs pass
`--price` to estimate it from tokens; the table then says "estimated".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Rental's hidden tests that check a rule given in tickets 1-3 (or 9) and needed later; the other 14 check the
# ticket's own feature.
RULES = {
    "test_the_deposit_is_four_months_rent": "4-month deposit",
    "test_residential_deposits_are_capped_in_model_tenancy_act_states": "Model Tenancy Act cap",
    "test_agreements_longer_than_11_months_are_refused": "11-month agreements",
    "test_a_quarter_of_the_deposit_stays_in_reserve": "25 % reserve",
    "test_without_consent_nothing_is_deployed": "no deposit placed without consent",
    "test_the_partner_pays_9_percent_split_2_3_and_the_rest": "9 % yield split 2/3/rest",
    "test_from_april_the_partner_pays_10_percent_and_shares_stay": "10 % from April",
    "test_the_statement_masks_tenant_phones": "phone numbers masked",
    "test_platform_earnings_follow_the_reserve_consent_and_rates": "platform report",
}
SIDES = {"without": "rental-baseline", "with": "rental-lean"}


def load(out: Path, side: str) -> list[dict] | None:
    path = out / side / SIDES[side] / "results.json"
    return json.loads(path.read_text())["sessions"] if path.exists() else None


def summary(sessions: list[dict], price: float | None) -> dict:
    last = sessions[-1]["hidden"]
    tokens = sum(s.get("tokens", 0) for s in sessions)
    cost = sum(s.get("cost_usd", 0) for s in sessions)
    estimated = not cost and price is not None
    return {
        "tickets": len(sessions),
        "minutes": sum(s.get("wall_s", 0) for s in sessions) / 60,
        "tokens": tokens,
        "cost": tokens / 1e6 * price if estimated else cost,
        "estimated": estimated,
        "hidden": (len(last["passed"]), len(last["passed"]) + len(last["failed"])),
        "rules": {label: (name in last["passed"]) for name, label in RULES.items()
                  if name in last["passed"] or name in last["failed"]},
        "aborted": any(s.get("aborted") for s in sessions),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="/tmp/horizon-demo")
    ap.add_argument("--price", type=float, help="USD per million tokens, to estimate cost where none is reported.")
    args = ap.parse_args(argv)
    out = Path(args.out)
    runs = {side: load(out, side) for side in SIDES}
    if not any(runs.values()):
        print(f"No results in {out} yet.")
        return 1
    n = min(len(r) for r in runs.values() if r)  # compare on the tickets both sides finished
    if all(runs.values()) and len({len(r) for r in runs.values()}) > 1:
        print(f"(Comparing the first {n} tickets, which both sides finished.)")
    s = {side: summary(r[:n], args.price) for side, r in runs.items() if r}
    rows = [
        ("Tickets done", lambda x: str(x["tickets"]) + (" (cut short)" if x["aborted"] else "")),
        ("Time", lambda x: f"{x['minutes']:.1f} min"),
        ("Tokens", lambda x: f"{x['tokens'] / 1e6:.2f} M"),
        ("Cost", lambda x: (f"${x['cost']:.2f}" + (" (estimated)" if x["estimated"] else "")) if x["cost"] else "not reported (use --price)"),
        ("Hidden tests passed", lambda x: f"{x['hidden'][0]}/{x['hidden'][1]}"),
        ("Rules kept", lambda x: f"{sum(x['rules'].values())}/{len(x['rules'])}"),
    ]
    width = 22
    print(f"\n{'':<{width}}" + "".join(f"{'Without Horizon' if k == 'without' else 'With Horizon':>20}" for k in s))
    for label, fmt in rows:
        print(f"{label:<{width}}" + "".join(f"{fmt(v):>20}" for v in s.values()))
    if len(s) == 2:
        w, h = s["without"], s["with"]
        if h["minutes"] and w["tokens"]:
            saved = 100 * (1 - h["tokens"] / w["tokens"])
            print(f"\nWith Horizon: {w['minutes'] / h['minutes']:.2f}x the speed, "
                  f"{abs(saved):.0f} % {'fewer' if saved >= 0 else 'more'} tokens")
    labels = sorted({lab for v in s.values() for lab in v["rules"]}, key=list(RULES.values()).index)
    if labels:
        print(f"\n{'Rule (given early, needed later)':<36}" + "".join(f"{('Without' if k == 'without' else 'With'):>10}" for k in s))
        for lab in labels:
            print(f"{lab:<36}" + "".join(f"{('yes' if v['rules'].get(lab) else ('no' if lab in v['rules'] else '-')):>10}"
                                         for v in s.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
