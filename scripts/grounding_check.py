#!/usr/bin/env python3
"""
SHADOW-SEE — GROUNDING CHECK
Version: 0.1.0

Purpose
-------
Provide a small, repeatable grounding procedure when system behavior,
observations, or terminology become uncertain.

Core rule:
    What do I know?
    What can I measure?
    What can I reproduce?

Design principles
-----------------
- Observation before explanation.
- Unknown is a valid result.
- Never invent missing evidence.
- Preserve provenance.
- Historical terminology may be recognized for compatibility,
  but obsolete instructions must not silently execute.
- When an obsolete term is encountered, immediately provide its
  modern interpretation/action.
- This program performs diagnostics only. It does not diagnose
  medical conditions or physical causes.

The "static shock" concept is implemented as a SOFTWARE INTERRUPT:
a deliberate pause that stops interpretation and returns the operator
to measurable facts.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


VERSION = "0.1.0"
SCHEMA_VERSION = "1.0"

OUTPUT_DIR = Path("data/events/grounding")
DEFAULT_TIMEOUT = 5


# ---------------------------------------------------------------------------
# HISTORICAL COMPATIBILITY LAYER
# ---------------------------------------------------------------------------
#
# These are NOT commands to execute.
#
# They are recognition aliases for terminology that may appear in older
# documentation, notes, prompts, or imported project material.
#
# If encountered, Shadow-See reports:
#
#   HISTORICAL TERM
#       ↓
#   MODERN INTERPRETATION
#       ↓
#   CURRENT SAFE ACTION
#
# This keeps old material understandable without allowing obsolete
# instructions to become executable behavior.
#

HISTORICAL_TERMS = {
    "ping": {
        "era": "pre-2000/common legacy terminology",
        "modern": "network reachability / round-trip latency test",
        "action": "Run the controlled network reachability test.",
    },
    "traceroute": {
        "era": "pre-2000/common legacy terminology",
        "modern": "path inspection / route observation",
        "action": "Inspect network path only when explicitly requested.",
    },
    "tracert": {
        "era": "pre-2000/common legacy terminology",
        "modern": "path inspection / route observation",
        "action": "Inspect network path only when explicitly requested.",
    },
    "nslookup": {
        "era": "pre-2000/common legacy terminology",
        "modern": "DNS resolution diagnostic",
        "action": "Perform a controlled DNS lookup.",
    },
    "ifconfig": {
        "era": "pre-2000/common legacy terminology",
        "modern": "network-interface inspection",
        "action": "Use the platform's current interface inspection method.",
    },
    "route": {
        "era": "pre-2000/common legacy terminology",
        "modern": "routing-table inspection",
        "action": "Inspect routing information without modifying routes.",
    },
    "arp": {
        "era": "pre-2000/common legacy terminology",
        "modern": "local neighbor/address-resolution inspection",
        "action": "Inspect local network-neighbor information.",
    },
}


def explain_historical_term(term: str) -> Dict[str, str]:
    """
    Convert historical terminology into a non-executable explanation.

    IMPORTANT:
    This function never executes the historical command.
    """
    entry = HISTORICAL_TERMS.get(term.lower())

    if entry is None:
        return {
            "status": "UNKNOWN",
            "term": term,
            "message": (
                "Historical or unfamiliar terminology detected. "
                "No action was executed."
            ),
            "modern_action": (
                "Stop, identify the intended measurement, and verify "
                "the current platform-specific method."
            ),
        }

    return {
        "status": "HISTORICAL_TERM_RECOGNIZED",
        "term": term,
        "era": entry["era"],
        "modern_equivalent": entry["modern"],
        "modern_action": entry["action"],
        "execution": "NOT_EXECUTED",
    }


# ---------------------------------------------------------------------------
# GROUNDING / EVIDENCE
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def result(
    name: str,
    status: str,
    value: Any = None,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a standardized observation record."""
    return {
        "check": name,
        "status": status,
        "value": value,
        "detail": detail,
    }


def check_clock() -> Dict[str, Any]:
    """Record the current wall-clock timestamp."""
    return result(
        "clock",
        "OBSERVED",
        {
            "utc": utc_now(),
            "monotonic_ns": time.monotonic_ns(),
        },
        "Wall-clock and monotonic timestamps captured.",
    )


