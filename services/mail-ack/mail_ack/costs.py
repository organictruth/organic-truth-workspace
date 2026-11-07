"""List-price model for the supported deployment. Not a billing quote.

Rates were read from Google Cloud pricing pages on 2026-11-07.
Free tiers are per billing account (or per project, for Logging) and are
shared with other workloads. This module does not call a billing API.
"""

from __future__ import annotations

from dataclasses import dataclass

RETRIEVED = "2026-11-07"
SOURCES = {
    "cloud_run": "https://cloud.google.com/run/pricing",
    "pubsub": "https://cloud.google.com/pubsub/pricing",
    "firestore": "https://cloud.google.com/firestore/pricing",
    "scheduler": "https://cloud.google.com/scheduler/pricing",
    "secret_manager": "https://cloud.google.com/secret-manager/pricing",
    "artifact_registry": "https://cloud.google.com/artifact-registry/pricing",
    "cloud_build": "https://cloud.google.com/build/pricing",
    "logging": "https://cloud.google.com/products/observability/pricing",
}

# us-central1 request-based Cloud Run, beyond the free tier.
RUN_VCPU_SECOND = 0.000024
RUN_GIB_SECOND = 0.0000025
RUN_PER_MILLION_REQUESTS = 0.40
RUN_FREE_VCPU_SECONDS = 180_000
RUN_FREE_GIB_SECONDS = 360_000
RUN_FREE_REQUESTS = 2_000_000

# Message Delivery Basic. First 10 GiB / billing account / month, then $40/TiB.
PUBSUB_FREE_GIB = 10
PUBSUB_USD_PER_TIB = 40

# Firestore Standard edition, Iowa (us-central1), beyond the daily free quota.
FIRESTORE_READ_PER_100K = 0.03
FIRESTORE_WRITE_PER_100K = 0.09
FIRESTORE_FREE_READS_PER_DAY = 50_000
FIRESTORE_FREE_WRITES_PER_DAY = 20_000
FIRESTORE_FREE_STORAGE_GIB = 1
# $0.000205479 per GiB-hour on the 2026-11-07 table; about $0.15 per GiB-month.
FIRESTORE_STORAGE_PER_GIB_HOUR = 0.000205479

SCHEDULER_FREE_JOBS = 3
SCHEDULER_USD_PER_JOB_MONTH = 0.10

SECRET_FREE_VERSIONS = 6
SECRET_USD_PER_VERSION_HOUR = 0.000082192
SECRET_FREE_ACCESS_OPS = 10_000

ARTIFACT_FREE_GIB = 0.5
ARTIFACT_USD_PER_GIB_HOUR = 0.000136986

BUILD_FREE_MINUTES = 2_500
BUILD_USD_PER_MINUTE = 0.006

LOGGING_FREE_GIB = 50
LOGGING_USD_PER_GIB = 0.50

# North America internet egress free tier stated on the Cloud Run pricing page.
# Whether Gmail API responses are metered as internet egress is not stated there.
EGRESS_FREE_GIB_NORTH_AMERICA = 1


@dataclass(frozen=True)
class Scenario:
    name: str
    pushes_per_month: int
    maintenance_per_month: int
    active_seconds_per_request: float
    vcpu: float
    memory_gib: float
    pubsub_kib_per_message: int
    pubsub_deliveries: int
    firestore_reads_per_message: int
    firestore_writes_per_message: int
    firestore_storage_gib: float
    secret_versions: int
    secret_access_ops: int
    image_gib: float
    build_minutes: float
    log_gib: float
    egress_gib: float
    scheduler_jobs: int
    hours_per_month: float = 730


def low_volume() -> Scenario:
    """One quiet mailbox. Assumptions are stated beside the runbook table."""
    return Scenario(
        name="low_volume_mailbox",
        pushes_per_month=1_500,
        maintenance_per_month=30,
        active_seconds_per_request=1.0,
        vcpu=1,
        memory_gib=0.5,
        pubsub_kib_per_message=1,
        pubsub_deliveries=1,
        firestore_reads_per_message=8,
        firestore_writes_per_message=6,
        firestore_storage_gib=0.01,
        secret_versions=2,
        secret_access_ops=2_000,
        image_gib=0.2,
        build_minutes=5,
        log_gib=0.1,
        egress_gib=0.15,
        scheduler_jobs=1,
    )


def list_price(scenario: Scenario, *, free_tiers_remaining: bool) -> dict[str, float]:
    requests = scenario.pushes_per_month + scenario.maintenance_per_month
    vcpu_seconds = requests * scenario.active_seconds_per_request * scenario.vcpu
    gib_seconds = requests * scenario.active_seconds_per_request * scenario.memory_gib
    pubsub_gib = (
        scenario.pushes_per_month
        * scenario.pubsub_kib_per_message
        * (1 + scenario.pubsub_deliveries)
        / (1024 * 1024)
    )
    reads = scenario.pushes_per_month * scenario.firestore_reads_per_message
    writes = scenario.pushes_per_month * scenario.firestore_writes_per_message
    raw = {
        "cloud_run": vcpu_seconds * RUN_VCPU_SECOND
        + gib_seconds * RUN_GIB_SECOND
        + requests / 1_000_000 * RUN_PER_MILLION_REQUESTS,
        "pubsub": pubsub_gib / 1024 * PUBSUB_USD_PER_TIB,
        "firestore_ops": reads / 100_000 * FIRESTORE_READ_PER_100K
        + writes / 100_000 * FIRESTORE_WRITE_PER_100K,
        "firestore_storage": scenario.firestore_storage_gib
        * FIRESTORE_STORAGE_PER_GIB_HOUR
        * scenario.hours_per_month,
        "scheduler": scenario.scheduler_jobs * SCHEDULER_USD_PER_JOB_MONTH,
        "secret_manager": scenario.secret_versions * SECRET_USD_PER_VERSION_HOUR * scenario.hours_per_month,
        "artifact_registry": scenario.image_gib * ARTIFACT_USD_PER_GIB_HOUR * scenario.hours_per_month,
        "cloud_build": scenario.build_minutes * BUILD_USD_PER_MINUTE,
        "logging": scenario.log_gib * LOGGING_USD_PER_GIB,
    }
    if free_tiers_remaining and _inside_free_tiers(scenario, vcpu_seconds, gib_seconds, requests, pubsub_gib, reads, writes):
        return {key: 0.0 for key in raw}
    return {key: round(value, 6) for key, value in raw.items()}


def _inside_free_tiers(scenario, vcpu_seconds, gib_seconds, requests, pubsub_gib, reads, writes) -> bool:
    days = 31
    return (
        vcpu_seconds <= RUN_FREE_VCPU_SECONDS
        and gib_seconds <= RUN_FREE_GIB_SECONDS
        and requests <= RUN_FREE_REQUESTS
        and pubsub_gib <= PUBSUB_FREE_GIB
        and reads <= FIRESTORE_FREE_READS_PER_DAY * days
        and writes <= FIRESTORE_FREE_WRITES_PER_DAY * days
        and scenario.firestore_storage_gib <= FIRESTORE_FREE_STORAGE_GIB
        and scenario.scheduler_jobs <= SCHEDULER_FREE_JOBS
        and scenario.secret_versions <= SECRET_FREE_VERSIONS
        and scenario.secret_access_ops <= SECRET_FREE_ACCESS_OPS
        and scenario.image_gib <= ARTIFACT_FREE_GIB
        and scenario.build_minutes <= BUILD_FREE_MINUTES
        and scenario.log_gib <= LOGGING_FREE_GIB
        and scenario.egress_gib <= EGRESS_FREE_GIB_NORTH_AMERICA
    )
