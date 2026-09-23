# hubmap-croissant

A small, informal repo for the team: **turn a HuBMAP dataset into a [Croissant](https://mlcommons.org/croissant)
metadata file** (JSON-LD), from HuBMAP's public API. Give it a dataset ID, get a `.jsonld` back.

```bash
python generate.py HBM279.TQRS.775
```

> **Heads-up — this is the metadata-only version.** It builds the Croissant purely from the
> HuBMAP API: **no data files are downloaded, and it does *not* use [Croissant Baker](https://github.com/mlcommons/croissant) yet.**
> That's a deliberate stage — everything here comes from metadata we can read without touching the
> actual imaging/data files. See ["What this can and can't do"](#what-this-can-and-cant-do) below.

## Why this exists

We want HuBMAP datasets to carry Croissant metadata so they're legible to ML tooling (and to the
Croissant viewer). The catch is that a *complete* Croissant has layers that describe the **files**
themselves (the "Resource" and "Structure" layers — file lists, record schemas, the antibody panel),
and those genuinely need the data files + Croissant Baker to build.

But a large and useful part of a Croissant needs **none** of that. All the descriptive metadata,
the assay/instrument facts, the donor/organ/spatial context, and the full **W3C PROV-O provenance
lineage** already live in HuBMAP's API. This repo builds exactly that part — a valid Croissant 1.1
you can generate for any dataset ID in one call, with nothing downloaded. It's the honest floor we
can stand on before the Baker stage.

## Install

```bash
uv sync                              # or: pip install -e .
```

Python 3.11+ (set in `pyproject.toml`). Published HuBMAP datasets are public, so
**no token is needed**.

## Use

**CLI** — pass any HuBMAP dataset ID (or UUID); nothing is hard-coded:

```bash
python generate.py HBM279.TQRS.775                        # writes croissant_HBM279.TQRS.775.jsonld
python generate.py HBM279.TQRS.775 HBM653.RRCF.859 --outdir examples
python generate.py HBM279.TQRS.775 --no-validate         # skip mlcroissant validation
```

**As a library** — two functions, that's the whole API:

```python
from hubmap_croissant import build_croissant, validate

croissant = build_croissant("HBM279.TQRS.775")   # -> dict
validate(croissant)                               # raises if it isn't valid Croissant 1.1
```

`build_croissant(identifier)` figures out on its own whether the dataset is **raw** or **processed**:
a processed dataset's provenance links back to its raw parent, and it inherits the raw parent's assay
parameters. You just hand it the ID.

## What this can and can't do

**Built from the API alone (what's in here):**

- **Metadata layer** — name, description, DOI/URL, license, creators + ORCID, citation, keywords.
- **Assay / instrument / protocol** facts — technique, instrument make+model, protocol DOIs.
- **Donor + organ + spatial context** — de-identified donor demographics, organ (with UBERON term),
  the specimen chain, and RUI/CCF spatial registration — surfaced in the Responsible-AI fields.
- **Native W3C PROV-O provenance**, embedded in the Croissant: acquisition activity → specimen chain
  → donor, and for processed datasets the git-pinned **processing pipeline** (repo + commit per step),
  with `wasDerivedFrom` pointing back at the raw parent.

**Needs the data files + Croissant Baker (NOT in here — a later version):**

- **Resource layer** — the actual `FileObject`s / `FileSet`s (the file inventory).
- **Structure layer** — `RecordSet`s / field schemas, including the **antibody panel** with the gene
  names. That list lives inside the data files, so it needs the files, not just the API.

So: this produces a real, valid Croissant that describes the dataset thoroughly — it just stops short
of the file-level layers, on purpose, because those can't be built without the files.

## Examples

`examples/` has three generated outputs:

- `croissant_HBM279.TQRS.775.jsonld` — a **raw** CODEX dataset (acquisition + specimen chain).
- `croissant_HBM653.RRCF.859.jsonld` — a **processed** dataset derived from it (pipeline provenance +
  `wasDerivedFrom` the raw parent).
- `croissant_HBM236.WBFT.443.jsonld` — a second **processed** CODEX dataset, kept because an
  independently generated Croissant exists for the same dataset to compare against.

Regenerate them any time:

```bash
python generate.py HBM279.TQRS.775 HBM653.RRCF.859 HBM236.WBFT.443 --outdir examples
```

## Files

| file | what it is |
|------|------------|
| `hubmap_croissant.py` | the library — all the HuBMAP→Croissant mapping logic |
| `generate.py` | CLI wrapper (accepts identifiers, writes `.jsonld` files) |
| `examples/` | checked-in sample outputs |
| `pyproject.toml` | project metadata + dependencies (`mlcroissant`, `globus-sdk`) |

## Notes

Everything here is deterministic field-to-field mapping — **no LLM** in the generation path. If a
dataset is missing a field, we drop it rather than guess. The one place a language model could help
later is summarizing free-text, but that's not wired in.

Next stage (not here yet): run **Croissant Baker** over a staged dataset to add the Resource +
Structure layers on top of this metadata core.
