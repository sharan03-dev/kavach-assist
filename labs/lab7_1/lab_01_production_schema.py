"""Lab 7.1 - Analyze a Production Schema (Cyber-Threat Intelligence platform)

    python labs/lab7_1/lab_01_production_schema.py              # analyse the live cti_platform database
    python labs/lab7_1/lab_01_production_schema.py --dump       # also create the MongoDB dump (dump/ folder)
    python labs/lab7_1/lab_01_production_schema.py --from-dump  # restore the dump into cti_platform_from_dump
                                                                  and analyse THAT copy ("given a dump ...")

Instructions covered:
  1. identify which relationships use embedding and which use referencing  (Tables 1 and 2)
  2. for each referencing relationship: why not embedded + what the embedded alternative looks like (Table 3)
  3. average document size with $bsonSize + verify nothing approaches 16 MB (Table 4 + growth check)
"""
import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bson  # noqa: E402
from bson import json_util  # noqa: E402
from rich.syntax import Syntax  # noqa: E402
from rich.table import Table  # noqa: E402

from backend.app import labs  # noqa: E402
from common import banner, client, config, connect, console, human, ROOT, save_output  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP_DIR = ROOT / "dump"
ARCHIVE = DUMP_DIR / "cti_platform.archive.gz"


def make_dump():
    if not shutil.which("mongodump"):
        console.print("[red]mongodump not found.[/red] Install the tools: brew install mongodb-database-tools")
        return False
    DUMP_DIR.mkdir(exist_ok=True)
    cmd = ["mongodump", f"--uri={config.MONGO_URI}", f"--db={config.DB_NAME}", "--gzip", f"--archive={ARCHIVE}",
           "--excludeCollection=ip_lookups", "--excludeCollection=lookup_history", "--excludeCollection=cache_metrics"]
    console.print("[bold]$[/bold] " + " ".join(cmd))
    ok = subprocess.run(cmd).returncode == 0
    if ok:
        console.print(f"[green][OK][/green] Dump written: {ARCHIVE.relative_to(ROOT)} ({human(ARCHIVE.stat().st_size)})")
    return ok


def restore_dump(target):
    if not ARCHIVE.exists():
        console.print("[red]No dump yet - run with --dump first.[/red]")
        sys.exit(1)
    cmd = ["mongorestore", f"--uri={config.MONGO_URI}", "--gzip", f"--archive={ARCHIVE}", "--drop",
           f"--nsFrom={config.DB_NAME}.*", f"--nsTo={target}.*"]
    console.print("[bold]$[/bold] " + " ".join(cmd))
    if subprocess.run(cmd).returncode != 0:
        sys.exit(1)
    console.print(f"[green][OK][/green] Restored into database [bold]{target}[/bold]")


def table_documented():
    t = Table(title="Table 1 - Relationship design decisions (embedding vs referencing)", show_lines=True)
    for col, kw in (("Collection.field", {"style": "cyan"}), ("Pattern", {}), ("Cardinality", {}), ("Why", {"max_width": 60})):
        t.add_column(col, **kw)
    for r in sorted(labs.relationships(), key=lambda x: (x["kind"], x["collection"])):
        pat = "[green]EMBEDDED[/green]" if r["kind"] == "embedded" else f"[yellow]REFERENCE[/yellow] -> {r['target']}"
        t.add_row(f"{r['collection']}.{r['field']}", pat, r["cardinality"], r["why"])
    console.print(t)


def table_detected(db):
    rows = labs.detect_relationships(db, sample=200)
    t = Table(title="Table 2 - Detected automatically from the real documents ($sample of 200 per collection)")
    for c in ("Collection", "Field", "Detected as", "Shape / target", "Evidence"):
        t.add_column(c)
    for r in rows:
        if r["kind"] == "reference":
            ev = f"{r['sampled_refs']} refs, {r['resolved_pct']}% resolve" if r["target"] else "-"
            t.add_row(r["collection"], r["field"], "[yellow]reference[/yellow]", f"-> {r['target']}", ev)
        else:
            ev = f"avg len {r.get('avg_len')} / max {r.get('max_len')}" if "avg_len" in r else f"avg {r.get('avg_keys')} keys"
            t.add_row(r["collection"], r["field"], "[green]embedded[/green]", r["shape"], ev)
    console.print(t)
    return rows


