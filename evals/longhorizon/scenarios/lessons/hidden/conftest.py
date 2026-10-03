import multiprocessing as mp


def run_workers(target, args_list, timeout=120):
    """Run `target(*args, start, out)` in spawned processes; return what each put on `out` (a crashed worker gives
    nothing). `start` is a barrier: workers call `start.wait()` once ready, so they really run at the same time
    (without it, slow start-ups ran them one after another and an unsafe store passed by luck)."""
    ctx = mp.get_context("spawn")
    out, start = ctx.Queue(), ctx.Barrier(len(args_list))
    procs = [ctx.Process(target=target, args=(*a, start, out)) for a in args_list]
    for p in procs:
        p.start()
    results = []
    for _ in procs:
        try:
            results.append(out.get(timeout=timeout))
        except Exception:
            break
    for p in procs:
        p.join(10)
        if p.is_alive():
            p.terminate()
    return results
