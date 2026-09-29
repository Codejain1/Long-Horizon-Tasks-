import sys

from ledger import load, total

if len(sys.argv) == 3 and sys.argv[1] == "report":
    lg = load(sys.argv[2])
    for c in sorted({e["category"] for e in lg["expenses"]} | set(lg["budgets"])):
        print(c, total(lg, c), lg["budgets"].get(c))
