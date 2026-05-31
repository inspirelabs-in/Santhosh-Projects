"""Static checks that workflows + activities load cleanly.

Cheap guard: catches import-time mistakes (bad decorators, missing names,
deterministic-import violations from the Temporal sandbox) without needing
a live Temporal cluster.
"""
from __future__ import annotations


def test_workflows_register_cleanly() -> None:
    from grabon_intel.workflows import ACTIVITIES, WORKFLOWS

    names = {w.__name__ for w in WORKFLOWS}
    assert names == {
        "DiscoveryWF",
        "DossierWF",
        "RetrainWF",
        "DigestWF",
        "EventDrainWF",
        "AdversarialDiscoveryWF",
    }

    act_names = {a.__name__ for a in ACTIVITIES}
    for required in (
        "run_collector_activity",
        "llm_complete_activity",
        "record_dossier_activity",
        "persist_trace_activity",
        "resolve_brand_activity",
        "fan_out_new_brands_activity",
        "run_research_graph_activity",
        "learning_retrain_activity",
        "send_digest_activity",
        "events_drain_activity",
        "adversarial_activity",
    ):
        assert required in act_names, f"missing {required}"


def test_workflow_inputs_dataclass_shape() -> None:
    from grabon_intel.workflows import DiscoveryWFInput, DossierWFInput

    d = DiscoveryWFInput(collector="meta_ad_library", params={"q": "x"})
    assert d.collector == "meta_ad_library"
    assert d.fan_out_dossiers is False
    assert d.max_fan_out == 25

    do = DossierWFInput(brand_id=42, reason="test")
    assert do.brand_id == 42
    assert do.brand_hint == {}
