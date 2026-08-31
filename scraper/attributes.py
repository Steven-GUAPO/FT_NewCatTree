"""
attributes.py  -  Deterministic attribute extraction & normalization.

Turns a raw product record (title + description + optional spec table) into a
normalized attribute set. Every extracted field carries:

    value        the normalized value
    provenance   which source field it came from  (specs > title > description)
    confidence   0.0-1.0, higher when it came from a structured/authoritative field

Design choices (see README for the "why"):
  * Deterministic rules first. On a storefront, a blank field is safer than a
    hallucinated spec, so the rule engine is the source of truth and the LLM
    pass (not required to run this) only *fills* nulls, never overrides.
  * Everything normalizes to canonical units (capacity -> GB integer, interface ->
    max Gb/s float) so the catalog can be sorted and range-filtered.
  * Provenance + confidence make QA possible: you can surface every value that
    was only guessed from the title, or every product where the title capacity
    disagreed with the spec table.

Pure standard library on purpose: this stage must run anywhere, including in a
sandbox with no network and nothing pip-installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# --------------------------------------------------------------------------- #
#  Small helpers
# --------------------------------------------------------------------------- #

@dataclass
class Field:
    """A single extracted value plus where it came from and how sure we are."""
    value: Any = None
    provenance: Optional[str] = None      # "specs" | "title" | "description" | "brand" | None
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {"value": self.value, "provenance": self.provenance, "confidence": round(self.confidence, 2)}


# Confidence weights by where a value was found. A spec-table hit is worth more
# than a title hit, which is worth more than free-text description.
SOURCE_CONF = {"specs": 0.98, "title": 0.9, "brand": 0.9, "description": 0.7}


def _search_sources(pattern, sources, flags=re.I):
    """
    Try a regex against ordered (name, text) sources and return the first match
    with its source name. `sources` is a list of (name, text) tuples in priority
    order (specs first, then title, then description).
    """
    rx = re.compile(pattern, flags)
    for name, text in sources:
        if not text:
            continue
        m = rx.search(text)
        if m:
            return m, name
    return None, None


# --------------------------------------------------------------------------- #
#  Capacity
# --------------------------------------------------------------------------- #

_CAP_UNIT_TO_GB = {"tb": 1000, "gb": 1, "pb": 1_000_000, "mb": 0.001}
# The trailing (?!/s) is critical: with re.IGNORECASE, "GB"/"MB" also match the
# throughput tokens "Gb/s" and "MB/s", so without this guard a "6000MB/s" read
# speed would be misread as a 6 GB capacity. We exclude anything followed by /s.
_CAP_RX = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(TB|GB|PB|MB)\b(?!/s)", re.I)


def _cap_to_gb(num: str, unit: str) -> int:
    return int(round(float(num) * _CAP_UNIT_TO_GB[unit.lower()]))


def extract_capacity(sources) -> Field:
    """
    Capacity is the single most important sort/filter key, so it gets special
    handling:
      * decimal convention (1 TB = 1000 GB) to match how storage is marketed and
        how FilmTools' own capacity facet is labelled (1TB, 2TB, 1.92TB ...);
      * configurable products ("1TB, 2TB, 4TB, 8TB") are detected and all options
        kept, with value_gb set to the smallest for a stable sort position.
    """
    # Title is the most reliable place for the *marketed* capacity of the SKU.
    title = dict(sources).get("title", "") or ""
    title_caps = [(m.group(1), m.group(2)) for m in _CAP_RX.finditer(title)]

    # Drop obvious non-capacity numbers that share the GB/TB token (e.g. cache
    # "64MB", throughput "80Gb/s" is not matched because unit is Gb not GB/TB).
    options = []
    for num, unit in title_caps:
        gb = _cap_to_gb(num, unit)
        if unit.lower() == "mb" and gb < 1:        # cache sizes etc. - ignore
            continue
        options.append((f"{num}{unit.upper()}", gb))

    if options:
        # dedupe, preserve order
        seen, uniq = set(), []
        for raw, gb in options:
            if gb not in seen:
                seen.add(gb)
                uniq.append((raw, gb))
        configurable = len(uniq) > 1
        primary = min(uniq, key=lambda t: t[1])
        val = {
            "raw": primary[0],
            "value_gb": primary[1],
            "display": _human_capacity(primary[1]),
            "configurable": configurable,
            "options_gb": [gb for _, gb in uniq] if configurable else None,
        }
        return Field(val, "title", SOURCE_CONF["title"])

    # Fall back to specs / description.
    m, src = _search_sources(_CAP_RX.pattern, [s for s in sources if s[0] != "title"])
    if m:
        gb = _cap_to_gb(m.group(1), m.group(2))
        val = {"raw": f"{m.group(1)}{m.group(2).upper()}", "value_gb": gb,
               "display": _human_capacity(gb), "configurable": False, "options_gb": None}
        return Field(val, src, SOURCE_CONF.get(src, 0.6) * 0.85)
    return Field(None, None, 0.0)


def _human_capacity(gb: int) -> str:
    if gb >= 1000 and gb % 1000 == 0:
        return f"{gb // 1000} TB"
    if gb >= 1000:
        return f"{gb / 1000:g} TB"
    return f"{gb} GB"


# --------------------------------------------------------------------------- #
#  Media type  (what *is* this thing)
# --------------------------------------------------------------------------- #

def extract_media_type(sources) -> Field:
    text = " ".join(t for _, t in sources if t).lower()
    title = dict(sources).get("title", "").lower()

    # "array" deliberately excludes a bare/1-bay "bay" so a single-bay M.2 SSD
    # ("1 bay supporting M.2") is not mistaken for a RAID box.
    array_sig = r"\braid\b|dual-?disk|\dbig|\b([2-9]|1\d)\s*-?\s*bay"
    ssd_sig = r"\bssd\b|solid[- ]state|\bnvme\b"
    hdd_sig = r"hard drive|hard disk|\bhdd\b|7200 ?rpm|5400 ?rpm|ironwolf"

    def classify(blob: str):
        """One ordered pass over a single text blob; return a media tag or None."""
        for media, pat in [
            ("cable", r"\bcable\b"),
            ("reader", r"card reader|\breader\b"),
            ("tape", r"\blto\b|tape (storage|drive|archiving)|\bwo?rm\b cartridge"),
            ("optical", r"\bblu-?ray\b|\bdvd\b|\bcd\b(?!\s*card)"),
            ("memory_card", r"cfexpress|cfast|\bsdxc\b|\bsdhc\b|\buhs-?ii\b|micro ?sd|\bsd card\b|memory card"),
            ("nas", r"\bnas\b|network storage|network attached"),
            ("recorder", r"\brecorder\b"),
        ]:
            if re.search(pat, blob):
                return media
        # Storage arrays & drives, ahead of dock/hub: a "2big Dock ... RAID Drive"
        # is the RAID drive it says it is, not a dock. A RAID box defaults to
        # spinning (hdd) unless it explicitly advertises SSD/NVMe.
        if re.search(array_sig, blob):
            return "ssd" if (re.search(ssd_sig, blob) and not re.search(hdd_sig, blob)) else "hdd"
        if re.search(ssd_sig, blob):
            return "ssd"
        if re.search(hdd_sig, blob):
            return "hdd"
        for media, pat in [("hub", r"\bhub\b"), ("dock", r"\bdock(ing)?\b"), ("enclosure", r"enclosure\b")]:
            if re.search(pat, blob):
                return media
        return None

    # Title first (authoritative for the object type); description only fills the
    # gap when the title alone is inconclusive. This stops a stray "includes USB-C
    # cable" in a description from reclassifying a drive as a cable.
    media = classify(title)
    if media:
        return Field(media, "title", 0.92)
    media = classify(text)
    if media:
        return Field(media, "description", 0.72)
    return Field("unknown", None, 0.0)


# --------------------------------------------------------------------------- #
#  Form factor
# --------------------------------------------------------------------------- #

def extract_form_factor(sources, media_type: Optional[str]) -> Field:
    text = " ".join(t for _, t in sources if t).lower()
    title = dict(sources).get("title", "").lower()

    def hit(pat, src_text):
        return re.search(pat, src_text) is not None

    # rugged portable takes priority over plain portable
    rugged = hit(r"rugged|ip6?\d|crushproof|dustproof|waterproof|drop[- ]?proof|shockproof", text)
    if media_type in ("cable", "reader", "dock", "hub"):
        return Field("accessory", "title", 0.85)
    if media_type == "memory_card":
        return Field("card", "title", 0.9)
    if media_type == "tape":
        return Field("deck_or_cartridge", "title", 0.8)
    if media_type == "optical":
        return Field("disc", "title", 0.8)
    if media_type == "nas":
        return Field("nas_enclosure", "title", 0.8)

    if hit(r"portable|ultra-?portable|bus[- ]?powered|pocket", text) or hit(r"envoy|extreme portable|\bt7\b|\bt9\b", title):
        return Field("rugged_portable" if rugged else "portable", "title" if hit(r"portable", title) else "description",
                     0.85 if hit(r"portable", title) else 0.7)
    if hit(r"\bdesktop\b|\bd2\b|\bdock\b|2big|6big|8big|4big", text):
        return Field("desktop", "description", 0.75)
    if hit(r"rackmount|rack[- ]?mount|1u|2u|3u", text):
        return Field("rackmount", "description", 0.8)
    if hit(r"\binternal\b|\bm\.2\b|\b2\.5\"|\b3\.5\"", text):
        return Field("bare_internal", "description", 0.7)
    if rugged:
        return Field("rugged_portable", "description", 0.65)
    return Field(None, None, 0.0)


# --------------------------------------------------------------------------- #
#  Interfaces / connectivity
# --------------------------------------------------------------------------- #

# (canonical family, regex, nominal max Gb/s).  Explicit "N Gb/s" in text wins.
_INTERFACE_RULES = [
    ("Thunderbolt 5", r"thunderbolt\s*5|\btb5\b", 80.0),
    ("Thunderbolt 4", r"thunderbolt\s*4|\btb4\b", 40.0),
    ("Thunderbolt 3", r"thunderbolt\s*3|\btb3\b", 40.0),
    ("Thunderbolt 2", r"thunderbolt\s*2|\btb2\b", 20.0),
    ("USB4",          r"\busb\s*4\b|\busb4\b", 40.0),
    ("USB 3.2 Gen 2x2", r"usb\s*3\.2\s*gen\s*2x2|20\s*gb/s", 20.0),
    ("USB 3.2 Gen 2", r"usb\s*3\.[12]\s*gen\s*2\b|usb\s*3\.1\s*gen\s*2", 10.0),
    ("USB 3.2 Gen 1", r"usb\s*3\.2\s*gen\s*1|usb\s*3\.1\s*gen\s*1", 5.0),
    ("USB 3.1",       r"usb\s*3\.1\b", 10.0),
    ("USB 3.0",       r"usb\s*3\.0\b", 5.0),
    ("USB 2.0",       r"usb\s*2\.0\b", 0.48),
    ("FireWire 800",  r"firewire\s*800|\bfw800\b", 0.8),
    ("SATA",          r"\bsata\b", 6.0),
    ("PCIe/NVMe",     r"\bpcie\b|\bnvme\b", None),
    ("Ethernet",      r"ethernet|\brj-?45\b|\bgbe\b|10gbe", None),
    ("CFexpress Type B", r"cfexpress[^.]{0,12}type\s*b", None),
    ("CFexpress Type A", r"cfexpress[^.]{0,12}type\s*a", None),
    ("SDXC UHS-II",   r"uhs-?ii", None),
    ("CFast 2.0",     r"cfast", None),
]

_CONNECTOR_RULES = [
    ("USB-C", r"usb-?c|type-?c|usb type-c"),
    ("USB-A", r"usb-?a|type-?a"),
]

_EXPLICIT_GBPS = re.compile(r"(\d+(?:\.\d+)?)\s*Gb/s", re.I)


def extract_interfaces(sources) -> Field:
    text = " ".join(t for _, t in sources if t)
    low = text.lower()
    found, seen = [], set()
    for family, pat, nominal in _INTERFACE_RULES:
        if re.search(pat, low):
            if family in seen:
                continue
            seen.add(family)
            found.append({"family": family, "max_gbps": nominal})
    # Promote an explicit "80 Gb/s" etc. onto the fastest matched host interface.
    explicit = [float(m.group(1)) for m in _EXPLICIT_GBPS.finditer(text)]
    if explicit and found:
        top = max(explicit)
        # attach to the interface with the highest nominal (or first)
        host = max(found, key=lambda f: (f["max_gbps"] or 0))
        host["max_gbps"] = max(host["max_gbps"] or 0, top)

    connectors = [c for c, pat in _CONNECTOR_RULES if re.search(pat, low)]

    if not found and not connectors:
        return Field(None, None, 0.0)
    prov = "specs" if any(n == "specs" for n, t in sources if t and re.search(r"gb/s|usb|thunderbolt", t.lower())) else "title"
    val = {"interfaces": found, "connectors": connectors}
    return Field(val, prov, 0.85 if found else 0.6)


# --------------------------------------------------------------------------- #
#  Speed  (read / write MB/s, bus Gb/s, spindle RPM)
# --------------------------------------------------------------------------- #

_RW_RX = re.compile(r"read[^.\d]{0,30}?(\d{2,5})\s*MB/s.*?write[^.\d]{0,30}?(\d{2,5})\s*MB/s", re.I | re.S)
_READ_RX = re.compile(r"read[^.\d]{0,30}?(?:up to\s*)?(\d{2,5})\s*MB/s", re.I)
_WRITE_RX = re.compile(r"write[^.\d]{0,30}?(?:up to\s*)?(\d{2,5})\s*MB/s", re.I)
_ANY_MBPS_RX = re.compile(r"(?:up to|speeds?(?:\s*of)?(?:\s*up to)?|over)\s*(\d{3,5})\s*MB/s", re.I)
_RPM_RX = re.compile(r"(\d{4,5})\s*-?\s*rpm", re.I)


def extract_speed(sources) -> Field:
    text = " ".join(t for _, t in sources if t)
    read = write = bus = rpm = None

    m = _RW_RX.search(text)
    if m:
        read, write = int(m.group(1)), int(m.group(2))
    else:
        mr, mw = _READ_RX.search(text), _WRITE_RX.search(text)
        if mr:
            read = int(mr.group(1))
        if mw:
            write = int(mw.group(1))
        if read is None and write is None:
            ma = _ANY_MBPS_RX.search(text)
            if ma:
                read = int(ma.group(1))       # "up to 6000MB/s" -> treat as peak read

    mrpm = _RPM_RX.search(text)
    if mrpm:
        rpm = int(mrpm.group(1))

    if read is None and write is None and rpm is None:
        return Field(None, None, 0.0)
    val = {"read_mbps": read, "write_mbps": write, "rpm": rpm}
    return Field(val, "description", 0.7)


# --------------------------------------------------------------------------- #
#  Bays / RAID
# --------------------------------------------------------------------------- #

_BAYS_RX = re.compile(r"(\d+)\s*-?\s*bay|dual-?disk|(\d+)big", re.I)
_RAID_RX = re.compile(r"raid\s*([0-9]+(?:\s*/\s*[0-9]+)*)", re.I)


def extract_bays_raid(sources, media_type) -> Field:
    text = " ".join(t for _, t in sources if t)
    low = text.lower()
    bays = None
    m = _BAYS_RX.search(text)
    if m:
        if m.group(1):
            bays = int(m.group(1))
        elif "dual" in m.group(0).lower():
            bays = 2
        elif m.group(2):
            bays = int(m.group(2))
    raid_levels = []
    for m in _RAID_RX.finditer(low):
        raid_levels += [int(x) for x in re.findall(r"\d+", m.group(1))]
    raid_levels = sorted(set(raid_levels))
    if bays is None and not raid_levels:
        return Field(None, None, 0.0)
    return Field({"bays": bays, "raid_levels": raid_levels or None}, "title" if m else "description", 0.8)


# --------------------------------------------------------------------------- #
#  Ruggedness
# --------------------------------------------------------------------------- #

_IP_RX = re.compile(r"\bIP\s?(\d{2})\b", re.I)
_DROP_RX = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(?:meter|m\b|')\s*(?:drop|drop protection|drop-?proof)", re.I)
_DROP_RX2 = re.compile(r"drop(?:-| )?(?:proof|protection)?\s*(?:up to\s*)?(\d+(?:\.\d+)?)\s*(?:meter|m\b|')", re.I)


def extract_rugged(sources) -> Field:
    text = " ".join(t for _, t in sources if t)
    low = text.lower()
    ip = None
    m = _IP_RX.search(text)
    if m:
        ip = "IP" + m.group(1)
    drop_m = None
    for rx in (_DROP_RX, _DROP_RX2):
        md = rx.search(text)
        if md:
            v = float(md.group(1))
            # normalize feet -> meters if the match used '
            if "'" in md.group(0):
                v = round(v * 0.3048, 2)
            drop_m = v
            break
    feats = []
    for feat in ("crushproof", "dustproof", "waterproof", "shockproof", "rain-resistant", "rain resistant"):
        if feat.replace("-", "").replace(" ", "") in low.replace("-", "").replace(" ", ""):
            feats.append(feat.split()[0] if " " in feat else feat.replace("-resistant", "-resistant"))
    is_rugged = bool(ip or drop_m or feats or re.search(r"\brugged\b", low))
    if not is_rugged:
        return Field(None, None, 0.0)
    return Field({"rugged": True, "ip_rating": ip, "drop_m": drop_m,
                  "features": sorted(set(feats)) or None}, "description", 0.75)


# --------------------------------------------------------------------------- #
#  Misc scalar fields
# --------------------------------------------------------------------------- #

_ENC_RX = re.compile(r"(256|128)-?bit\s*AES", re.I)
_WARRANTY_RX = re.compile(r"(\d+)\s*-?\s*year", re.I)
_LTO_RX = re.compile(r"\bLTO-?(\d)\b", re.I)
_CARD_TYPE_RX = re.compile(r"(CFexpress(?:\s*(?:2\.0|v\d))?\s*Type\s*[AB]|CFast\s*2\.0|SDXC\s*UHS-?II|microSDXC|SDXC|microSD)", re.I)
_VSPEED_RX = re.compile(r"\bV(30|60|90)\b")


def _scalar(pattern_field, sources, transform=lambda m: m.group(1)):
    m, src = _search_sources(pattern_field.pattern, sources)
    if m:
        return Field(transform(m), src, SOURCE_CONF.get(src, 0.6))
    return Field(None, None, 0.0)


def extract_encryption(sources) -> Field:
    m, src = _search_sources(_ENC_RX.pattern, sources)
    if m:
        return Field(f"{m.group(1)}-bit AES", src, SOURCE_CONF.get(src, 0.7))
    return Field(None, None, 0.0)


def extract_bus_powered(sources) -> Field:
    text = " ".join(t for _, t in sources if t).lower()
    if "bus-powered" in text or "bus powered" in text:
        return Field(True, "description", 0.8)
    if re.search(r"external power (adapter|supply)\b", text) and "no" not in text[:0]:
        return Field(False, "description", 0.5)
    return Field(None, None, 0.0)


def extract_warranty_years(sources) -> Field:
    # take the max plausible warranty figure (avoid "2 year" vs "3 year" ambiguity by max)
    text = " ".join(t for _, t in sources if t)
    years = [int(x) for x in _WARRANTY_RX.findall(text) if 1 <= int(x) <= 10]
    if years:
        return Field(max(years), "description", 0.6)
    return Field(None, None, 0.0)


def extract_lto(sources) -> Field:
    m, src = _search_sources(_LTO_RX.pattern, sources)
    if m:
        gen = int(m.group(1))
        # LTO native capacities (TB) by generation
        native = {5: 1.5, 6: 2.5, 7: 6, 8: 12, 9: 18}.get(gen)
        return Field({"generation": gen, "native_tb": native}, src, SOURCE_CONF.get(src, 0.8))
    return Field(None, None, 0.0)


def extract_card_spec(sources) -> Field:
    m, src = _search_sources(_CARD_TYPE_RX.pattern, sources)
    if not m:
        return Field(None, None, 0.0)
    text = " ".join(t for _, t in sources if t)
    v = _VSPEED_RX.search(text)
    return Field({"card_type": re.sub(r"\s+", " ", m.group(1)).strip(),
                  "video_speed_class": ("V" + v.group(1)) if v else None},
                 src, SOURCE_CONF.get(src, 0.85))


def extract_brand(record, sources) -> Field:
    if record.get("brand"):
        return Field(record["brand"], "brand", 0.9)
    # first token heuristics
    title = record.get("title", "")
    known = ["SanDisk", "Samsung", "OWC", "LaCie", "Angelbird", "Sony", "ProGrade",
             "Seagate", "Western Digital", "WD", "G-Technology", "Glyph", "Lexar",
             "Transcend", "Crucial", "Sabrent", "Oyen", "Promise", "Quantum"]
    for b in known:
        if re.search(r"\b" + re.escape(b) + r"\b", title, re.I):
            return Field(b, "title", 0.8)
    tok = title.split()
    return Field(tok[0] if tok else None, "title", 0.4)


# --------------------------------------------------------------------------- #
#  Orchestration
# --------------------------------------------------------------------------- #

def _spec_text(record) -> str:
    specs = record.get("specs") or {}
    parts = []
    for k, v in specs.items():
        parts.append(f"{k}: {v}")
    return " | ".join(parts)


def extract_all(record: dict) -> dict:
    """Run every extractor and return a structured attribute dict for one product."""
    title = record.get("title", "") or ""
    desc = record.get("description", "") or ""
    spectext = _spec_text(record)

    # Priority-ordered sources: structured specs win, then title, then description.
    sources = [("specs", spectext), ("title", title), ("description", desc)]

    brand = extract_brand(record, sources)
    media = extract_media_type(sources)
    form = extract_form_factor(sources, media.value)
    capacity = extract_capacity(sources)
    interfaces = extract_interfaces(sources)
    speed = extract_speed(sources)
    bays = extract_bays_raid(sources, media.value)
    rugged = extract_rugged(sources)
    encryption = extract_encryption(sources)
    bus_powered = extract_bus_powered(sources)
    warranty = extract_warranty_years(sources)
    lto = extract_lto(sources)
    card = extract_card_spec(sources)

    attrs = {
        "brand": brand.as_dict(),
        "media_type": media.as_dict(),
        "form_factor": form.as_dict(),
        "capacity": capacity.as_dict(),
        "interfaces": interfaces.as_dict(),
        "speed": speed.as_dict(),
        "bays_raid": bays.as_dict(),
        "rugged": rugged.as_dict(),
        "encryption": encryption.as_dict(),
        "bus_powered": bus_powered.as_dict(),
        "warranty_years": warranty.as_dict(),
        "lto": lto.as_dict(),
        "card": card.as_dict(),
    }

    # QA notes: surface disagreements & low-confidence gaps rather than hiding them.
    notes = []
    # capacity mismatch title vs description
    cap_title = [(_cap_to_gb(n, u)) for n, u in _CAP_RX.findall(title)]
    cap_desc = [(_cap_to_gb(n, u)) for n, u in _CAP_RX.findall(desc)]
    if cap_title and cap_desc and set(cap_title).isdisjoint(cap_desc):
        notes.append(f"capacity mismatch: title says {sorted(set(cap_title))}GB but description says {sorted(set(cap_desc))}GB")
    if media.value in (None, "unknown"):
        notes.append("media_type could not be determined")
    if capacity.value is None and media.value in ("ssd", "hdd", "memory_card", "tape"):
        notes.append("no capacity found for a capacity-bearing product")
    attrs["_qa_notes"] = notes

    # A rough overall completeness score = mean confidence of the core fields.
    core = ["media_type", "form_factor", "capacity", "interfaces"]
    attrs["_completeness"] = round(
        sum(attrs[k]["confidence"] for k in core) / len(core), 2
    )
    return attrs


if __name__ == "__main__":  # tiny smoke test
    demo = {
        "title": "SanDisk 2TB Extreme PRO Portable SSD V2 SDSSDE81-2T00-G25",
        "brand": "SanDisk",
        "description": "Read and write speeds up to 2000 MB/s via USB 3.2 Gen 2x2 Type-C. "
                       "256-bit AES encryption. IP55 dust and water resistance, drop-proof up to 6'. 2TB.",
    }
    import json
    print(json.dumps(extract_all(demo), indent=2))
