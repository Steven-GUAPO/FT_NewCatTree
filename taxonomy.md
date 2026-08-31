# Proposed storage taxonomy

A user-first replacement for the current FilmTools storage tree. It is not "a
tidier tree" — it changes the *shape* of the model so the catalog can answer the
questions filmmakers actually ask.

---

## 1. What's wrong with the current tree

FilmTools' live storage nav mixes **different classification axes at the same
level**:

```
Storage
├─ Hard Drives & RAID Storage        (media + arrangement)
│   ├─ Portable Drives               ← form factor
│   ├─ Desktop Drives                ← form factor
│   ├─ RAID Drives                   ← arrangement
│   ├─ SSD Solid State Drives        ← media
│   ├─ Internal Drives               ← form factor
│   └─ Network Storage               ← connection
├─ Memory Cards & Readers
├─ USB Flash Drives                  ← connection
├─ Data Archiving                    ← use case
├─ Rugged Drives                     ← durability
├─ Thunderbolt Drives                ← interface
├─ Solid State Drives                ← media  (again, top level)
├─ Docks
└─ Cables
```

Form factor, media, interface, durability, connection and use-case are all used
as sibling "categories." The consequences show up in the real catalog:

- **One product, many true homes.** A *rugged, portable, Thunderbolt SSD* (OWC
  Envoy Ultra, SanDisk Extreme PRO) legitimately belongs under Rugged Drives
  **and** Thunderbolt Drives **and** SSD **and** Portable Drives. In a strict
  single-parent tree it gets filed in one and silently disappears from the other
  three browse paths.
- **Duplicated branches.** "SSD Solid State Drives" exists both under Hard Drives
  *and* as its own top-level category. Same products, two hand-maintained lists
  that drift apart.
- **No way to combine axes.** A shopper who wants "a 2 TB Thunderbolt SSD under
  $250" cannot express that: the axes they care about are scattered across
  branches instead of being filters they can stack.

This is the classic *mixed-axis, single-parent* failure. You cannot fix it by
renaming branches, because the problem is that one tree is being asked to encode
several independent dimensions at once.

---

## 2. The model: three cooperating layers

Stop overloading one tree. Split the job into three layers that each do one
thing well.

### Layer 1 — Primary spine (one clean axis: *what kind of object is this?*)

A single-axis, mutually-exclusive tree. Every product has exactly **one** home,
so breadcrumbs, URLs and SEO stay stable.

```
Drives
├─ Solid-State Drives (SSD)
├─ Hard Disk Drives (HDD)
└─ Bare / Internal Drives
Multi-Bay & RAID
├─ 2-Bay
├─ 4-Bay
└─ 6-Bay & Up
Camera Media
├─ CFexpress
├─ SD / microSD
├─ CFast
└─ Other Cards
Readers & Docks
├─ Card Readers
└─ Docks & Hubs
Network Storage (NAS)
├─ Desktop NAS
└─ Rackmount NAS
Archive & Backup
├─ LTO Tape
└─ Optical (Blu-ray/DVD/CD)
Recorders
└─ Field Recorders
Cables & Connectivity
├─ Thunderbolt / USB Cables
└─ Adapters
```

The spine is keyed on the *noun* — the thing in the box — which is the one axis
that is genuinely single-valued. "Portable," "Thunderbolt," "rugged" and "2 TB"
are **not** on the spine, because they are not what the object *is*; they are
properties of it. That is Layer 2's job.

### Layer 2 — Facets (orthogonal, multi-valued filters)

Every other dimension becomes a facet the shopper can stack:

| Facet | Example values |
|---|---|
| Media type | SSD, HDD, tape, memory card, dock, cable |
| Form factor | portable, rugged-portable, desktop, rackmount, card, accessory |
| Interface | Thunderbolt 5/4/3, USB4, USB 3.2 Gen 2x2, FireWire 800, PCIe/NVMe, Ethernet |
| Connector | USB-C, USB-A |
| Capacity | <256 GB, 256 GB–1 TB, 1–2 TB, 2–4 TB, 4–8 TB, 8–20 TB, 20 TB+ |
| Speed tier | Standard, Fast, Very Fast, Ultra |
| Ruggedness | rugged (+ IP rating, drop height) |
| Bays | 1, 2, 4, 6+ |
| Brand | OWC, LaCie, SanDisk, Samsung, Angelbird, … |
| Price band | derived from price |

Now the rugged portable Thunderbolt SSD is reachable from *every* dimension it
has, and "2 TB Thunderbolt SSD under $250" is just three facets plus the spine.

### Layer 3 — Workflows (curated saved-queries as landing pages)

Facets are powerful but assume the shopper knows the vocabulary. Workflows are
task-first entry points — each one is really a *saved facet query*, so a product
can appear in several without any single-parent contradiction:

| Workflow | Roughly, the saved query |
|---|---|
| **On-Set Capture** | camera cards + rugged pocket drives |
| **Offload & Ingest** | readers, docks, fast bus-powered portable SSDs |
| **Edit Fast** | Thunderbolt/USB4 SSDs, fast RAID (≥400 MB/s) |
| **Backup & Redundancy** | multi-bay / mirrored / desktop HDD |
| **Archive Forever** | LTO tape, optical, large cold HDD |
| **Team & Network** | NAS, 4+ bay arrays, Ethernet storage |

Because they are queries, they never go stale: add a new drive that matches and
it shows up in the right workflows automatically.

---

## 3. How products get assigned (see `scraper/taxonomy.py`)

Assignment is deterministic from the normalized attributes:

1. **Primary** — a single path from media type / form factor / bay count. Any
   unit with ≥2 bays or RAID lands in *Multi-Bay & RAID* regardless of the
   product-line name (this is why a "2big **Dock** … RAID Drive" is filed as a
   2-bay RAID drive, not as a dock).
2. **Facets** — every dimension the extractor found, bucketed for capacity and
   speed.
3. **Workflows** — rule predicates over the facets; a product can match several.

Each assignment carries an **`assignment_confidence`** and a **`needs_review`**
flag. Anything the rules can't place, or whose core attributes were too sparse to
trust, is routed to *Uncategorized → Needs Review* rather than being guessed into
a real category — a wrong home is worse than an honest "review me."

---

## 4. Migrating from the current tree

The current categories don't disappear — they **become facet values or workflow
queries**, which is what most of them already were:

- *Rugged Drives* → `Ruggedness = rugged` facet (and the On-Set Capture workflow)
- *Thunderbolt Drives* → `Interface = Thunderbolt *` facet
- *Portable / Desktop / Internal Drives* → `Form factor` facet under **Drives**
- *Data Archiving* → **Archive & Backup** spine node + Archive Forever workflow
- *USB Flash Drives* → **Drives** (or a Flash facet) + `Interface = USB`
- *Docks / Cables* → **Readers & Docks** / **Cables & Connectivity** spine nodes

So old URLs can 301 to the equivalent facet query, and nothing that ranks today
is orphaned.
