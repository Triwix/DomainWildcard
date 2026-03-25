from scripts.domain_value_ranker import (
    DomainRecord,
    _select_shortlist,
    load_trademark_terms,
    rank_domains,
    scan_domain_files,
)


def test_scan_domain_files_counts_all_lines_and_duplicates(tmp_path):
    first = tmp_path / "first.txt"
    first.write_text("alpha.com\nbeta.com\ninvalid domain\n\nalpha.com\n", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("gamma.com\nBETA.com\n#comment\n", encoding="utf-8")

    records, stats = scan_domain_files([first, second])

    assert set(records.keys()) == {"alpha.com", "beta.com", "gamma.com"}
    assert records["alpha.com"].occurrences == 2
    assert records["alpha.com"].source_file_count == 1
    assert records["beta.com"].occurrences == 2
    assert records["beta.com"].source_file_count == 2

    total_lines = sum(item.total_lines for item in stats)
    blank_lines = sum(item.blank_lines for item in stats)
    invalid_lines = sum(item.invalid_lines for item in stats)
    valid_lines = sum(item.valid_lines for item in stats)
    duplicate_valid_lines = sum(item.duplicate_valid_lines for item in stats)

    assert total_lines == 8
    assert blank_lines == 1
    assert invalid_lines == 2
    assert valid_lines == 5
    assert duplicate_valid_lines == 2
    assert total_lines == blank_lines + invalid_lines + valid_lines


def test_load_trademark_terms_ignores_comments_and_short_terms(tmp_path):
    path = tmp_path / "marks.txt"
    path.write_text("# brands\nApple\nAI\nMeta\n\n", encoding="utf-8")

    terms = load_trademark_terms(path)
    assert "apple" in terms
    assert "meta" in terms
    assert "ai" not in terms


def test_rank_domains_applies_trademark_penalty():
    records = [
        DomainRecord(domain="applecloud.com", occurrences=1, source_files={"a.txt"}, first_seen_file="a.txt", first_seen_line=1),
        DomainRecord(domain="cloudforge.com", occurrences=1, source_files={"a.txt"}, first_seen_file="a.txt", first_seen_line=2),
    ]
    ranked = rank_domains(records, trademark_terms={"apple"})
    by_domain = {item.domain: item for item in ranked}

    assert by_domain["applecloud.com"].trademark_risk == "high"
    assert by_domain["applecloud.com"].score < by_domain["cloudforge.com"].score


def test_select_shortlist_respects_trademark_filter():
    records = [
        DomainRecord(domain="applecloud.com", occurrences=1, source_files={"a.txt"}, first_seen_file="a.txt", first_seen_line=1),
        DomainRecord(domain="cloudforge.com", occurrences=1, source_files={"a.txt"}, first_seen_file="a.txt", first_seen_line=2),
        DomainRecord(domain="metapilot.com", occurrences=1, source_files={"a.txt"}, first_seen_file="a.txt", first_seen_line=3),
    ]
    ranked = rank_domains(records, trademark_terms={"apple", "meta"})

    strict_shortlist = _select_shortlist(
        ranked,
        top=10,
        min_score=0.0,
        trademark_filter_enabled=True,
        allow_medium_trademark_risk=False,
    )
    strict_domains = {item.domain for item in strict_shortlist}
    assert "applecloud.com" not in strict_domains
    assert "cloudforge.com" in strict_domains

    no_filter_shortlist = _select_shortlist(
        ranked,
        top=10,
        min_score=0.0,
        trademark_filter_enabled=False,
        allow_medium_trademark_risk=False,
    )
    no_filter_domains = {item.domain for item in no_filter_shortlist}
    assert "applecloud.com" in no_filter_domains
