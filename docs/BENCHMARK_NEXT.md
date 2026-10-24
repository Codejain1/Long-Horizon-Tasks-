# Epic 3: next benchmark run (lab)

Run on the Hermes server when the GPU tunnel and model are up. Do not deploy over a running backlog folder.

```bash
cd /home/hermes/epic3   # or /opt/longrun checkout + venv
MODEL=http://127.0.0.1:8000/v1
OUT=/home/hermes/epic3-long
.venv/bin/python bench/preflight.py --model-url "$MODEL" --scenario rental
.venv/bin/python bench/tickets.py run --scenario rental --tickets 1-3 --arms plain,runner,mem \
  --reps 2 --streams 1 --out "$OUT" --model-url "$MODEL" --max-minutes 20
.venv/bin/python bench/tickets.py report --out "$OUT"
```

Use `--streams 1` on a single A10G so jobs don't pile up. Record solved rate and Wilson interval vs plain in `PROGRESS.md`. Ward tickets stay held-out for milestone claims.
