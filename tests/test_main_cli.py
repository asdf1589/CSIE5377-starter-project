from crawler.main import parse_args


def test_new_flags_default_to_spec_values():
    args = parse_args([])
    assert args.max_runtime_hours is None
    assert args.checkpoint_interval == 300
    assert args.checkpoint_path == "output/checkpoint.json"
    assert args.resume is None
    assert args.follow_links is False
    assert args.max_pages_per_domain == 20


def test_new_flags_parse_when_provided():
    args = parse_args([
        "--max-runtime-hours", "48",
        "--checkpoint-interval", "10",
        "--checkpoint-path", "ckpt.json",
        "--resume", "ckpt.json",
        "--follow-links",
        "--max-pages-per-domain", "5",
    ])
    assert args.max_runtime_hours == 48.0
    assert args.checkpoint_interval == 10.0
    assert args.checkpoint_path == "ckpt.json"
    assert args.resume == "ckpt.json"
    assert args.follow_links is True
    assert args.max_pages_per_domain == 5
