from crawler.linkextract import extract_links


def test_extracts_absolute_and_relative_links():
    html = '<a href="https://other.example/x">x</a><a href="/relative">rel</a>'
    links = extract_links("https://a.example/page", html)
    assert links == ["https://other.example/x", "https://a.example/relative"]


def test_ignores_non_anchor_tags_and_empty_href():
    html = '<link href="/style.css"><a href="">empty</a><a>no href</a>'
    links = extract_links("https://a.example/", html)
    assert links == []


def test_tolerates_malformed_html_without_raising():
    html = '<a href="/ok">ok</a><div><a href="/also-ok"'
    links = extract_links("https://a.example/", html)
    assert "https://a.example/ok" in links
