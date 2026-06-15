"""Prometheus metrics for DNS Zone Manager."""

from prometheus_client import Counter, Gauge

# Authentication metrics
logins_total = Counter(
    "dns_zone_manager_logins_total",
    "Total number of UI login actions",
    ["auth_type"],  # api_key, azure_ad
)

logouts_total = Counter(
    "dns_zone_manager_logouts_total",
    "Total number of UI logout actions",
)

# Search metrics
zone_searches_total = Counter(
    "dns_zone_manager_zone_searches_total",
    "Total number of zone-specific searches",
    ["zone"],
)

global_searches_total = Counter(
    "dns_zone_manager_global_searches_total",
    "Total number of global searches",
)

# RRSET operation metrics
rrset_adds_total = Counter(
    "dns_zone_manager_rrset_adds_total",
    "Total number of RRset add operations",
    ["zone"],
)

rrset_deletes_total = Counter(
    "dns_zone_manager_rrset_deletes_total",
    "Total number of RRset delete operations",
    ["zone"],
)

rrset_replaces_total = Counter(
    "dns_zone_manager_rrset_replaces_total",
    "Total number of RRset replace operations",
    ["zone"],
)

# DDNS update outcome metrics
ddns_updates_successful = Counter(
    "dns_zone_manager_ddns_updates_successful_total",
    "Total number of successful DDNS updates",
)

ddns_updates_failed = Counter(
    "dns_zone_manager_ddns_updates_failed_total",
    "Total number of failed DDNS updates",
    ["reason"],  # prereq_failed, update_error, dns_error
)

# Zone transfer metrics
zone_transfers_total = Counter(
    "dns_zone_manager_zone_transfers_total",
    "Total number of zone transfers",
    ["method", "zone"],  # method: axfr, ixfr
)

zone_transfers_failed = Counter(
    "dns_zone_manager_zone_transfers_failed_total",
    "Total number of failed zone transfers",
    ["method", "zone"],  # method: axfr, ixfr
)

# NOTIFY metrics
notifies_received_total = Counter(
    "dns_zone_manager_notifies_received_total",
    "Total number of NOTIFY messages received",
    ["transport", "zone"],  # transport: udp, tcp
)

notifies_rejected_total = Counter(
    "dns_zone_manager_notifies_rejected_total",
    "Total number of rejected NOTIFY messages",
    ["transport", "reason"],  # reason: tsig_failed, wrong_opcode, no_question, parse_error
)

# Cache metrics
cache_size_bytes = Gauge(
    "dns_zone_manager_cache_size_bytes",
    "Current cache size in bytes",
)

cache_evictions_total = Counter(
    "dns_zone_manager_cache_evictions_total",
    "Total number of zones evicted from cache due to size limits",
)