def embedded_alternative(db):
    """Build what ONE report would look like if all its references were embedded, and compare sizes."""
    rep = db.reports.find_one({"cve_ids.0": {"$exists": True}}) or db.reports.find_one({})
    if not rep:
        return None
    alt = dict(rep)
    alt["source"] = db.sources.find_one({"_id": rep["source_id"]}, {"stats": 0})
    alt["vulnerabilities"] = list(db.vulnerabilities.find({"_id": {"$in": rep.get("cve_ids", [])}}))
    alt["threat_actors"] = list(db.threat_actors.find({"_id": {"$in": rep.get("actor_ids", [])}}))
    alt["indicators"] = list(db.indicators.find({"_id": {"$in": rep.get("indicator_ids", [])}}))
    for k in ("source_id", "cve_ids", "actor_ids", "indicator_ids"):
        alt.pop(k, None)
    ref_size, emb_size = len(bson.encode(rep)), len(bson.encode(alt))
    t = Table(title="Table 3 - Referencing relationships: why not embedded, and the embedded alternative", show_lines=True)
    t.add_column("Reference", style="yellow")
    t.add_column("Why embedding was not used", max_width=46)
    t.add_column("Embedded alternative would look like", max_width=46)
    for r in labs.relationships():
        if r["kind"] == "reference":
            t.add_row(f"{r['collection']}.{r['field']} -> {r['target']}", r["why"], r["alternative"] or "-")
    console.print(t)
    shown = {k: alt[k] for k in ("title", "source", "vulnerabilities", "threat_actors") if k in alt}
    if shown.get("source"):
        shown["source"] = {k: shown["source"].get(k) for k in ("_id", "name", "reliability", "weight")}
    shown["vulnerabilities"] = [{"_id": v["_id"], "kev": {"vendor": (v.get("kev") or {}).get("vendor"),
                                                           "product": (v.get("kev") or {}).get("product")}}
                                for v in shown.get("vulnerabilities", [])][:2]
    shown["threat_actors"] = [{"_id": a["_id"], "name": a["name"], "aliases": a["aliases"][:2]}
                              for a in shown.get("threat_actors", [])][:2]
    console.print("\n[bold]Embedded alternative for one real report[/bold] (references replaced by copies):")
    console.print(Syntax(json_util.dumps(shown, indent=2)[:1800], "json", theme="ansi_dark", word_wrap=True))
    console.print(f"  Referenced report: [bold]{ref_size:,} B[/bold]   ->   fully embedded: [bold]{emb_size:,} B[/bold] "
                  f"({emb_size / max(ref_size, 1):.1f}x) - and the same source/CVE/actor copies would be repeated "
                  f"in every report that mentions them.")
    return {"report_id": str(rep["_id"]), "referenced_bytes": ref_size, "embedded_bytes": emb_size}


