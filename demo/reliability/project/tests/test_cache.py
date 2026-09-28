from textkit.cache import WordCountCache


def test_counts_are_cached(tmp_path):
    cache = WordCountCache(tmp_path / "wc")
    assert cache.get("hello world") is None
    cache.put("hello world", 2)
    assert cache.get("hello world") == 2


def test_cache_survives_a_restart(tmp_path):
    WordCountCache(tmp_path / "wc").put("a b c", 3)
    assert WordCountCache(tmp_path / "wc").get("a b c") == 3


def test_many_entries(tmp_path):
    cache = WordCountCache(tmp_path / "wc")
    for i in range(200):
        cache.put(f"text {i}", i)
    assert WordCountCache(tmp_path / "wc").get("text 150") == 150
