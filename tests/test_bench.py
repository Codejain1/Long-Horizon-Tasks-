"""Phase 1 benchmark harness: selection, models, runner, evaluation and reports (all dry run)."""

import json
import socket
import subprocess
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("minisweagent")

from horizon.bench import evaluate as ev  # noqa: E402
from horizon.bench import runner  # noqa: E402
from horizon.bench.config import Prices, load_config  # noqa: E402
from horizon.bench.models import MockModel, Usage, make_priced_litellm_model, usage_from_litellm  # noqa: E402
from horizon.bench.report import build_report, to_markdown  # noqa: E402
from horizon.bench.select import BUCKETS, largest_remainder, select_tasks, synthetic_verified_like  # noqa: E402

WEIGHTS = {"1-4 hours": 0.40, "15 min - 1 hour": 0.35, "<15 min fix": 0.25}
PRICES = Prices(input=2.0, output=10.0, cache_write=2.5, cache_read=0.2)


def select(instances=None, seed=7, n=50, smoke=10):
    return select_tasks(instances or synthetic_verified_like(), seed=seed, n_tasks=n, smoke_n=smoke,
                        take_all=[">4 hours"], weights=WEIGHTS)


# --- selection -------------------------------------------------------------------------------

def test_largest_remainder_sums_and_respects_caps():
    assert largest_remainder(WEIGHTS, 47) == {"1-4 hours": 19, "15 min - 1 hour": 16, "<15 min fix": 12}
    capped = largest_remainder(WEIGHTS, 47, {"1-4 hours": 5, "15 min - 1 hour": 100, "<15 min fix": 100})
    assert capped["1-4 hours"] == 5 and sum(capped.values()) == 47
    assert sum(largest_remainder({"a": 1, "b": 1}, 10, {"a": 2, "b": 3}).values()) == 5  # not enough items


def test_selection_follows_the_approved_weights():
    tasks = select()
    assert len(tasks) == 50 and len({t["instance_id"] for t in tasks}) == 50
    counts = Counter(t["difficulty"] for t in tasks)
    assert counts == {">4 hours": 3, "1-4 hours": 19, "15 min - 1 hour": 16, "<15 min fix": 12}


def test_selection_is_deterministic_and_seed_dependent():
    assert select(seed=7) == select(seed=7)
    assert select(seed=7) != select(seed=8)
    shuffled = list(reversed(synthetic_verified_like()))
    assert select(shuffled, seed=7) == select(seed=7)  # input order doesn't matter


def test_selection_is_stratified_across_repos():
    pool = synthetic_verified_like()
    tasks = select()
    for bucket in ("1-4 hours", "15 min - 1 hour", "<15 min fix"):
        in_bucket = [i for i in pool if i["difficulty"] == bucket]
        chosen = [t for t in tasks if t["difficulty"] == bucket]
        share = Counter(i["repo"] for i in in_bucket)
        got = Counter(t["repo"] for t in chosen)
        for repo, n in got.items():
            expected = len(chosen) * share[repo] / len(in_bucket)
            assert abs(n - expected) <= 1, (bucket, repo, n, expected)


def test_smoke_run_is_the_first_ten_and_stratified():
    tasks = select()
    assert [t["smoke"] for t in tasks] == [True] * 10 + [False] * 40
    smoke = Counter(t["difficulty"] for t in tasks[:10])
    assert smoke[">4 hours"] == 3 and sum(smoke.values()) == 10
    assert smoke["1-4 hours"] >= smoke["<15 min fix"]


def test_selection_redistributes_when_a_bucket_is_short():
    pool = [i for i in synthetic_verified_like() if i["difficulty"] != "1-4 hours"][:200]
    tasks = select(pool)
    assert len(tasks) == 50 and not any(t["difficulty"] == "1-4 hours" for t in tasks)


def test_unknown_difficulty_is_rejected():
    with pytest.raises(ValueError):
        select([{"instance_id": "x", "repo": "a/b", "difficulty": "weird"}])
    assert set(BUCKETS) == {i["difficulty"] for i in synthetic_verified_like()}


# --- models ----------------------------------------------------------------------------------

