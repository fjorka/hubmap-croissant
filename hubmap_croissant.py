"""
hubmap_croissant.py
===================
Generate a **metadata-only** Croissant (JSON-LD) for a HuBMAP dataset, straight from
HuBMAP's public API — no file downloads, and (for now) no Croissant Baker.

Public API:
    build_croissant(identifier) -> dict     # the Croissant, as a Python dict
    validate(croissant_dict)    -> None      # raises if it isn't valid Croissant 1.1

`identifier` is any HuBMAP dataset ID (e.g. "HBM279.TQRS.775") or UUID. Published datasets
need no token. Everything here is deterministic field-to-field mapping — no LLM.

What we can build from the API alone (no files): the Metadata layer (name, creators+ORCID,
DOI, license, keywords), assay/instrument/protocol facts, donor + organ + spatial context,
and a native embedded **W3C PROV-O** lineage (acquisition → specimen chain → donor, and, for
processed datasets, the git-pinned pipeline). What we CANNOT build without the files: the
Resource layer (FileObjects) and Structure layer (RecordSets / the antibody panel) — those
need the data files and Croissant Baker, which is a later version.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone, date

ENTITY_API = "https://entity.api.hubmapconsortium.org"
SEARCH_API = "https://search.api.hubmapconsortium.org/v3/portal/search"
PORTAL = "https://portal.hubmapconsortium.org/browse/dataset"
HUBMAP_LICENSE = "https://creativecommons.org/licenses/by/4.0/"
HUBMAP = "https://hubmapconsortium.org/"
ENTITY_BASE = "https://portal.hubmapconsortium.org/browse/"

# HuBMAP two-letter organ codes -> (label, UBERON term)
ORGAN_MAP = {
    "LY": ("Lymph Node", "UBERON:0000029"), "SP": ("Spleen", "UBERON:0002106"),
    "TH": ("Thymus", "UBERON:0002370"), "BM": ("Bone Marrow", "UBERON:0002371"),
    "LK": ("Kidney (left)", "UBERON:0004538"), "RK": ("Kidney (right)", "UBERON:0004539"),
    "HT": ("Heart", "UBERON:0000948"), "LI": ("Large Intestine", "UBERON:0000059"),
    "SI": ("Small Intestine", "UBERON:0002108"), "LL": ("Lung (left)", "UBERON:0002168"),
    "RL": ("Lung (right)", "UBERON:0002167"), "LV": ("Liver", "UBERON:0002107"),
    "PA": ("Pancreas", "UBERON:0001264"), "BL": ("Bladder", "UBERON:0001255"),
    "SK": ("Skin", "UBERON:0002097"),
}


# ----------------------------------------------------------------- HuBMAP API

def _get_json(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def fetch_entity(identifier: str) -> dict:
    """The Dataset entity (descriptive metadata). Public/Published datasets need no token."""
    e = _get_json(f"{ENTITY_API}/entities/{identifier}")
    if e.get("entity_type") != "Dataset":
        print(f"[warn] {identifier} is entity_type={e.get('entity_type')!r}, expected Dataset",
              file=sys.stderr)
    return e


def fetch_context(uuid: str) -> dict:
    """Donor + organ + specimen chain + spatial registration, from the search index."""
    payload = {"query": {"term": {"uuid": uuid}}, "_source": [
        "donor.mapped_metadata", "ancestors.sample_category", "ancestors.organ",
        "ancestors.entity_type", "ancestors.hubmap_id", "ancestors.rui_location"]}
    try:
        hits = _get_json(SEARCH_API, payload).get("hits", {}).get("hits", [])
        return hits[0]["_source"] if hits else {}
    except Exception as exc:
        print(f"[warn] context fetch failed: {exc}", file=sys.stderr)
        return {}


def fetch_processed_descendants(uuid: str) -> list[dict]:
    """Processed/derived datasets produced from this raw dataset (their pipelines live here)."""
    payload = {"query": {"term": {"ancestor_ids": uuid}},
               "_source": ["hubmap_id", "dataset_type", "creation_action",
                           "ingest_metadata.dag_provenance_list"], "size": 25}
    try:
        hits = _get_json(SEARCH_API, payload).get("hits", {}).get("hits", [])
    except Exception as exc:
        print(f"[warn] descendants fetch failed: {exc}", file=sys.stderr)
        return []
    out = []
    for h in hits:
        s = h["_source"]
        dag = (s.get("ingest_metadata") or {}).get("dag_provenance_list", [])
        if dag:
            out.append({"hubmap_id": s.get("hubmap_id"), "dataset_type": s.get("dataset_type"),
                        "creation_action": s.get("creation_action"), "dag": dag})
    return out


# ----------------------------------------------------------------- helpers

def _md(entity: dict) -> dict:
    """The assay/instrument metadata block (handles one level of nesting)."""
    md = entity.get("metadata") or {}
    if isinstance(md.get("metadata"), dict):
        md = md["metadata"]
    return md


def _ts_to_iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat() if ms else None


def _creators(entity: dict) -> list[dict]:
    out = []
    for c in entity.get("contributors", []):
        name = c.get("name") or f"{c.get('first_name','')} {c.get('last_name','')}".strip()
        if not name:
            continue
        person = {"name": name}
        orcid = (c.get("orcid_id") or c.get("orcid") or "").strip()
        if orcid:
            person["url"] = orcid if orcid.startswith("http") else f"https://orcid.org/{orcid}"
        out.append(person)
    return out


def _organ(context: dict):
    for anc in context.get("ancestors", []) or []:
        if anc.get("organ"):
            return ORGAN_MAP.get(anc["organ"], (anc["organ"], None))
    return None, None


def _protocol_dois(md: dict) -> list[str]:
    dois = []
    for k in ("preparation_protocol_doi", "reagent_prep_protocols_io_doi",
              "section_prep_protocols_io_doi"):
        v = (md.get(k) or "").strip()
        if not v:
            continue
        dois.append(f"https://dx.doi.org/{v}" if v.startswith("10.") else v)
    return dois


def _keywords(entity: dict, context: dict, md: dict) -> list[str]:
    kws: list[str] = []
    for v in [entity.get("dataset_type"), md.get("assay_category"), md.get("analyte_class")]:
        if v and v not in kws:
            kws.append(v)
    organ_label, uberon = _organ(context)
    if organ_label:
        kws.append(organ_label)
        if uberon:
            kws.append(uberon)
    for anc in context.get("ancestors", []) or []:
        rui = anc.get("rui_location")
        if rui:
            r = json.loads(rui) if isinstance(rui, str) else rui
            for term in r.get("ccf_annotations", []) or []:
                short = "UBERON:" + term.rsplit("_", 1)[-1] if "UBERON" in term else term
                if short not in kws:
                    kws.append(short)
            break
    kws += ["Homo sapiens", "NCBITaxon:9606", "HuBMAP"]
    return kws


def _build_citation(entity: dict, doi) -> str:
    authors = "; ".join(c["name"] for c in _creators(entity)) or "HuBMAP Consortium"
    year = (_ts_to_iso(entity.get("published_timestamp")) or "")[:4]
    title = entity.get("title") or entity.get("hubmap_id", "")
    return f"{authors} ({year}). {title}. HuBMAP Consortium." + (f" {doi}" if doi else "")


def map_metadata(entity: dict, context: dict, md: dict) -> dict:
    """The Croissant Metadata layer, as plain kwargs."""
    hubmap_id = entity.get("hubmap_id", entity.get("uuid", "unknown"))
    doi = entity.get("doi_url") or (
        f"https://doi.org/{entity['registered_doi']}" if entity.get("registered_doi") else None)
    kwargs = {
        "name": f"HuBMAP {hubmap_id}",
        "description": entity.get("description") or entity.get("dataset_info") or hubmap_id,
        "url": doi or f"{PORTAL}/{entity.get('uuid','')}",
        "license": HUBMAP_LICENSE,
        "citation": _build_citation(entity, doi),
        "version": "1.0.0",
        "date_published": _ts_to_iso(entity.get("published_timestamp")),
        "creators": _creators(entity),
        "publisher": entity.get("group_name") or "HuBMAP Consortium",
        "keywords": _keywords(entity, context, md),
        "same_as": doi,
    }
    return {k: v for k, v in kwargs.items() if v}


# ----------------------------------------------------------------- RAI (free-text)

def _donor_text(context: dict):
    dm = (context.get("donor") or {}).get("mapped_metadata", {}) or {}
    def first(k):
        v = dm.get(k)
        return v[0] if isinstance(v, list) and v else v
    parts = []
    if first("age_value"):
        parts.append(f"{first('age_value')} {first('age_unit') or 'years'}-old")
    if first("race"):
        parts.append(str(first("race")))
    if first("sex"):
        parts.append(str(first("sex")).lower())
    donor = " ".join(parts)
    extras = [f"{lbl} {first(k)}" for lbl, k in
              [("BMI", "body_mass_index_value"), ("blood group", "abo_blood_group_system"),
               ("cause of death", "cause_of_death")] if first(k)]
    chain = [a.get("sample_category") for a in context.get("ancestors", []) if a.get("sample_category")]
    txt = ""
    if donor:
        txt += f"Donor: {donor}" + (f" ({'; '.join(extras)})" if extras else "") + ". "
    if chain:
        txt += "Specimen chain: " + " <- ".join(chain) + ". "
    return txt, bool(donor)


def build_rai_fields(entity: dict, context: dict, md: dict, hubmap_id: str) -> dict:
    donor_text, is_human = _donor_text(context)
    organ_label, _ = _organ(context)
    assay = entity.get("dataset_type", "assay")
    instr = " / ".join(filter(None, [
        f"{md.get('acquisition_instrument_vendor','')} {md.get('acquisition_instrument_model','')}".strip(),
        f"prep: {md.get('preparation_instrument_vendor','')} {md.get('preparation_instrument_model','')}".strip()]))
    params = ", ".join(f"{k}={md[k]}" for k in
                       ("number_of_antibodies", "number_of_biomarker_imaging_rounds",
                        "number_of_channels", "is_targeted", "analyte_class") if md.get(k))
    collection = (
        f"{assay} on {organ_label.lower() if organ_label else 'tissue'}. {donor_text}"
        f"Instrument: {instr}. Assay parameters: {params}. "
        f"Full machine-readable provenance (W3C PROV) at "
        f"{ENTITY_API}/entities/{hubmap_id}/provenance.").strip()
    rai = {"data_collection": collection, "data_collection_type": "Direct measurement",
           "data_collection_raw_data": f"Raw {assay} instrument output on the HuBMAP portal."}
    if is_human:
        rai["personal_sensitive_information"] = (
            "Human donor data; direct identifiers removed. De-identified demographic/clinical "
            "attributes provided under HuBMAP human-subjects governance.")
        rai["data_limitations"] = ("Single donor, single tissue section — not representative of "
                                   "population variation.")
    return rai


# ------------------------------------------- native embedded W3C PROV-O lineage

def is_processed(entity: dict) -> bool:
    """Raw vs processed: `creation_action` is 'Create Dataset Activity' vs 'Central Process'."""
    return "process" in (entity.get("creation_action") or "").lower()


def raw_ancestor_id(entity: dict):
    for a in entity.get("direct_ancestors", []) or []:
        if a.get("entity_type") == "Dataset":
            return a.get("hubmap_id") or a.get("uuid")
    return None


def _own_dag(entity: dict) -> list:
    return (entity.get("ingest_metadata") or {}).get("dag_provenance_list", [])


def _pipeline_steps(dag_list: list) -> list[dict]:
    steps, seen = [], set()
    for s in dag_list or []:
        repo = (s.get("origin") or "").strip().replace(".git", "")
        name = repo.rsplit("/", 1)[-1] if repo else (s.get("name") or "")
        commit = (s.get("hash") or "")[:7]
        cwl = s.get("name") or ""
        key = (name, commit, cwl)
        if not name or key in seen:
            continue
        seen.add(key)
        steps.append({"name": name, "repo": repo, "commit": commit, "cwl": cwl})
    return steps


def _acquisition_activity(entity: dict, md: dict) -> dict:
    def agent(name, role=None, org=False):
        n = {"@type": "prov:Organization" if org else "prov:Person", "schema:name": name}
        if role:
            n["prov:role"] = role
        return n
    assoc = []
    if md.get("pi"):
        assoc.append(agent(md["pi"], role="principal investigator"))
    if md.get("operator"):
        assoc.append(agent(md["operator"], role="operator"))
    if entity.get("group_name"):
        assoc.append(agent(entity["group_name"], org=True))
    act = {
        "@type": "prov:Activity",
        "schema:name": f"{entity.get('dataset_type', 'assay')} acquisition",
        "hubmap:instrument": " ".join(filter(None, [md.get("acquisition_instrument_vendor"),
                                                    md.get("acquisition_instrument_model")])),
        "hubmap:numberOfAntibodies": md.get("number_of_antibodies"),
        "hubmap:numberOfImagingRounds": md.get("number_of_biomarker_imaging_rounds"),
        "hubmap:numberOfChannels": md.get("number_of_channels"),
        "prov:wasAssociatedWith": assoc,
    }
    if md.get("execution_datetime"):
        act["prov:startedAtTime"] = md["execution_datetime"]
    protocols = [{"@type": ["prov:Entity", "schema:CreativeWork"], "@id": d, "prov:role": "protocol"}
                 for d in _protocol_dois(md)]
    if protocols:
        act["prov:used"] = protocols
    return {k: v for k, v in act.items() if v not in (None, "", [])}


def _entity_iri(e: dict) -> str:
    """A resolvable IRI for a HuBMAP entity (dataset / sample / donor).

    Prefers the registered DOI when the entity has one (raw datasets do; samples,
    donors and processed datasets generally don't), otherwise the portal browse URL,
    which resolves by HuBMAP ID for every entity type. NOTE: `HUBMAP` is the JSON-LD
    vocabulary namespace and is NOT dereferenceable -- do not mint entity @ids from it.
    """
    doi = e.get("doi_url") or (
        f"https://doi.org/{e['registered_doi']}" if e.get("registered_doi") else None)
    return doi or ENTITY_BASE + (e.get("hubmap_id") or "")


def _specimen_chain(context: dict):
    def node(anc):
        n = {"@type": "prov:Entity", "@id": _entity_iri(anc),
             "schema:name": anc.get("hubmap_id"), "hubmap:entityType": anc.get("entity_type"),
             "hubmap:sampleCategory": anc.get("sample_category")}
        rui = anc.get("rui_location")
        if rui:
            r = json.loads(rui) if isinstance(rui, str) else rui
            n["hubmap:ccfAnnotations"] = r.get("ccf_annotations")
            n["hubmap:dimensions"] = {"x": r.get("x_dimension"), "y": r.get("y_dimension"),
                                      "z": r.get("z_dimension"), "unit": r.get("dimension_units")}
        return {k: v for k, v in n.items() if v not in (None, "", [])}
    order = {"section": 0, "block": 1, "organ": 2}
    ancs = [a for a in context.get("ancestors", []) if a.get("entity_type") in ("Sample", "Donor")]
    ancs.sort(key=lambda a: order.get(a.get("sample_category"), 4))
    derived = None
    for anc in reversed(ancs):
        n = node(anc)
        if derived:
            n["prov:wasDerivedFrom"] = derived
        derived = n
    return derived


def _pipeline_activity(dag_list: list) -> dict:
    agents = []
    for st in _pipeline_steps(dag_list):
        a = {"@type": ["prov:SoftwareAgent", "schema:SoftwareApplication"],
             "schema:name": st["name"] + (f" [{st['cwl']}]" if st["cwl"] else ""),
             "schema:codeRepository": st["repo"], "hubmap:commit": st["commit"]}
        agents.append({k: v for k, v in a.items() if v})
    act = {"@type": "prov:Activity", "schema:name": "HuBMAP uniform processing pipeline"}
    if agents:
        act["prov:wasAssociatedWith"] = agents
    return act


def build_embedded_provenance(entity, context, md, descendants=None, raw_entity=None, raw_md=None) -> dict:
    """PROCESSED subject: wasGeneratedBy its own pipeline; wasDerivedFrom the raw parent
    (which carries the acquisition activity + specimen chain). RAW subject: wasGeneratedBy
    acquisition; wasDerivedFrom specimen chain; + a light forward pointer to processed versions."""
    if is_processed(entity) and raw_entity is not None:
        raw_node = {"@type": "prov:Entity", "@id": _entity_iri(raw_entity),
                    "schema:name": raw_entity.get("hubmap_id"),
                    "hubmap:datasetType": raw_entity.get("dataset_type"),
                    "prov:wasGeneratedBy": _acquisition_activity(raw_entity, raw_md or {}),
                    "prov:wasDerivedFrom": _specimen_chain(context)}
        raw_node = {k: v for k, v in raw_node.items() if v}
        return {"prov:wasGeneratedBy": _pipeline_activity(_own_dag(entity)),
                "prov:wasDerivedFrom": raw_node}
    provo = {"prov:wasGeneratedBy": _acquisition_activity(entity, md)}
    chain = _specimen_chain(context)
    if chain:
        provo["prov:wasDerivedFrom"] = chain
    if descendants:
        provo["hubmap:hasProcessedDataset"] = [
            {"@type": "prov:Entity", "@id": _entity_iri(d),
             "schema:name": d.get("hubmap_id"), "hubmap:datasetType": d.get("dataset_type")}
            for d in descendants]
    return provo


# ----------------------------------------------------------------- public API

def _assemble(entity: dict, context: dict, md: dict, descendants: list, raw_entity, raw_md) -> dict:
    import mlcroissant as mlc
    meta = map_metadata(entity, context, md)
    rai = build_rai_fields(entity, context, md, entity.get("hubmap_id", ""))

    def as_date(s):
        try:
            return date.fromisoformat(s) if s else None
        except ValueError:
            return None

    kwargs = {
        "name": meta["name"], "description": meta["description"], "url": meta.get("url"),
        "license": meta.get("license"), "cite_as": meta.get("citation"),
        "version": meta.get("version"), "date_published": as_date(meta.get("date_published")),
        "keywords": meta.get("keywords"),
        "creators": [mlc.Person(name=c["name"], url=c.get("url")) for c in meta.get("creators", [])],
        "publisher": [mlc.Organization(name=meta["publisher"])] if meta.get("publisher") else None,
        "same_as": [meta["same_as"]] if meta.get("same_as") else None,
    }
    kwargs.update(rai)
    kwargs = {k: v for k, v in kwargs.items() if v}
    d = mlc.Metadata(**kwargs).to_json()

    ctx = d.get("@context", {})
    if isinstance(ctx, dict):
        ctx.setdefault("prov", "http://www.w3.org/ns/prov#")
        ctx.setdefault("schema", "https://schema.org/")
        ctx.setdefault("hubmap", HUBMAP)
        d["@context"] = ctx
    d.update(build_embedded_provenance(entity, context, md, descendants, raw_entity, raw_md))
    d["conformsTo"] = "http://mlcommons.org/croissant/1.1"
    if entity.get("dataset_type"):
        d["schema:measurementTechnique"] = entity["dataset_type"]
    prot = _protocol_dois(md)
    if prot:
        d["schema:isBasedOn"] = prot
    return d


def build_croissant(identifier: str) -> dict:
    """Build a metadata-only Croissant (dict) for a HuBMAP dataset ID or UUID.

    No files are read and Croissant Baker is not used — this is the API-only version.
    Automatically handles raw vs processed datasets (a processed dataset's provenance links
    back to its raw parent and its assay parameters are inherited from it)."""
    entity = fetch_entity(identifier)
    uuid = entity.get("uuid", identifier)
    context = fetch_context(uuid)
    raw_entity = raw_md = None
    descendants = []
    if is_processed(entity):
        raw_id = raw_ancestor_id(entity)
        raw_entity = fetch_entity(raw_id) if raw_id else None
        raw_md = _md(raw_entity) if raw_entity else {}
        md = {**(raw_md or {}), **_md(entity)}          # inherit assay params from the raw parent
    else:
        md = _md(entity)
        descendants = fetch_processed_descendants(uuid)
    return _assemble(entity, context, md, descendants, raw_entity, raw_md)


def validate(croissant: dict) -> None:
    """Raise if `croissant` is not a valid Croissant 1.1 document (via mlcroissant)."""
    import mlcroissant as mlc
    mlc.Dataset(jsonld=croissant)
