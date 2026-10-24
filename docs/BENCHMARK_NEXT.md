# Epic 3: next benchmark run (lab)

Run on the Hermes server when the GPU tunnel and model are up. Do not deploy over a running backlog folder.

```bash
cd /opt/longrun   # or your checkout of Long-Horizon-Tasks-v2
.venv/bin/python bench/preflight.py --model-url http://127.0.0.1:8000/v1 ...
.venv/bin/python bench/tickets.py run --scenario rental --ticket 1 --arms plain,runner,mem --reps 2 --model-url ...
.venv/bin/python bench/tickets.py report --out DIR
```

Record solved rate and Wilson interval vs plain in `PROGRESS.md`. Ward tickets stay held-out for milestone claims.
