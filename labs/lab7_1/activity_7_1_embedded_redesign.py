"""Activity 7.1 - Redesign one referencing relationship as embedded.

Chosen relationship:  sightings.indicator_id -> indicators   (+ sightings.source_id -> sources)
Question it answers:  "Which source says this indicator is an ACTUAL threat, and how sure are we?"

Referenced (current):  indicators <- sightings -> sources      (3 collections, 2 x $lookup per decision)
Embedded (redesign):   threat_verdicts { _id, value, score, verdict,
                                         sightings: [ {source_id, source_name, reliability, weight,
                                                       confidence, observed_at}, ... up to 50 ] }

    python labs/lab7_1/activity_7_1_embedded_redesign.py          # build + benchmark (300 random indicators)
    python labs/lab7_1/activity_7_1_embedded_redesign.py --n 1000
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import json_util  # noqa: E402
from rich.syntax import Syntax  # noqa: E402
from rich.table import Table  # noqa: E402

from backend.app import labs  # noqa: E402
from common import banner, connect, console, get_db, save_output  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300, help="how many random indicators to benchmark")
    args = ap.parse_args()

    banner("Activity 7.1: Embedded redesign - deciding which source is an actual threat")
    connect()
    db = get_db()
    if db.sightings.estimated_document_count() == 0:
        console.print("[red]No sightings yet.[/red] Run:  python backend/scripts/setup_database.py")
        sys.exit(1)

    console.print("Building [bold]threat_verdicts[/bold] with one aggregation "
                  "(sightings -> $lookup sources -> $group -> $reduce noisy-OR -> $merge) ...")
    res = labs.run_activity_71(db, n=args.n, rebuild=True)
    if res.get("error"):
        console.print(f"[red]{res['error']}[/red]")
        sys.exit(1)
    b = res["build"]
    console.print(f"[green][OK][/green] {b['documents']:,} embedded documents built in {b['seconds']} s\n")

    ex = db.threat_verdicts.find_one({"verdict": "Actual threat", "sighting_count": {"$gte": 2}}) or \
        db.threat_verdicts.find_one(sort=[("score", -1)])
    if ex:
        ex["sightings"] = ex["sightings"][:4]
        console.print("[bold]Example embedded document[/bold] (first 4 sightings shown):")
        console.print(Syntax(json_util.dumps(ex, indent=2), "json", theme="ansi_dark", word_wrap=True))

    t = Table(title=f"Queries that become FASTER when embedded (average over {res['sample_size']} random indicators)",
              show_lines=True)
    for c in ("Query", "Referenced", "Embedded", "Speed-up", "Referenced reads", "Embedded reads"):
        t.add_column(c)
    for q in res["faster"]:
        sp = q["referenced_ms"] / q["embedded_ms"] if q["embedded_ms"] else 0
        t.add_row(q["query"], f"{q['referenced_ms']:.3f} ms", f"{q['embedded_ms']:.3f} ms", f"[green]{sp:.1f}x[/green]",
                  q["referenced_reads"], q["embedded_reads"])
    console.print(t)

    t = Table(title="Operations that become HARDER when embedded", show_lines=True)
    for c in ("Operation", "Referenced", "Embedded", "Docs touched (ref / emb)", "Why"):
        t.add_column(c)
    for h in res["harder"]:
        docs = f"{h.get('referenced_docs', '-')} / {h.get('embedded_docs', '-')}"
        t.add_row(h["operation"], f"{h['referenced_ms']} ms", f"{h['embedded_ms']} ms", docs, h["note"])
    console.print(t)

    s = res["size"]
    console.print(f"\n[bold]Size check[/bold]: avg {s['avg_bytes']:,} B, max {s['max_bytes']:,} B per verdict document; "
                  f"busiest indicator has {s['max_total_sightings']} sightings (array capped at 50).")
    console.print(f"  ~{s['bytes_per_sighting']} B per embedded sighting -> about {s['sightings_to_reach_16mb']:,} "
                  f"sightings would hit 16 MB, so the cap ($slice 50, 'subset pattern') keeps this design safe.")
    console.print("\n[bold]Conclusion[/bold]: embed for the read-heavy 'is it a threat?' decision (1 document, "
                  "1 index seek); keep the referenced sightings collection as the source of truth and rebuild "
                  "threat_verdicts after each feed import (this is what the $merge does).")
    save_output(HERE, "activity_7_1_results.json", res)
    banner("Activity 7.1 Complete")


if __name__ == "__main__":
    main()
