import datetime as dt

from grabon_intel.signals.base import SignalEvent


def test_dedupe_key_stable() -> None:
    ts = dt.datetime(2026, 5, 18, 12, 0, tzinfo=dt.timezone.utc)
    a = SignalEvent(
        type="ad_spend.meta.active",
        source="meta_ad_library",
        observed_at=ts,
        brand_domain="mamaearth.in",
        value_num=5,
    )
    b = SignalEvent(
        type="ad_spend.meta.active",
        source="meta_ad_library",
        observed_at=ts,
        brand_domain="mamaearth.in",
        value_num=5,
    )
    assert a.derive_dedupe_key() == b.derive_dedupe_key()


def test_dedupe_key_changes_on_value() -> None:
    ts = dt.datetime(2026, 5, 18, 12, 0, tzinfo=dt.timezone.utc)
    a = SignalEvent(type="t", source="s", observed_at=ts, brand_domain="x.com", value_num=1)
    b = SignalEvent(type="t", source="s", observed_at=ts, brand_domain="x.com", value_num=2)
    assert a.derive_dedupe_key() != b.derive_dedupe_key()


def test_explicit_dedupe_key_used() -> None:
    ts = dt.datetime(2026, 5, 18, 12, 0, tzinfo=dt.timezone.utc)
    ev = SignalEvent(type="t", source="s", observed_at=ts, dedupe_key="custom-key")
    assert ev.derive_dedupe_key() == "custom-key"
