from grabon_intel.resolver import normalize_domain


def test_normalize_strips_scheme_and_www() -> None:
    assert normalize_domain("https://www.Mamaearth.in/products") == "mamaearth.in"
    assert normalize_domain("http://boat-lifestyle.com") == "boat-lifestyle.com"
    assert normalize_domain("WWW.Nykaa.COM") == "nykaa.com"


def test_normalize_bare_domain() -> None:
    assert normalize_domain("mamaearth.in") == "mamaearth.in"


def test_normalize_empty() -> None:
    assert normalize_domain("") is None
    assert normalize_domain(None) is None
    assert normalize_domain("just a name no domain") is None
