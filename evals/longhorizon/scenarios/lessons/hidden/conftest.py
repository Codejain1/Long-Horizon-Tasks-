import multiprocessing as mp


def run_workers(target, args_list, timeout=120):
    """Run `target(*args, out)` in spawned processes; return what each put on `out` (a crashed worker gives [])."""
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    procs = [ctx.Process(target=target, args=(*a, out)) for a in args_list]
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
