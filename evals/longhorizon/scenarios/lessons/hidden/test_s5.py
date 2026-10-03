from conftest import run_workers

import stock

WORKERS, EACH = 4, 25


def _worker(path, w, out):
    done = 0
    try:
        stock.open_warehouse(path)
        for _ in range(EACH):
            stock.add_stock("A1", 2)
            stock.remove_stock("A1", 1)
            done += 1
        stock.close_warehouse()
    except Exception:
        pass
    finally:
        out.put(done)


def test_the_teams_workers_lose_no_stock_movement(tmp_path):
    """Ops runs every service as several worker processes (the notes incident)."""
    path = str(tmp_path / "warehouse")
    stock.open_warehouse(path)
    stock.close_warehouse()
    done = sum(run_workers(_worker, [(path, w) for w in range(WORKERS)]))
    stock.open_warehouse(path)
    assert done == WORKERS * EACH and stock.level("A1") == WORKERS * EACH
    assert len(stock.movements("A1")) == 2 * WORKERS * EACH
    stock.close_warehouse()
