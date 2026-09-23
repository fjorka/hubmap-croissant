#!/usr/bin/env python3
"""
Add an HONEST catalog layer (Resource + a minimal Structure layer) to the
metadata-only Croissant produced by hubmap_croissant.py.

Honest means: we inventory every file group, and we declare a RecordSet ONLY
where mlcroissant can actually serve it correctly today. See NOTES at the end.

    python build_catalog_layer.py examples/croissant_HBM279.TQRS.775.jsonld \
        -o examples/croissant_HBM279.TQRS.775.catalog.jsonld
"""
import argparse, json, pathlib

UUID = "077f7862f6306055899374c7807a30c3"
# Real sha256 digests, computed from the published files. These pin the metadata
# to exact bytes -- the thing that stops a view going silently stale.
SHA256 = {
    "assay-metadata-tsv":   "8df27cfdfbbeb0d1f9bf78a668f538483313407068aa0d7480203c9647b0c5b1",
    "channel-names":        "473aed4ed81b3153b6f2f4c132e297d5f723033bba1e517816a0b4f010998cf6",
    "experiment-config":    "cb72567e5c058010dfdd903442a6a52c78aace1feb8fdc6a2071be66ae06e43c",
    "segmentation-config":  "a27372a598b9111d45d676f33beb0455c6abdaf115184f948fd759d8ce5f4d40",
    "exposure-times":       "7e3b41e5ff1abdafd703502d3ffcb64578cbb329771f62efe058a2a23150a00a",
    "antibody-panel-xlsx":  "fab1c7adc645cd992b0538fe030ac9097e6a6044cc0d708589596ce128aa0171",
}
# A directory has no digest. Hugging Face's generator uses the same placeholder.
NO_DIGEST = "https://github.com/mlcommons/croissant/issues/80"
ASSETS = f"https://assets.hubmapconsortium.org/{UUID}"

SRC = "src_CX_19-002_LN_R2"
DRV = "drv_CX_19-002_lymph-node_R2/processed_2020-02-18"


def file_object(id_, name, path, fmt, description, **extra):
    obj = {
        "@type": "cr:FileObject", "@id": id_, "name": name,
        "description": description,
        "contentUrl": f"{ASSETS}/{path}",
        "encodingFormat": fmt,
        "sha256": SHA256[id_],
    }
    obj.update(extra)
    return obj


def file_set(id_, name, includes, fmt, description):
    return {
        "@type": "cr:FileSet", "@id": id_, "name": name,
        "description": description,
        "containedIn": {"@id": "dataset-archive"},
        "encodingFormat": fmt,
        "includes": includes,
    }


DISTRIBUTION = [
    # --- the archive itself -------------------------------------------------
    {
        "@type": "cr:FileObject", "@id": "dataset-archive",
        "name": "dataset-archive",
        "description": (
            "The published HuBMAP dataset directory on the HuBMAP asset service. "
            "62 GB total: 38 GB raw CODEX acquisition, 25 GB processed derivatives."
        ),
        "contentUrl": ASSETS,
        "encodingFormat": "text/html",
        "sha256": NO_DIGEST,
    },

    # --- small text/tabular files: these ARE cheaply readable ---------------
    file_object(
        "assay-metadata-tsv", "assay-metadata-tsv", "codex-akoya-metadata.tsv",
        "text/tab-separated-values",
        "One-row TSV of assay-level acquisition parameters: instrument make/model, "
        "x/y/z resolution and units, antibody/channel/cycle counts, protocol DOIs.",
    ),
    file_object(
        "channel-names", "channel-names", f"{SRC}/channelnames.txt", "text/plain",
        "36 lines, one per (cycle, channel) acquisition slot, in acquisition order. "
        "Values are marker names (DAPI-01, CD31, CD8, CD45, ...) or 'Blank'.",
    ),
    file_object(
        "experiment-config", "experiment-config", f"{SRC}/experiment.json",
        "application/json",
        "Akoya CODEX acquisition configuration: region/tile grid, z-planes, "
        "exposure and objective settings.",
    ),
    file_object(
        "segmentation-config", "segmentation-config", f"{SRC}/segmentation.json",
        "application/json",
        "Segmentation parameters used by the CODEX processing pipeline.",
    ),
    file_object(
        "exposure-times", "exposure-times", f"{SRC}/exposure_times.txt", "text/plain",
        "Per-cycle, per-channel exposure times.",
    ),
    file_object(
        "antibody-panel-xlsx", "antibody-panel-xlsx",
        "Pre FEB2020_Antibody_Panel1_UF .xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Antibody panel workbook (18 antibodies): clone, vendor, catalogue number, "
        "conjugated barcode, dilution. Marker-to-gene mapping lives here.",
    ),

    # --- image file sets: inventoried, NOT declared as RecordSets -----------
    file_set(
        "raw-acquisition-tiles", "raw-acquisition-tiles",
        f"{SRC}/cyc*_reg*/*.tif", "image/tiff",
        "9,000 raw Keyence BZ-X800 TIFFs, 38 GB. 9 cycles x 1 region x 4 channels "
        "x tile positions x z-planes. Each file 1920x1440, 16-bit, LZW-compressed, "
        "single plane. Filename pattern <tile>_<NNNNN>_Z<zzz>_CH<c>.tif.",
    ),
    file_set(
        "processed-tiles", "processed-tiles",
        f"{DRV}/tiles/reg*_X*_Y*/*.tif", "image/tiff",
        "2,268 flat-field-corrected, drift-compensated, best-focus tiles, 16-bit. "
        "Filename pattern reg<r>_X<xx>_Y<yy>_t<ttt>_z<zzz>_c<ccc>.tif.",
    ),
    file_set(
        "stitched-channel-images", "stitched-channel-images",
        f"{DRV}/stitched/reg001/*.tif", "image/tiff",
        "37 stitched whole-region mosaics, one per (cycle, channel) slot. "
        "Each 9408x9072, 16-bit, uncompressed, ~170 MB. The marker name is encoded "
        "in the filename: reg<r>_cyc<cc>_ch<c>_<MARKER>.tif. This is the layer most "
        "ML work would start from.",
    ),
    file_set(
        "segmentation-masks", "segmentation-masks",
        f"{DRV}/segm/segm-1/masks/reg*_X*_Y*/*.png", "image/png",
        "63 per-tile segmentation region masks (PNG), one per processed tile "
        "position. Filename pattern regions_reg<r>_X<xx>_Y<yy>_Z<zz>.png.",
    ),
    file_set(
        "segmentation-features", "segmentation-features",
        f"{DRV}/segm/segm-1/fcs/*.txt", "text/plain",
        "196 per-tile segmentation outputs: per-cell feature tables and Gabriel "
        "graph neighbour lists.",
    ),
    file_set(
        "pipeline-diagnostics", "pipeline-diagnostics",
        f"{DRV}/diagnostics/**/*", "image/tiff",
        "1,734 quality-control artefacts from the processing pipeline (flat-field "
        "estimates, focal maps, tile registration reports). Provenance evidence, "
        "not analysis input.",
    ),
]

