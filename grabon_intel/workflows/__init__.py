"""Temporal workflows + activities for grabon-intel."""
from .activities import (
    create_approval_activity,
    enrich_unresolved_hints_activity,
    fan_out_new_brands_activity,
    fetch_brand_domain_activity,
    fetch_recent_brand_domains_activity,
    fetch_stale_brands_activity,
    llm_complete_activity,
    persist_trace_activity,
    pre_qualify_brands_activity,
    record_dossier_activity,
    resolve_brand_activity,
    run_collector_activity,
    run_research_graph_activity,
)
from .adversarial import AdversarialDiscoveryWF, AdversarialWFResult, adversarial_activity
from .auto_discovery import AutoDiscoveryWF, AutoDiscoveryWFInput, AutoDiscoveryWFResult
from .digest import DigestWF, DigestWFInput, DigestWFResult, send_digest_activity
from .discovery import DiscoveryWF, DiscoveryWFInput, DiscoveryWFResult
from .dossier import DossierWF, DossierWFInput, DossierWFResult
from .events_drain import EventDrainResult, EventDrainWF, events_drain_activity
from .monitoring import MonitoringWF, MonitoringWFInput, MonitoringWFResult
from .outreach_sequence import OutreachSequenceWF, process_due_sequences_activity
from .retrain import RetrainWF, RetrainWFInput, RetrainWFResult, learning_retrain_activity

ACTIVITIES = [
    run_collector_activity,
    resolve_brand_activity,
    fan_out_new_brands_activity,
    fetch_stale_brands_activity,
    fetch_recent_brand_domains_activity,
    enrich_unresolved_hints_activity,
    llm_complete_activity,
    run_research_graph_activity,
    record_dossier_activity,
    persist_trace_activity,
    learning_retrain_activity,
    send_digest_activity,
    events_drain_activity,
    adversarial_activity,
    process_due_sequences_activity,
    pre_qualify_brands_activity,
    fetch_brand_domain_activity,
    create_approval_activity,
]

WORKFLOWS = [
    DiscoveryWF,
    DossierWF,
    RetrainWF,
    DigestWF,
    EventDrainWF,
    AdversarialDiscoveryWF,
    MonitoringWF,
    OutreachSequenceWF,
    AutoDiscoveryWF,
]

__all__ = [
    "ACTIVITIES",
    "WORKFLOWS",
    "AutoDiscoveryWF",
    "AutoDiscoveryWFInput",
    "AutoDiscoveryWFResult",
    "DiscoveryWF",
    "DiscoveryWFInput",
    "DiscoveryWFResult",
    "DossierWF",
    "DossierWFInput",
    "DossierWFResult",
    "RetrainWF",
    "RetrainWFInput",
    "RetrainWFResult",
    "DigestWF",
    "DigestWFInput",
    "DigestWFResult",
    "EventDrainWF",
    "EventDrainResult",
    "AdversarialDiscoveryWF",
    "AdversarialWFResult",
    "MonitoringWF",
    "MonitoringWFInput",
    "MonitoringWFResult",
    "OutreachSequenceWF",
]
