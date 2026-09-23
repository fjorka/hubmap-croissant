#!/usr/bin/env python3
"""
Build a file manifest for a HuBMAP dataset from Globus -- WITHOUT downloading data.

Uses Transfer's `operation_ls`, which returns name/size/type/last_modified per
directory entry and transfers no file content. Listing is non-recursive, so we
walk the tree ourselves, one call per directory.

    pip install globus-sdk

    # 1. find the HuBMAP collection id (once):
    python globus_manifest.py --find-collection

    # 2. walk one dataset:
    python globus_manifest.py --collection <UUID> --dataset HBM695.NCKX.893 \
        -o HBM695.NCKX.893.manifest.json

    # 3. compare what Globus has against HuBMAP's own search-API manifest:
    python globus_manifest.py --collection <UUID> --dataset HBM695.NCKX.893 --compare
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

import globus_sdk
from globus_sdk.scopes import TransferScopes

# Globus's public tutorial/native client. Fine for read-only listing.
# Register your own at https://app.globus.org/settings/developers if you prefer.
CLIENT_ID = "61338d24-54d5-408f-a10d-66c06b59f6d2"
TRANSFER_SCOPE = TransferScopes.all

ENTITY_API = "https://entity.api.hubmapconsortium.org/entities/"
SEARCH_API = "https://search.api.hubmapconsortium.org/v3/portal/search"


# ---------------------------------------------------------------- auth
def login() -> globus_sdk.TransferClient:
    """Native-app login. Opens a URL you paste a code back from."""
    client = globus_sdk.NativeAppAuthClient(CLIENT_ID)
    client.oauth2_start_flow(requested_scopes=TRANSFER_SCOPE)
    print("Open this URL, log in, and paste the code below:\n")
    print("   ", client.oauth2_get_authorize_url(), "\n")
    code = input("code: ").strip()
    tokens = client.oauth2_exchange_code_for_tokens(code)
    tk = tokens.by_resource_server["transfer.api.globus.org"]
    return globus_sdk.TransferClient(
        authorizer=globus_sdk.AccessTokenAuthorizer(tk["access_token"])
    )


# ---------------------------------------------------------------- HuBMAP lookups
def hubmap_entity(identifier: str) -> dict:
    with urllib.request.urlopen(ENTITY_API + identifier, timeout=30) as r:
        return json.load(r)


def hubmap_api_manifest(uuid: str) -> list[dict] | None:
    """The `files` array HuBMAP's search index holds (processed datasets only)."""
    payload = {"query": {"ids": {"values": [uuid]}}, "_source": ["files"], "size": 1}
    req = urllib.request.Request(
        SEARCH_API, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        hits = json.load(r)["hits"]["hits"]
    return hits[0]["_source"].get("files") if hits else None


# ---------------------------------------------------------------- the walk
def walk(tc: globus_sdk.TransferClient, collection: str, root: str) -> list[dict]:
    """Depth-first listing. One operation_ls call per directory. No data moves."""
    out: list[dict] = []
    queue = [root.rstrip("/")]
    ndirs = 0
    while queue:
        d = queue.pop()
        ndirs += 1
        try:
            entries = tc.operation_ls(collection, path=d, show_hidden=True)
        except globus_sdk.TransferAPIError as e:
            print(f"  ! {d}: {e.code} {e.message}", file=sys.stderr)
            continue
        for e in entries:
            full = f"{d}/{e['name']}"
            if e["type"] == "dir":
                queue.append(full)
            else:
                out.append({
                    "rel_path": full[len(root.rstrip('/')) + 1:],
                    "size": e.get("size"),
                    "type": e.get("type"),
                    "last_modified": e.get("last_modified"),
                })
        print(f"\r  dirs listed: {ndirs}  files: {len(out)}", end="", file=sys.stderr)
    print(file=sys.stderr)
    return out


# ---------------------------------------------------------------- compare
def compare(globus_files: list[dict], api_files: list[dict]) -> None:
    g = {f["rel_path"]: f.get("size") for f in globus_files}
    a = {f["rel_path"]: f.get("size") for f in api_files}
    only_g, only_a = sorted(set(g) - set(a)), sorted(set(a) - set(g))
    mismatch = [(p, g[p], a[p]) for p in set(g) & set(a) if g[p] != a[p]]

    print(f"\nGlobus: {len(g)} files   HuBMAP API: {len(a)} files")
    print(f"  only in Globus:   {len(only_g)}")
    print(f"  only in API:      {len(only_a)}")
    print(f"  size mismatches:  {len(mismatch)}")
    for label, rows in (("ONLY IN GLOBUS", only_g), ("ONLY IN API", only_a)):
        if rows:
            print(f"\n  --- {label} (first 15) ---")
            for p in rows[:15]:
                print("     ", p)
    if mismatch:
        print("\n  --- SIZE MISMATCH (first 15) ---")
        for p, gs, as_ in mismatch[:15]:
            print(f"      {p}\n        globus={gs}  api={as_}")
    if not (only_g or only_a or mismatch):
        print("\n  IDENTICAL -- the API manifest exactly describes what Globus serves.")


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--find-collection", action="store_true",
                    help="Search Globus for HuBMAP collections and exit.")
    ap.add_argument("--collection", help="Globus collection UUID.")
    ap.add_argument("--dataset", help="HuBMAP ID or UUID.")
    ap.add_argument("-o", "--outfile", help="Write the manifest JSON here.")
    ap.add_argument("--compare", action="store_true",
                    help="Diff the Globus listing against HuBMAP's search-API manifest.")
    a = ap.parse_args()

    tc = login()

    if a.find_collection:
        for ep in tc.endpoint_search("hubmap", filter_scope="all", limit=25):
            print(f"{ep['id']}  {ep['display_name']}  (owner: {ep.get('owner_string')})")
        return 0

    if not (a.collection and a.dataset):
        ap.error("--collection and --dataset are required (or use --find-collection)")

    ent = hubmap_entity(a.dataset)
    root = "/" + ent["local_directory_rel_path"].strip("/")
    print(f"{ent['hubmap_id']}  ({ent['uuid']})")
    print(f"  access: {ent.get('data_access_level')}   status: {ent.get('status')}")
    print(f"  path:   {root}\n")

    files = walk(tc, a.collection, root)
    total = sum(f["size"] or 0 for f in files)
    print(f"\n{len(files)} files, {total/1e9:.2f} GB  (nothing downloaded)")

    if a.outfile:
        json.dump({"hubmap_id": ent["hubmap_id"], "uuid": ent["uuid"],
                   "root": root, "files": files},
                  open(a.outfile, "w"), indent=2)
        print(f"[write] {a.outfile}")

    if a.compare:
        api = hubmap_api_manifest(ent["uuid"])
        if api is None:
            print("\nNo `files` manifest in the search index "
                  "(expected for raw / non-Central-Process datasets).")
        else:
            compare(files, api)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