def table_bson(db):
    rows = labs.bson_stats(db)
    t = Table(title="Table 4 - Document size with $bsonSize (limit = 16 MB = 16,777,216 bytes)")
    for c, j in (("Collection", "left"), ("Docs", "right"), ("Avg", "right"), ("Min", "right"), ("Max", "right"),
                 ("Max as % of 16 MB", "right"), ("Status", "left")):
        t.add_column(c, justify=j)
    total_docs = total_bytes = 0
    for r in rows:
        if not r.get("count"):
            continue
        total_docs += r["count"]
        total_bytes += r["total_bytes"]
        colour = {"OK": "green", "WATCH": "yellow", "DANGER": "red"}[r["status"]]
        t.add_row(r["collection"], f"{r['count']:,}", f"{r['avg_bytes']:,.0f} B", f"{r['min_bytes']:,} B",
                  f"{r['max_bytes']:,} B", f"{r['max_pct_of_16mb']:.4f} %", f"[{colour}]{r['status']}[/{colour}]")
    console.print(t)
    avg_all = total_bytes / total_docs if total_docs else 0
    biggest = max((r for r in rows if r.get("count")), key=lambda r: r["max_bytes"], default=None)
    console.print(f"  Average document size across the platform: [bold]{avg_all:,.0f} bytes[/bold] "
                  f"over {total_docs:,} documents")
    if biggest:
        safe = biggest["max_bytes"] < config.BSON_LIMIT * 0.1
        verdict = "[green]no document approaches 16 MB[/green]" if safe else "[red]review this collection![/red]"
        console.print(f"  Largest document: [bold]{biggest['max_bytes']:,} B[/bold] in {biggest['collection']} = "
                      f"{biggest['max_pct_of_16mb']:.4f} % of the limit -> {verdict}")
    return rows, avg_all


def growth_check(db):
    """Where WOULD 16 MB become a risk? An indicator that embedded all its sightings."""
    busiest = list(db.sightings.aggregate([{"$group": {"_id": "$indicator_id", "n": {"$sum": 1}}},
                                           {"$sort": {"n": -1}}, {"$limit": 1}]))
    sample = db.sightings.find_one({}, {"_id": 0, "indicator_id": 0}) or {"source_id": "x", "confidence": 50}
    per = len(bson.encode({"0": sample}))
    need = config.BSON_LIMIT // max(per, 1)
    console.print("\n[bold]Growth check[/bold] - the risky design would be embedding sightings[] inside indicators:")
    console.print(f"  one embedded sighting ~ {per} bytes -> about [bold]{need:,}[/bold] sightings reach 16 MB.")
    if busiest:
        console.print(f"  Busiest indicator today: {busiest[0]['_id']} with {busiest[0]['n']} sightings. A feed that "
                      f"re-lists an IP every day adds 365 per year per feed, so the array is unbounded -> "
                      f"that is why sightings are a separate, referenced collection.")
    return {"bytes_per_sighting": per, "sightings_to_16mb": need, "busiest": busiest[0] if busiest else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="create dump/cti_platform.archive.gz with mongodump")
    ap.add_argument("--from-dump", action="store_true", help="restore the dump to a new DB and analyse that copy")
    args = ap.parse_args()

    banner("Lab 7.1: Analyze a Production Schema - CTI platform")
    connect()
    dbname = config.DB_NAME
    if args.dump:
        make_dump()
    if args.from_dump:
        dbname = f"{config.DB_NAME}_from_dump"
        restore_dump(dbname)
    db = client()[dbname]
    if db.reports.estimated_document_count() == 0:
        console.print("[red]The database is empty.[/red] Run:  python backend/scripts/setup_database.py")
        sys.exit(1)
    console.print(f"Analysing database [bold]{dbname}[/bold]: " + ", ".join(
        f"{c}={db[c].estimated_document_count():,}" for c in ("reports", "indicators", "sightings", "sources")))

    banner("Step 1 - Which relationships use embedding and which use referencing", "-")
    table_documented()
    detected = table_detected(db)
    banner("Step 2 - Referencing: why not embedded, and the embedded alternative", "-")
    alt = embedded_alternative(db)
    banner("Step 3 - $bsonSize: average size and the 16 MB limit", "-")
    console.print("Pipeline used:  [cyan]{$project: {s: {$bsonSize: '$$ROOT'}}}, "
                  "{$group: {_id: null, avg: {$avg: '$s'}, max: {$max: '$s'}}}[/cyan]\n")
    rows, avg_all = table_bson(db)
    growth = growth_check(db)

    save_output(HERE, "lab_7_1_results.json", {"database": dbname, "relationships": labs.relationships(),
                                                "detected": detected, "embedded_alternative": alt, "bson": rows,
                                                "average_bytes_all": avg_all, "growth": growth})
    banner("Lab 7.1 Complete")


if __name__ == "__main__":
    main()