def check_system() -> List[Dict[str, Any]]:
    """Collect basic non-invasive system observations."""
    observations = []

    observations.append(
        result(
            "operating_system",
            "OBSERVED",
            {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
        )
    )

    load = None
    try:
        load = os.getloadavg()
        observations.append(
            result(
                "system_load",
                "OBSERVED",
                {
                    "one_minute": load[0],
                    "five_minute": load[1],
                    "fifteen_minute": load[2],
                },
            )
        )
    except (AttributeError, OSError):
        observations.append(
            result(
                "system_load",
                "UNKNOWN",
                detail="Load average is unavailable on this platform.",
            )
        )

    disk_path = Path.cwd().anchor or "."
    try:
        usage = shutil.disk_usage(disk_path)
        observations.append(
            result(
                "disk",
                "OBSERVED",
                {
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "used_fraction": usage.used / usage.total,
                },
            )
        )
    except OSError as exc:
        observations.append(
            result("disk", "UNKNOWN", detail=str(exc))
        )

    return observations


def check_dns(hostname: str = "example.com") -> Dict[str, Any]:
    """Perform a DNS resolution observation."""
    start = time.perf_counter()

    try:
        addresses = socket.getaddrinfo(hostname, 443)
        elapsed_ms = (time.perf_counter() - start) * 1000

        unique_addresses = sorted(
            {
                item[4][0]
                for item in addresses
                if item and item[4]
            }
        )

        return result(
            "dns",
            "PASS" if unique_addresses else "DEGRADED",
            {
                "hostname": hostname,
                "resolution_ms": round(elapsed_ms, 3),
                "addresses": unique_addresses,
            },
        )

    except socket.gaierror as exc:
        return result(
            "dns",
            "FAIL",
            {
                "hostname": hostname,
                "resolution_ms": None,
            },
            f"DNS resolution failed: {exc}",
        )


def check_network(hostname: str = "example.com") -> Dict[str, Any]:
    """
    Perform a conservative TCP connectivity check.

    This does not modify routing tables, interfaces, firewall settings,
    or other system configuration.
    """
    start = time.perf_counter()

    try:
        with socket.create_connection(
            (hostname, 443),
            timeout=DEFAULT_TIMEOUT,
        ):
            elapsed_ms = (time.perf_counter() - start) * 1000

        return result(
            "network_tcp_443",
            "PASS",
            {
                "hostname": hostname,
                "port": 443,
                "connection_ms": round(elapsed_ms, 3),
            },
        )

    except OSError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000

        return result(
            "network_tcp_443",
            "FAIL",
            {
                "hostname": hostname,
                "port": 443,
                "connection_ms": round(elapsed_ms, 3),
            },
            f"Connection failed: {exc}",
        )


def check_external_endpoints() -> List[Dict[str, Any]]:
    """
    Check several independent HTTPS endpoints.

    The purpose is not to prove that the entire Internet is healthy.
    It is only to determine whether several independent observations
    agree at this moment.
    """
    endpoints = [
        "example.com",
        "www.cloudflare.com",
        "www.google.com",
    ]

    observations = []

    for hostname in endpoints:
        observations.append(check_network(hostname))

    return observations


# ---------------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------------

def classify(observations: List[Dict[str, Any]]) -> str:
    """
    Conservative classification.

    IMPORTANT:
    This is a status classification, not a causal explanation.
    """
    failures = [
        item for item in observations
        if item.get("status") == "FAIL"
    ]

    unknowns = [
        item for item in observations
        if item.get("status") == "UNKNOWN"
    ]

    if unknowns and not failures:
        return "UNKNOWN"

    if failures:
        return "DEGRADED"

    return "NORMAL"


# ---------------------------------------------------------------------------
# RECORDING
# ---------------------------------------------------------------------------

def save_evidence(record: Dict[str, Any]) -> Path:
    """Write a grounding record atomically."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    filename = (
        f"grounding_{timestamp}_{record['record_id']}.json"
    )

    destination = OUTPUT_DIR / filename
    temporary = destination.with_suffix(".tmp")

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)

    temporary.replace(destination)

    return destination


# ---------------------------------------------------------------------------
# DISPLAY
# ---------------------------------------------------------------------------

def print_header() -> None:
    print()
    print("=" * 64)
    print("                 SHADOW-SEE GROUNDING CHECK")
    print("=" * 64)
    print()
    print("What do I know?")
    print("What can I measure?")
    print("What can I reproduce?")
    print("What can another system confirm?")
    print("What remains unknown?")
    print()
    print("-" * 64)


def print_observation(item: Dict[str, Any]) -> None:
    status = item.get("status", "UNKNOWN")
    name = item.get("check", "unknown")

    print(f"[{status:<12}] {name}")

    if item.get("detail"):
        print(f"              {item['detail']}")


def run_grounding_check() -> Dict[str, Any]:
    """Execute one complete grounding cycle."""
    started_monotonic = time.monotonic_ns()

    observations: List[Dict[str, Any]] = []

    observations.append(check_clock())
    observations.extend(check_system())
    observations.append(check_dns())
    observations.append(check_network())
    observations.extend(check_external_endpoints())

    status = classify(observations)

    finished_monotonic = time.monotonic_ns()

    record = {
        "record_id": str(uuid.uuid4()),
        "schema_version": SCHEMA_VERSION,
        "shadow_see_version": VERSION,
        "timestamp_utc": utc_now(),
        "execution": {
            "started_monotonic_ns": started_monotonic,
            "finished_monotonic_ns": finished_monotonic,
            "duration_ms": (
                finished_monotonic - started_monotonic
            ) / 1_000_000,
        },
        "purpose": "grounding_check",
        "status": status,
        "interpretation": "NONE",
        "observations": observations,
        "historical_compatibility": {
            "enabled": True,
            "execution_of_legacy_commands": False,
        },
    }

    return record


def main() -> int:
    print_header()

    print("[GROUNDING] Interrupting interpretation.")
    print("[GROUNDING] Collecting measurable facts.")
    print()

    try:
        record = run_grounding_check()

        print("OBSERVATIONS")
        print("-" * 64)

        for observation in record["observations"]:
            print_observation(observation)

        print()
        print("-" * 64)
        print(f"STATUS: {record['status']}")
        print("CAUSE:  NOT ASSIGNED")
        print()

        evidence_path = save_evidence(record)

        print(f"EVIDENCE: {evidence_path}")
        print()
        print("NO CAUSE HAS BEEN ASSIGNED.")
        print("OBSERVATION HAS BEEN PRESERVED.")
        print()
        print("GROUNDING RULE:")
        print("  What do I know?")
        print("  What can I measure?")
        print("  What can I reproduce?")
        print()

        return 0

    except KeyboardInterrupt:
        print()
        print("[INTERRUPTED] Grounding check stopped by operator.")
        print("[ACTION] No interpretation was assigned.")
        return 130

    except Exception as exc:
        print()
        print("[CRITICAL] Grounding check itself encountered an error.")
        print(f"[DETAIL] {type(exc).__name__}: {exc}")
        print()
        print("UPDATED ACTION:")
        print(
            "Stop interpretation. Preserve the error exactly as observed, "
            "then investigate the grounding tool itself."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
