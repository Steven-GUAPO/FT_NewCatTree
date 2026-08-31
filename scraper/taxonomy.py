"""
taxonomy.py  -  The proposed category model + deterministic assignment.

WHY THIS MODEL (full argument in taxonomy.md and README):

FilmTools' live storage tree mixes *different classification axes at the same
level*: form factor (Portable, Desktop, Internal) sits beside interface
(Thunderbolt Drives) beside ruggedness (Rugged Drives) beside media
(SSD Solid State Drives). A single real SKU - e.g. a rugged, portable,
Thunderbolt SSD - legitimately belongs under four sibling top categories at
once. In a strict single-parent tree it must be forced into one, so the other
three browse paths silently fail to show it. That is the classic mixed-axis
taxonomy failure, and it is exactly what makes their nav frustrating to shop.

The fix is not "a better tree" - it is to stop overloading one tree with jobs it
can't do. This model has three cooperating layers:

  1. PRIMARY SPINE  - a clean, single-axis tree keyed on ONE question: "what kind
     of object is this?" (a drive, a card, a RAID box, a reader, tape...). Every
     product has exactly one home here (MECE), so breadcrumbs and URLs are stable.

  2. FACETS         - orthogonal filters (capacity, interface, speed tier,
     ruggedness, bays, brand, price). Multi-dimensional products get found by
     *any* of their dimensions instead of being trapped in one folder.

  3. WORKFLOWS      - curated landing pages that are really saved facet queries
     ("On-Set Capture", "Edit Fast", "Archive Forever"). They give a filmmaker a
     task-first entry point without pretending to be exclusive folders, so a
     product can appear in several without contradiction.

This module encodes all three and assigns each product deterministically from
the normalized attributes produced by attributes.py.
"""

from __future__ import annotations
from typing import Optional


# --------------------------------------------------------------------------- #
#  1. PRIMARY SPINE  (single-axis: object type).  Exactly one home per product.
# --------------------------------------------------------------------------- #

PRIMARY_SPINE = {
    "Drives": ["Solid-State Drives (SSD)", "Hard Disk Drives (HDD)", "Bare / Internal Drives"],
    "Multi-Bay & RAID": ["2-Bay", "4-Bay", "6-Bay & Up"],
    "Camera Media": ["CFexpress", "SD / microSD", "CFast", "Other Cards"],
    "Readers & Docks": ["Card Readers", "Docks & Hubs"],
    "Network Storage (NAS)": ["Desktop NAS", "Rackmount NAS"],
    "Archive & Backup": ["LTO Tape", "Optical (Blu-ray/DVD/CD)"],
    "Recorders": ["Field Recorders"],
    "Cables & Connectivity": ["Thunderbolt / USB Cables", "Adapters"],
    "Uncategorized": ["Needs Review"],   # anything the rules can't place -> review queue
}


def assign_primary(attrs: dict) -> list[str]:
    """Return a single [Category, Subcategory] path for the primary spine."""
    media = attrs["media_type"]["value"]
    form = attrs["form_factor"]["value"]
    bays = (attrs["bays_raid"]["value"] or {}).get("bays")
    raid = (attrs["bays_raid"]["value"] or {}).get("raid_levels")

    if media == "cable":
        return ["Cables & Connectivity", "Thunderbolt / USB Cables"]
    if media == "reader":
        return ["Readers & Docks", "Card Readers"]
    if media in ("dock", "hub"):
        return ["Readers & Docks", "Docks & Hubs"]
    if media == "memory_card":
        card_type = (attrs["card"]["value"] or {}).get("card_type", "") or ""
        if "cfexpress" in card_type.lower():
            return ["Camera Media", "CFexpress"]
        if "cfast" in card_type.lower():
            return ["Camera Media", "CFast"]
        if "sd" in card_type.lower():
            return ["Camera Media", "SD / microSD"]
        return ["Camera Media", "Other Cards"]
    if media == "tape":
        return ["Archive & Backup", "LTO Tape"]
    if media == "optical":
        return ["Archive & Backup", "Optical (Blu-ray/DVD/CD)"]
    if media == "nas":
        return ["Network Storage (NAS)", "Rackmount NAS" if form == "rackmount" else "Desktop NAS"]
    if media == "recorder":
        return ["Recorders", "Field Recorders"]

    # Multi-bay / RAID wins over the plain Drives bucket for any 2+ bay unit.
    if (bays and bays >= 2) or raid:
        if bays and bays >= 6:
            return ["Multi-Bay & RAID", "6-Bay & Up"]
        if bays == 4:
            return ["Multi-Bay & RAID", "4-Bay"]
        return ["Multi-Bay & RAID", "2-Bay"]

    if media == "ssd":
        return ["Drives", "Bare / Internal Drives"] if form == "bare_internal" else ["Drives", "Solid-State Drives (SSD)"]
    if media == "hdd":
        return ["Drives", "Bare / Internal Drives"] if form == "bare_internal" else ["Drives", "Hard Disk Drives (HDD)"]
    if media == "enclosure":
        return ["Drives", "Bare / Internal Drives"]

    return ["Uncategorized", "Needs Review"]


# --------------------------------------------------------------------------- #
#  2. FACETS  (orthogonal, multi-valued).  Derived from normalized attributes.
# --------------------------------------------------------------------------- #

# Capacity buckets chosen to line up with FilmTools' own capacity facet labels
# and with how editors actually think about card vs working-drive vs archive size.
CAPACITY_BUCKETS = [
    ("< 256 GB", 0, 255),
    ("256 GB - 1 TB", 256, 1000),
    ("1 - 2 TB", 1001, 2000),
    ("2 - 4 TB", 2001, 4000),
    ("4 - 8 TB", 4001, 8000),
    ("8 - 20 TB", 8001, 20000),
    ("20 TB+", 20001, 10_000_000),
]

