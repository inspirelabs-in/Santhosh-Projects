"""Signal collector framework + concrete sources."""
from .ads_txt import AdsTxtCollector
from .affiliate_program import AffiliateProgramCollector
from .app_store import AppStoreCollector
from .base import Collector, SignalEvent
from .content_blog import ContentBlogCollector
from .cert_transparency import CertTransparencyCollector
from .common_crawl import CommonCrawlCollector
from .email_maturity import EmailMaturityCollector
from .google_ads_transparency import GoogleAdsTransparencyCollector
from .google_trends import GoogleTrendsCollector
from .hn_discovery import HNDiscoveryCollector
from .ios_app_store import IOSAppStoreCollector
from .ipinfo_geo import IPInfoGeoCollector
from .linkedin_jobs import LinkedInJobsCollector
from .meta_ad_library import MetaAdLibraryCollector
from .news_polling import NewsPollingCollector
from .open_food_facts import OpenFoodFactsCollector
from .pagespeed import PageSpeedCollector
from .rdap_whois import RDAPWhoisCollector
from .seo_rank_tracking import SEORankTrackingCollector
from .shodan_internetdb import ShodanInternetDBCollector
from .social_presence import SocialPresenceCollector
from .tech_stack import TechStackCollector
from .urlscan_search import URLScanSearchCollector
from .youtube_social import YouTubeSocialCollector

_REGISTRY: dict[str, type["Collector"]] = {
    "google_ads_transparency": GoogleAdsTransparencyCollector,
    "meta_ad_library": MetaAdLibraryCollector,
    "linkedin_jobs": LinkedInJobsCollector,
    "news_polling": NewsPollingCollector,
    "seo_rank_tracking": SEORankTrackingCollector,
    "youtube_social": YouTubeSocialCollector,
    "app_store": AppStoreCollector,
    "ios_app_store": IOSAppStoreCollector,
    "ads_txt": AdsTxtCollector,
    "email_maturity": EmailMaturityCollector,
    "tech_stack": TechStackCollector,
    "pagespeed": PageSpeedCollector,
    "google_trends": GoogleTrendsCollector,
    "cert_transparency": CertTransparencyCollector,
    "rdap_whois": RDAPWhoisCollector,
    "common_crawl": CommonCrawlCollector,
    "shodan_internetdb": ShodanInternetDBCollector,
    "hn_discovery": HNDiscoveryCollector,
    "open_food_facts": OpenFoodFactsCollector,
    "urlscan_search": URLScanSearchCollector,
    "ipinfo_geo": IPInfoGeoCollector,
    "affiliate_program": AffiliateProgramCollector,
    "social_presence": SocialPresenceCollector,
    "content_blog": ContentBlogCollector,
}


def get_collector(name: str) -> "Collector":
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown collector: {name}. Available: {list(_REGISTRY.keys())}")
    return cls()


__all__ = [
    "Collector",
    "SignalEvent",
    "MetaAdLibraryCollector",
    "LinkedInJobsCollector",
    "NewsPollingCollector",
    "GoogleAdsTransparencyCollector",
    "SEORankTrackingCollector",
    "YouTubeSocialCollector",
    "AppStoreCollector",
    "IOSAppStoreCollector",
    "AdsTxtCollector",
    "EmailMaturityCollector",
    "TechStackCollector",
    "PageSpeedCollector",
    "GoogleTrendsCollector",
    "CertTransparencyCollector",
    "RDAPWhoisCollector",
    "CommonCrawlCollector",
    "ShodanInternetDBCollector",
    "HNDiscoveryCollector",
    "OpenFoodFactsCollector",
    "URLScanSearchCollector",
    "IPInfoGeoCollector",
    "AffiliateProgramCollector",
    "SocialPresenceCollector",
    "ContentBlogCollector",
    "get_collector",
]