def test_usage_cost_and_litellm_normalisation():
    u = usage_from_litellm({"prompt_tokens": 10_000, "completion_tokens": 500, "cache_read_input_tokens": 6_000,
                            "cache_creation_input_tokens": 1_000})
    assert u == Usage(input=3_000, output=500, cache_write=1_000, cache_read=6_000)
    assert u.cost(PRICES) == pytest.approx((3_000 * 2 + 500 * 10 + 1_000 * 2.5 + 6_000 * 0.2) / 1e6)
    assert usage_from_litellm(None) == Usage()
    assert usage_from_litellm({"prompt_tokens": 5, "prompt_tokens_details": {"cached_tokens": 3}}).cache_read == 3


def test_priced_litellm_model_uses_config_prices_without_calling_the_api():
    import litellm

    model = make_priced_litellm_model("anthropic/claude-sonnet-5", PRICES, {"model_kwargs": {"drop_params": True}})
    assert model.config.model_kwargs == {"drop_params": True}
    response = litellm.ModelResponse(usage={"prompt_tokens": 2_000_000, "completion_tokens": 100_000,
                                            "total_tokens": 2_100_000})
    out = model._calculate_cost(response)
    assert out["cost"] == pytest.approx(2 * 2.0 + 0.1 * 10.0)
    assert out["usage"]["input"] == 2_000_000


def test_mock_model_is_deterministic():
    a, b = MockModel(PRICES, "solve"), MockModel(PRICES, "solve")
    assert [a.query([])["extra"]["actions"] for _ in range(4)] == [b.query([])["extra"]["actions"] for _ in range(4)]


# --- runner ----------------------------------------------------------------------------------