# Speed tiers by peak sequential read (MB/s) or, if unknown, by bus Gb/s.
SPEED_TIERS = [
    ("Standard (up to ~600 MB/s)", 0, 600),
    ("Fast (600 - 1500 MB/s)", 601, 1500),
    ("Very Fast (1500 - 3000 MB/s)", 1501, 3000),
    ("Ultra (3000 MB/s+)", 3001, 10_000_000),
]


def _capacity_bucket(gb: Optional[int]) -> Optional[str]:
    if gb is None:
        return None
    for label, lo, hi in CAPACITY_BUCKETS:
        if lo <= gb <= hi:
            return label
    return None


def _speed_tier(attrs) -> Optional[str]:
    speed = attrs["speed"]["value"] or {}
    read = speed.get("read_mbps")
    if read is None:
        # fall back to bus max Gb/s -> approx MB/s (Gb/s * 125)
        ifaces = (attrs["interfaces"]["value"] or {}).get("interfaces", [])
        gbps = max([i["max_gbps"] for i in ifaces if i.get("max_gbps")], default=None)
        if gbps:
            read = int(gbps * 125 * 0.8)   # 80% of theoretical bus ceiling
    if read is None:
        return None
    for label, lo, hi in SPEED_TIERS:
        if lo <= read <= hi:
            return label
    return None


def derive_facets(attrs: dict) -> dict:
    caps = attrs["capacity"]["value"] or {}
    ifaces = (attrs["interfaces"]["value"] or {}).get("interfaces", [])
    bays = (attrs["bays_raid"]["value"] or {}).get("bays")
    rugged = bool((attrs["rugged"]["value"] or {}).get("rugged"))
    return {
        "media_type": attrs["media_type"]["value"],
        "form_factor": attrs["form_factor"]["value"],
        "interface_families": [i["family"] for i in ifaces] or None,
        "connectors": (attrs["interfaces"]["value"] or {}).get("connectors") or None,
        "capacity_bucket": _capacity_bucket(caps.get("value_gb")),
        "speed_tier": _speed_tier(attrs),
        "rugged": rugged,
        "bays": bays,
        "brand": attrs["brand"]["value"],
        "encrypted": bool(attrs["encryption"]["value"]),
    }


# --------------------------------------------------------------------------- #
#  3. WORKFLOWS  (curated saved-queries; a product may match several)
# --------------------------------------------------------------------------- #

WORKFLOWS = {
    "On-Set Capture": "Camera cards and rugged pocket drives you trust on location.",
    "Offload & Ingest": "Readers, docks and fast bus-powered drives for clearing cards between takes.",
    "Edit Fast": "High-throughput working storage for 4K/8K timelines.",
    "Backup & Redundancy": "Mirrored and multi-bay storage that protects a project.",
    "Archive Forever": "Cold, long-life storage for finished work.",
    "Team & Network": "Shared storage multiple seats can hit at once.",
}


def assign_workflows(attrs: dict, facets: dict) -> list[str]:
    media = attrs["media_type"]["value"]
    form = attrs["form_factor"]["value"]
    rugged = facets["rugged"]
    bays = facets["bays"] or 0
    raid = (attrs["bays_raid"]["value"] or {}).get("raid_levels") or []
    cap_gb = (attrs["capacity"]["value"] or {}).get("value_gb") or 0
    ifaces = set(facets["interface_families"] or [])
    speed = attrs["speed"]["value"] or {}
    read = speed.get("read_mbps") or 0
    fast_bus = bool(ifaces & {"Thunderbolt 5", "Thunderbolt 4", "Thunderbolt 3", "USB4", "USB 3.2 Gen 2x2"})

    out = set()

    # On-Set Capture: camera media, or a rugged pocket drive
    if media == "memory_card" or (media in ("ssd", "hdd") and rugged and form in ("portable", "rugged_portable")):
        out.add("On-Set Capture")

    # Offload & Ingest: readers/docks/hubs, or bus-powered portable SSDs
    if media in ("reader", "dock", "hub") or (media == "ssd" and form in ("portable", "rugged_portable")):
        out.add("Offload & Ingest")

    # Edit Fast: fast working storage - quick SSDs or quick RAID
    if (media == "ssd" and (fast_bus or read >= 1500)) or (bays >= 2 and read >= 400):
        out.add("Edit Fast")

    # Backup & Redundancy: mirrored / multi-bay / desktop HDD
    if (bays and bays >= 2) or (1 in raid) or (media == "hdd" and form == "desktop"):
        out.add("Backup & Redundancy")

    # Archive Forever: tape, optical, or big cold HDD
    if media in ("tape", "optical") or (media == "hdd" and cap_gb >= 8000):
        out.add("Archive Forever")

    # Team & Network: NAS, or 4+ bay arrays, or an Ethernet interface
    if media == "nas" or bays >= 4 or "Ethernet" in ifaces:
        out.add("Team & Network")

    return sorted(out)


# --------------------------------------------------------------------------- #
#  Public entry point
# --------------------------------------------------------------------------- #

def assign(attrs: dict) -> dict:
    primary = assign_primary(attrs)
    facets = derive_facets(attrs)
    workflows = assign_workflows(attrs, facets)
    # assignment confidence rides on how well we could read the driving attributes
    conf = round(min(1.0, 0.4 + 0.6 * attrs.get("_completeness", 0.0)), 2)
    review = primary[0] == "Uncategorized" or attrs.get("_completeness", 0) < 0.5
    return {
        "primary": primary,
        "primary_path": " > ".join(primary),
        "facets": facets,
        "workflows": workflows,
        "assignment_confidence": conf,
        "needs_review": review,
    }