# Only ONE RecordSet: the one mlcroissant can serve correctly and cheaply.
RECORD_SETS = [
    {
        "@type": "cr:RecordSet", "@id": "assay_parameters",
        "name": "assay_parameters",
        "description": (
            "Assay-level acquisition parameters, one record. Read directly from "
            "codex-akoya-metadata.tsv."
        ),
        "field": [
            {"@type": "cr:Field", "@id": "assay_parameters/assay_type",
             "name": "assay_parameters/assay_type", "dataType": "sc:Text",
             "description": "Assay type, e.g. CODEX.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "assay_type"}}},
            {"@type": "cr:Field", "@id": "assay_parameters/instrument_model",
             "name": "assay_parameters/instrument_model", "dataType": "sc:Text",
             "description": "Acquisition instrument model.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "acquisition_instrument_model"}}},
            {"@type": "cr:Field", "@id": "assay_parameters/resolution_x_value",
             "name": "assay_parameters/resolution_x_value", "dataType": "sc:Float",
             "description": "Lateral pixel size in x.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "resolution_x_value"}}},
            {"@type": "cr:Field", "@id": "assay_parameters/resolution_x_unit",
             "name": "assay_parameters/resolution_x_unit", "dataType": "sc:Text",
             "description": "Unit for resolution_x_value, e.g. nm.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "resolution_x_unit"}}},
            {"@type": "cr:Field", "@id": "assay_parameters/resolution_z_value",
             "name": "assay_parameters/resolution_z_value", "dataType": "sc:Float",
             "description": "Axial step size in z.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "resolution_z_value"}}},
            {"@type": "cr:Field", "@id": "assay_parameters/number_of_antibodies",
             "name": "assay_parameters/number_of_antibodies", "dataType": "sc:Integer",
             "description": "Number of antibodies in the panel.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "number_of_antibodies"}}},
            {"@type": "cr:Field", "@id": "assay_parameters/number_of_cycles",
             "name": "assay_parameters/number_of_cycles", "dataType": "sc:Integer",
             "description": "Number of CODEX cycles.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "number_of_cycles"}}},
            {"@type": "cr:Field", "@id": "assay_parameters/number_of_channels",
             "name": "assay_parameters/number_of_channels", "dataType": "sc:Integer",
             "description": "Fluorescence channels per cycle.",
             "source": {"fileObject": {"@id": "assay-metadata-tsv"},
                        "extract": {"column": "number_of_channels"}}},
        ],
    },
]

NOTE = (
    "RESOURCE LAYER ONLY for the imaging file sets. Every image file set above is "
    "inventoried and addressable, but no cr:RecordSet is declared over it, and that "
    "is deliberate rather than incomplete. Two current mlcroissant limitations make "
    "an image RecordSet here dishonest: (1) the image/tiff reader converts pixels "
    "with (tifffile.imread(f) * 255).astype('uint8'), which silently corrupts 16-bit "
    "data -- every TIFF in this dataset is 16-bit; (2) the reader materialises full "
    "pixel arrays whether or not a field requests cr:content, so even a "
    "filename-only RecordSet over stitched-channel-images would read 6 GB. Consumers "
    "should read these files with a scientific imaging library (tifffile, bioio) "
    "using the contentUrl and filename conventions described above."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile", type=pathlib.Path)
    ap.add_argument("-o", "--outfile", type=pathlib.Path, required=True)
    a = ap.parse_args()

    croissant = json.loads(a.infile.read_text())
    croissant["distribution"] = DISTRIBUTION
    croissant["recordSet"] = RECORD_SETS
    croissant["rai:dataLimitations"] = (
        (croissant.get("rai:dataLimitations", "") + " " + NOTE).strip()
    )

    a.outfile.parent.mkdir(parents=True, exist_ok=True)
    a.outfile.write_text(json.dumps(croissant, indent=2, ensure_ascii=False))
    print(f"[write] {a.outfile}")

    import mlcroissant as mlc
    mlc.Dataset(jsonld=json.loads(a.outfile.read_text()))
    print("[ok] valid Croissant 1.1")


if __name__ == "__main__":
    main()
