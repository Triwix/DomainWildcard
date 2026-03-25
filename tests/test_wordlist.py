from app.wordlist import parse_wordlist, parse_wordlist_bytes


def test_parse_wordlist_normalizes_and_deduplicates():
    raw = """
    # comment
    Alpha
    beta
    alpha

    gamma
    """
    assert parse_wordlist(raw) == ["alpha", "beta", "gamma"]


def test_parse_wordlist_bytes_with_latin1_fallback():
    raw = "caf\xe9\nCafe\n".encode("latin-1")
    assert parse_wordlist_bytes(raw) == ["café", "cafe"]
