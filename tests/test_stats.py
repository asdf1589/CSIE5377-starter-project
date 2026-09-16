from crawler.stats import Stats


def test_load_dict_restores_all_fields():
    s = Stats()
    s.load_dict({
        "fetched": 10, "ok": 8, "failed": 2,
        "retried": 3, "robots_skipped": 1, "bytes_received": 4096,
    })
    assert s.as_dict() == {
        "fetched": 10, "ok": 8, "failed": 2,
        "retried": 3, "robots_skipped": 1, "bytes_received": 4096,
    }


def test_load_dict_defaults_missing_fields_to_zero():
    s = Stats(fetched=99)
    s.load_dict({})
    assert s.as_dict() == {
        "fetched": 0, "ok": 0, "failed": 0,
        "retried": 0, "robots_skipped": 0, "bytes_received": 0,
    }
