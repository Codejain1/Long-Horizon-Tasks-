"""Choose the fixed benchmark task list (PROJECT.md §18, owner-approved weights).

- Every task in the ``take_all`` buckets (">4 hours") is included.
- The rest is split across the other difficulty buckets by ``weights`` (largest remainder;
  a bucket's unused quota moves to the others).
- Within each bucket, tasks are allocated to repos in proportion to the repo's share
  (stratified), then picked at random with the fixed seed.
- The list is ordered so the first ``smoke_n`` tasks are themselves a stratified sample
  (same rules), i.e. the smoke run is "the first 10 of the 50".
"""

from __future__ import annotations

import random
from collections import defaultdict

# SWE-bench Verified difficulty labels, shortest first.
BUCKETS = ("<15 min fix", "15 min - 1 hour", "1-4 hours", ">4 hours")


def largest_remainder(shares: dict[str, float], total: int, caps: dict[str, int] | None = None) -> dict[str, int]:
    """Split ``total`` integer slots by ``shares``, never exceeding ``caps``; leftovers are redistributed."""
    caps = caps or {k: total for k in shares}
    alloc = {k: 0 for k in shares}
    remaining = min(total, sum(caps[k] for k in shares if shares[k] > 0))
    while remaining > 0:
        open_keys = [k for k in shares if shares[k] > 0 and alloc[k] < caps[k]]
        weight = sum(shares[k] for k in open_keys)
        exact = {k: remaining * shares[k] / weight for k in open_keys}
        step = {k: min(int(exact[k]), caps[k] - alloc[k]) for k in open_keys}
        given = sum(step.values())
        # Hand out the rest by largest fractional part (stable sort: ties go in key order), respecting caps.
        for k in sorted(open_keys, key=lambda k: -(exact[k] - int(exact[k]))):
            if given == remaining:
                break
            if alloc[k] + step[k] < caps[k]:
                step[k] += 1
                given += 1
        for k in open_keys:
            alloc[k] += step[k]
        remaining -= given
    return alloc


def stratified_by_repo(items: list[dict], n: int, rng: random.Random) -> list[dict]:
    """Pick n items, allocating to repos in proportion to their counts."""
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_repo[item["repo"]].append(item)
    repos = sorted(by_repo)
    rng.shuffle(repos)  # seeded tie-breaking between equally sized repos
    quotas = largest_remainder({r: len(by_repo[r]) for r in repos}, n, {r: len(by_repo[r]) for r in repos})
    picked = []
    for repo in repos:
        picked += rng.sample(sorted(by_repo[repo], key=lambda i: i["instance_id"]), quotas[repo])
    return picked


def _stratified(instances: list[dict], n: int, take_all: list[str], weights: dict[str, float],
                rng: random.Random) -> list[dict]:
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for inst in instances:
        by_bucket[inst["difficulty"]].append(inst)
    chosen: list[dict] = []
    for bucket in take_all:
        items = sorted(by_bucket.get(bucket, []), key=lambda i: i["instance_id"])
        chosen += items if len(items) <= n else stratified_by_repo(items, n, rng)
    rest = n - len(chosen)
    caps = {b: len(by_bucket.get(b, [])) for b in weights}
    quotas = largest_remainder(weights, rest, caps)
    for bucket in weights:
        chosen += stratified_by_repo(by_bucket.get(bucket, []), quotas[bucket], rng)
    return chosen


def select_tasks(instances: list[dict], *, seed: int, n_tasks: int, smoke_n: int, take_all: list[str],
                 weights: dict[str, float]) -> list[dict]:
    """Return the ordered task list: [{instance_id, repo, difficulty, smoke}], smoke tasks first."""
    for inst in instances:
        if inst.get("difficulty") not in BUCKETS:
            raise ValueError(f"{inst.get('instance_id')}: unknown difficulty {inst.get('difficulty')!r}")
    pool = sorted(instances, key=lambda i: i["instance_id"])
    rng = random.Random(seed)
    chosen = _stratified(pool, n_tasks, take_all, weights, rng)
    smoke = _stratified(sorted(chosen, key=lambda i: i["instance_id"]), smoke_n, take_all, weights, rng)
    smoke_ids = {s["instance_id"] for s in smoke}
    others = [c for c in sorted(chosen, key=lambda i: i["instance_id"]) if c["instance_id"] not in smoke_ids]
    rng.shuffle(others)
    ordered = sorted(smoke, key=lambda i: i["instance_id"]) + others
    return [{"instance_id": i["instance_id"], "repo": i["repo"], "difficulty": i["difficulty"],
             "smoke": i["instance_id"] in smoke_ids} for i in ordered]


def synthetic_verified_like(seed: int = 0) -> list[dict]:
    """500 fake instances shaped like SWE-bench Verified (bucket sizes and repo mix), for dry runs
    and tests while the real dataset is unreachable. Never used for real runs."""
    buckets = {"<15 min fix": 194, "15 min - 1 hour": 261, "1-4 hours": 42, ">4 hours": 3}
    repos = {"django/django": 231, "sympy/sympy": 75, "sphinx-doc/sphinx": 44, "matplotlib/matplotlib": 34,
             "scikit-learn/scikit-learn": 32, "astropy/astropy": 22, "pydata/xarray": 22, "pytest-dev/pytest": 19,
             "pylint-dev/pylint": 10, "psf/requests": 8, "mwaskom/seaborn": 2, "pallets/flask": 1}
    rng = random.Random(seed)
    repo_pool = [r for r, n in repos.items() for _ in range(n)]
    diff_pool = [d for d, n in buckets.items() for _ in range(n)]
    rng.shuffle(repo_pool)
    rng.shuffle(diff_pool)
    out = []
    for k, (repo, diff) in enumerate(zip(repo_pool, diff_pool)):
        name = repo.split("/")[1]
        out.append({
            "instance_id": f"{repo.replace('/', '__')}-{10000 + k}",
            "repo": repo,
            "difficulty": diff,
            "base_commit": "0" * 40,
            "problem_statement": f"[synthetic dry-run task {k}] Fix a bug in {name}.",
        })
    return out