@pytest.fixture
def no_network(monkeypatch):
    """Dry runs must make no network calls from this process (model, evaluator, dataset)."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(f"network call during dry run: {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    assert c.real_runs is False, "the committed config must stay dry-run"
    return c.model_copy(update={"selection": c.selection.model_copy(update={"task_list": str(tmp_path / "none.json")})})


def test_dry_run_smoke_end_to_end(cfg, tmp_path, no_network):
    [run_dir] = runner.run(cfg, "smoke", repeats=1, out_root=tmp_path)
    results = runner.read_results(run_dir)
    tasks, _ = runner.load_task_list(cfg)
    assert [r["instance_id"] for r in results] == [t["instance_id"] for t in tasks[:10]]
    for r in results:
        assert r["schema_version"] == 1 and r["dry_run"] is True and r["environment"] == "stub"
        assert r["exit_status"] in {"Submitted", "LimitsExceeded"} and r["error"] is None
        assert r["steps"] <= cfg.limits.step_limit and r["tokens"]["total"] > 0 and r["wall_time_s"] >= 0
        # The cap is checked before each call, so a task can overrun by at most one call.
        assert r["cost_usd"] < cfg.limits.cost_limit_usd + 0.1
    preds = json.loads((run_dir / "preds.json").read_text())
    submitted = [r for r in results if r["submitted"]]
    assert submitted and all("dry-run fix" in preds[r["instance_id"]]["model_patch"] for r in submitted)
    assert (run_dir / "trajectories" / f"{results[0]['instance_id']}.traj.json").exists()
    assert not (run_dir / "work").exists()
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["task_list"] == "synthetic" and meta["config"]["real_runs"] is False


def test_step_cap(cfg, tmp_path, no_network):
    tight = cfg.model_copy(update={"limits": cfg.limits.model_copy(update={"step_limit": 3, "cost_limit_usd": 100})})
    [run_dir] = runner.run(tight, "smoke", out_root=tmp_path, limit=10)
    results = runner.read_results(run_dir)
    assert all(r["steps"] <= 3 for r in results)
    assert all(r["exit_status"] == "LimitsExceeded" for r in results)  # the mock needs 4 steps to submit


def test_cost_cap(cfg, tmp_path, no_network):
    cheap = cfg.model_copy(update={"limits": cfg.limits.model_copy(update={"cost_limit_usd": 0.03})})
    [run_dir] = runner.run(cheap, "smoke", out_root=tmp_path, limit=3)
    for r in runner.read_results(run_dir):
        assert r["exit_status"] == "LimitsExceeded" and r["steps"] < 4


def test_uses_committed_task_list_when_present(cfg, tmp_path, no_network):
    tasks = select()[:2]
    path = tmp_path / "list.json"
    path.write_text(json.dumps({"tasks": tasks}))
    c = cfg.model_copy(update={"selection": cfg.selection.model_copy(update={"task_list": str(path), "smoke_n": 2})})
    [run_dir] = runner.run(c, "smoke", out_root=tmp_path / "runs")
    assert [r["instance_id"] for r in runner.read_results(run_dir)] == [t["instance_id"] for t in tasks]


def test_real_runs_need_credentials_and_a_task_list(cfg, tmp_path, monkeypatch):
    real = cfg.model_copy(update={"real_runs": True})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SWEBENCH_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        runner.run(real, "smoke", out_root=tmp_path)
    with pytest.raises(SystemExit, match="task list"):
        runner.load_task_list(real)


def test_environment_resolution(cfg, monkeypatch):
    assert runner.resolve_environment(cfg) == "stub"
    real = cfg.model_copy(update={"real_runs": True})
    monkeypatch.setattr(runner, "docker_available", lambda: True)
    assert runner.resolve_environment(real) == "docker"
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    assert runner.resolve_environment(real) == "local"


def test_local_checkout_fallback(tmp_path, monkeypatch):
    origin = tmp_path / "origin" / "acme" / "widgets.git"
    origin.mkdir(parents=True)
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=origin, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "later"],
                   cwd=origin, check=True)
    monkeypatch.setenv("HORIZON_BENCH_GIT_BASE", str(tmp_path / "origin"))
    repo, log = runner.prepare_local_checkout({"instance_id": "acme__widgets-1", "repo": "acme/widgets",
                                               "base_commit": base}, tmp_path / "work", install=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert head == base and log["cloned"] is True


# --- evaluation and reports ------------------------------------------------------------------

def test_dry_run_evaluation_and_report(cfg, tmp_path, no_network):
    dirs = runner.run(cfg, "smoke", repeats=2, out_root=tmp_path)
    for d in dirs:
        summary = ev.evaluate(cfg, d)
        assert summary["evaluator"] == "mock (dry run)"
        assert all(r["resolved"] is not None for r in runner.read_results(d))
    report = build_report(dirs)
    assert report["dry_run"] is True and report["across_repeats"]["n_runs"] == 2
    assert report["across_repeats"]["success_rate_stdev"] is not None
    run = next(iter(report["runs"].values()))
    assert run["n_tasks"] == 10 and 0 <= run["success_rate"] <= run["submitted_rate"]
    assert "DRY RUN" in to_markdown(report)
    task = next(iter(report["tasks"].values()))
    assert task["runs"] == 2


def test_real_evaluation_calls_sb_cli(cfg, tmp_path, monkeypatch, no_network):
    [d] = runner.run(cfg, "smoke", out_root=tmp_path, limit=2)
    meta = json.loads((d / "meta.json").read_text())
    meta["dry_run"] = False
    (d / "meta.json").write_text(json.dumps(meta))
    ids = [r["instance_id"] for r in runner.read_results(d)]
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--output_dir") + 1])
        out.mkdir(parents=True)
        (out / "report.json").write_text(json.dumps({"resolved_ids": ids[:1]}))

    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("SWEBENCH_API_KEY", "x")
    real = cfg.model_copy(update={"real_runs": True})
    summary = ev.evaluate(real, d)
    assert calls[0][:4] == ["sb-cli", "submit", "swe-bench_verified", "test"]
    assert summary["resolved"] == ids[:1]
    with pytest.raises(SystemExit, match="dry"):
        ev.evaluate(cfg, d)  # a real run can't be evaluated with the dry-run config


def test_cli_dry_run(tmp_path, monkeypatch, no_network):
    from horizon.bench.cli import main

    out = tmp_path / "list.json"
    assert main(["select", "--source", "synthetic", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert len(data["tasks"]) == 50 and data["dataset"].startswith("synthetic")
    assert main(["run", "--stage", "smoke", "--limit", "2", "--out", str(tmp_path / "runs")]) == 0
    assert list((tmp_path / "runs").glob("report-*/report.md"))
