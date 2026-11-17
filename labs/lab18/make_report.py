"""Builds results/Exercise-18_Report.pdf - the Lab 18.2 comparative analysis report (Docker Hub URL on page 1).
    python labs/lab18/make_report.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from report_kit import (DBC, DBN, W, P, Paragraph, arrow, bars3, box, build_doc, checks, esc, hbars, header, mm, styles, tbl,  # noqa: E402
                        Drawing, Rect, String, colors)
from reportlab.platypus import PageBreak  # noqa: E402

TITLE = "Comparative Capstone Lab"
K3 = ("mongodb", "cassandra", "neo4j")


def gb(v):
    return f"{v / 1024:.1f} TB" if v >= 1024 else f"{v:.1f} GB"


def architecture():
    d = Drawing(510, 200)
    d.add(Rect(2, 2, 506, 196, fillColor=colors.HexColor("#eef3fb"), strokeColor=None))
    d.add(String(8, 188, "System architecture - false-positive detection for the threat-intelligence service", fontSize=7.5, fontName="Helvetica-Bold"))
    box(d, 8, 120, 110, 50, ["Threat feeds", "Kavach (Lab 7) sightings", "+ 12 simulated feeds"], fill="#fff4dc", size=6.2)
    box(d, 8, 30, 110, 50, ["Ingest service", "writes every threat to", "all three databases (Q4)"], fill="#fff4dc", size=6.2)
    arrow(d, 63, 120, 63, 80)
    for i, (k, lines) in enumerate((("mongodb", ["MongoDB rs0", "sources.recent_threats[50]", "full_threats + 4 indexes"]),
                                    ("cassandra", ["Cassandra", "4 query tables + counters", "+ indicator_sources (set)"]),
                                    ("neo4j", ["Neo4j", "(Source)-[:REPORTED]->(Indicator)", "-[:OF_FAMILY]->(Family)"]))):
        y = 130 - i * 56
        box(d, 170, y, 140, 46, lines, fill="#ffffff", size=6.1)
        arrow(d, 118, 55, 170, y + 23, DBC[k])
        arrow(d, 310, y + 23, 370, 115, DBC[k])
    box(d, 370, 90, 130, 50, ["False-positive detector", "allowlist + corroboration", "+ feed quality -> verdict"], fill="#fde8e8", size=6.1)
    box(d, 370, 20, 130, 50, ["Kavach website (Lab 7)", "cti_platform.fp_verdicts", "SUSPICIOUS feeds, communities"], fill="#fff4dc", size=6.1)
    arrow(d, 435, 90, 435, 70)
    return d


def _ingest_sentence(sc) -> str:
    need = sc["rows"][-1]["ingest_needed_per_s"]
    ok = [DBN[k] for k in K3 if (sc["measured_throughput"].get(k) or 0) >= need]
    low = [DBN[k] for k in K3 if (sc["measured_throughput"].get(k) or 0) < need]
    s = f"The 100M-threat peak needs {need:,}/s: "
    s += (", ".join(ok) + " already reach it on one node" if ok else "no database reaches it on one node")
    s += ("; " + ", ".join(low) + " would need more nodes or batching" if low else "")
    return s + ". At scale the deciding factors are storage, memory for the working set, and operations."


def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    r = out.get("lab_18_1") or {}
    e = header(st, 18, TITLE + " - false positives from the threat intelligence service", docker_url,
               "docker compose up -d lab18                  # Lab 7 -> 8 -> ... -> 17 -> 18 (everything)\n"
               "open http://localhost:8000/#/lab18          # schemas, benchmarks, false positives, communities, report\n"
               "docker compose exec lab18 lab18 all         # Lab 18.1 in full (100,000 threats) + this report", out.get("environment"))
    e += checks(out, st)
    if not r.get("benchmark"):
        e.append(Paragraph("Lab 18.1 has not produced results yet: run docker compose exec lab18 lab18 all.", st["p"]))
        return build_doc(results / "Exercise-18_Report.pdf", TITLE, 18, e)
    b, R, fp, cm, ld = r["benchmark"], r["benchmark"]["results"], r["fp"], r["communities"], r["load"]
    rec = r["recommendation"]
    # ------------------------------------------------------------------ (1) executive summary
    e += [Paragraph("1. Executive summary", st["h1"]),
          Paragraph(f"We stored {ld['n']:,} recent threats ({ld['real']:,} real sightings of the Kavach platform, {ld['generated']:,} generated from 12 simulated feeds) in "
                    "MongoDB, Cassandra and Neo4j, ran four benchmark queries ten times each, and ran the same false-positive detector on all three. "
                    f"Measured medians: threat history {R['q1']['mongodb']['median_ms']:.1f} / {R['q1']['cassandra']['median_ms']:.1f} / {R['q1']['neo4j']['median_ms']:.1f} ms, "
                    f"target and spread {R['q2']['mongodb']['median_ms']:.1f} / {R['q2']['cassandra']['median_ms']:.1f} / {R['q2']['neo4j']['median_ms']:.1f} ms, "
                    f"daily volume {R['q3']['mongodb']['median_ms']:.1f} / {R['q3']['cassandra']['median_ms']:.1f} / {R['q3']['neo4j']['median_ms']:.1f} ms, "
                    f"ingest {R['q4']['mongodb']['throughput_per_s']:,.0f} / {R['q4']['cassandra']['throughput_per_s']:,.0f} / {R['q4']['neo4j']['throughput_per_s']:,.0f} threats/s "
                    "(MongoDB / Cassandra / Neo4j). <b>Recommendation:</b> no single database wins every access pattern. Use <b>MongoDB</b> as the system of record for "
                    "threat documents and analyst views (flexible documents, embedded recent history, strong aggregation), <b>Neo4j</b> for the false-positive and campaign "
                    f"analysis (feed relationships and multi-hop patterns: {len(fp['suspicious'])} SUSPICIOUS feed pairs, {cm['campaigns_found']}/{cm['campaigns_planted']} "
                    "coordinated campaigns found as communities), and <b>Cassandra</b> only when the ingest volume outgrows a sharded MongoDB cluster - its answers are fast "
                    "because they are precomputed on write, which costs a table per question.", st["p"]),
          Paragraph("System architecture", st["h2"]), architecture()]
    # ------------------------------------------------------------------ (2) methodology
    env = (out.get("environment") or {}).get("backends") or (r.get("environment") or {}).get("versions") or {}
    e += [PageBreak(), Paragraph("2. Methodology", st["h1"]),
          Paragraph(f"<b>Data volume.</b> {ld['n']:,} threats, {ld['indicators']:,} distinct indicators, {len(ld['sources'])} sources, the last 30 days. Every real sighting of the "
                    "Kavach platform (Lab 7 feeds) is used first; the rest comes from simulated feeds with known reliability that also produce the three kinds of "
                    "false positives seen in practice - known-good infrastructure (public DNS, big domains), uncorroborated low-confidence noise, and coordinated "
                    f"campaigns (labels by sighting: {esc(json.dumps(ld['labels_by_sighting']))}). <b>Test environment.</b> One Docker host, each database a single "
                    f"container with default settings except memory limits; versions {esc(', '.join(f'{k} {v}' for k, v in env.items()))}. "
                    "<b>Query patterns.</b> Q1 latest 50 threats of a feed; Q2 who reported an indicator and how many related indicators (same family) those feeds "
                    "reported in 7 days; Q3 threats per day and type for 30 days; Q4 insert 1,000 new threats through the full write path. <b>Measurement.</b> One warm-up "
                    "call, then 10 runs per query and database with 10 different parameters (the same 10 for every database), wall-clock time with time.perf_counter() "
                    "in the client; the median is reported. The answers of the three databases are compared (they must be identical).", st["p"]),
          tbl([["Load", "Seconds", "What was written"],
               ["MongoDB", ld["mongodb"]["seconds"], P(f"{ld['mongodb']['documents']:,} documents + {ld['mongodb']['sources']} source documents (recent_threats via $topN)", st)],
               ["Cassandra", ld["cassandra"]["seconds"], P(f"{ld['cassandra']['rows']:,} rows in 4 tables, {ld['cassandra']['counters']:,} counters, {ld['cassandra']['indicator_rows']:,} indicator rows", st)],
               ["Neo4j", ld["neo4j"]["seconds"], P(f"{ld['neo4j']['indicators']:,} Indicator + {ld['neo4j']['sources']} Source nodes, {ld['neo4j']['relationships']:,} REPORTED relationships", st)]],
              [26 * mm, 20 * mm, W - 46 * mm])]
    # ------------------------------------------------------------------ (3) schema design
    e += [Paragraph("3. Schema design and data modeling analysis", st["h1"]),
          tbl([["Database", "Schema", "Design decision"],
               ["MongoDB", P("<b>sources</b> {_id, total, recent_threats: [last 50]} and <b>full_threats</b> {_id: threat_id, indicator, ind_type, from_source, threat_type, family, asn, "
                             "country, sector, confidence, timestamp}; indexes (from_source, timestamp), (indicator, timestamp), (family, timestamp), (timestamp, threat_type); "
                             "<b>allowlist</b>", st),
                P("Embedding the newest 50 threats in the source document makes the most frequent screen one document read; the array is bounded ($push + $each + $sort + "
                  "$slice: 50), so the document cannot grow past 16 MB. Everything else is a reference-free document per threat.", st)],
               ["Cassandra", P("<b>threats_by_source</b> ((source, month), ts DESC, threat_id), <b>threats_by_type</b> ((threat_type, day), ts DESC), <b>threats_by_indicator</b> "
                               "((indicator), ts DESC), <b>threats_by_family</b> ((family, day), ts DESC), <b>daily_volume</b> counters ((month), day, threat_type), "
                               "<b>indicator_sources</b> (indicator PK, sources set), <b>allowlist</b>", st),
                P("Query-driven: each query reads exactly one partition, so each needs its own table and every threat is written 4 times + 2 updates. The month / day in the "
                  "partition keys keeps partitions bounded. Aggregations are precomputed on write (counters, the set of feeds).", st)],
               ["Neo4j", P("(:Source {name})-[:REPORTED {threat_id, ts, day, type, confidence}]-&gt;(:Indicator {value, type, family, asn, country, allowlisted})"
                           "-[:OF_FAMILY]-&gt;(:Family), -[:IN_ASN]-&gt;(:ASN); (:Source)-[:SUSPICIOUS {shared_benign}]-(:Source); unique constraints + relationship indexes on day/ts", st),
                P("A sighting is a relationship, so 'who else reported this' and 'what else did those feeds report' are traversals, and feeds that share false positives "
                  "become an explicit SUSPICIOUS relationship that later queries can follow.", st)]],
              [20 * mm, 76 * mm, W - 96 * mm])]
    # ------------------------------------------------------------------ (4) performance
    e += [PageBreak(), Paragraph("4. Query performance comparison", st["h1"]),
          bars3([("Q1 history", {k: R["q1"][k]["median_ms"] for k in K3}), ("Q2 target+spread", {k: R["q2"][k]["median_ms"] for k in K3}),
                 ("Q3 daily volume", {k: R["q3"][k]["median_ms"] for k in K3})], "median latency (ms) - lower is better"),
          hbars([(DBN[k], R["q4"][k]["throughput_per_s"], DBC[k]) for k in K3], "Q4 ingest throughput (threats per second) - higher is better", unit="/s"),
          tbl([["Query", "Database", "median", "p95", "min", "max", "same answer"]] +
              [[q.upper(), DBN[k], f"{R[q][k]['median_ms']:.2f} ms", f"{R[q][k]['p95_ms']:.2f} ms", f"{R[q][k]['min_ms']:.2f} ms", f"{R[q][k]['max_ms']:.2f} ms",
                ("yes" if b["same_answer"].get(q) else "no") if q != "q4" else "-"] for q in ("q1", "q2", "q3", "q4") for k in K3],
              [14 * mm, 22 * mm, 24 * mm, 24 * mm, 24 * mm, 24 * mm, W - 132 * mm])]
    if R["q1"].get("mongodb_full_collection"):
        e.append(Paragraph(f"Q1 in MongoDB without the embedded array (find + sort on full_threats, index (from_source, timestamp)): median {R['q1']['mongodb_full_collection']['median_ms']:.2f} ms.", st["p"]))
    for q in ("q1", "q2", "q3", "q4"):
        e += [Paragraph(esc(b["text"][q]["title"]), st["h2"]),
              tbl([[DBN[k], P(esc(b["text"][q][k]).replace("\n", "<br/>"), st)] for k in K3], [22 * mm, W - 22 * mm], head=False)]
    # ------------------------------------------------------------------ use case results
    e += [PageBreak(), Paragraph("The use case: false-positive detection", st["h1"]),
          Paragraph(esc(fp["rules"]).replace("\n", "<br/>"), st["p"]),
          tbl([["Database", "Read time (ms)", "Indicators", "Flagged", "By reason"]] +
              [[DBN[k], f"{fp['per_db'][k]['fetch_ms']:,.0f}", f"{fp['per_db'][k]['indicators']:,}", f"{fp['per_db'][k]['flagged']:,}", P(esc(json.dumps(fp['per_db'][k]['by_reason'])), st)] for k in K3],
              [24 * mm, 26 * mm, 24 * mm, 20 * mm, W - 94 * mm]),
          Paragraph(f"Identical verdicts in all three databases: <b>{'yes' if fp['identical'] else 'no'}</b>. Against the generator's labels: precision <b>{fp['accuracy']['precision']}</b>, "
                    f"recall <b>{fp['accuracy']['recall']}</b> (known-good infrastructure: {fp['accuracy']['benign_recall']}); {fp['accuracy']['real_kavach_flagged']:,} real Kavach indicators flagged "
                    f"(unlabelled). Neo4j found {len(fp['suspicious'])} SUSPICIOUS feed pairs in {fp['suspicious_ms']} ms - a pattern that is one MATCH in Cypher and would need "
                    "a self-join (MongoDB $lookup on the same collection) or a new table (Cassandra).", st["p"]),
          hbars([(x["source"], 100 * x["corroboration"], colors.HexColor("#c92a2a") if x["suspicious_links"] else colors.HexColor("#2b8a3e")) for x in fp["feeds"]],
                "feed corroboration rate (%) - red: feed has SUSPICIOUS links", unit=" %"),
          Paragraph("Activity 18.1 - communities of threats (graph algorithm) cross-referenced with MongoDB", st["h2"]),
          Paragraph(f"Co-reporting graph from Neo4j: {cm['nodes']:,} indicators, {cm['edges']:,} links (same feed, same hour, repeated by >= 2 feeds). "
                    f"Louvain found {cm['communities']} communities; {len(cm['flagged'])} have unusually high inter-connectivity (density >= 0.5, links repeated by >= 2.5 feeds on average) and were written back to "
                    f"Indicator.community. Planted coordinated campaigns recovered: <b>{cm['campaigns_found']}/{cm['campaigns_planted']}</b>. {esc(cm['method'])}.", st["p"])]
    if cm["flagged"]:
        e.append(tbl([["#", "Size", "Density", "Freq.", "z", "Feeds (MongoDB)", "Families", "Window (min)", "Avg conf.", "Campaign"]] +
                     [[s["community"], s["size"], s["density"], s["avg_link_frequency"], s["z"], len(s["mongodb"].get("sources", [])), P(esc(", ".join(s["mongodb"].get("families", []))), st),
                       s["mongodb"].get("window_minutes"), s["mongodb"].get("avg_conf"), f"{int(100 * s['matches_campaign'])}%"] for s in cm["flagged"][:10]],
                     [10 * mm, 12 * mm, 15 * mm, 13 * mm, 12 * mm, 20 * mm, W - 136 * mm, 20 * mm, 17 * mm, 17 * mm]))
    # ------------------------------------------------------------------ scalability
    sc, sz = r["scale"], r["sizes"]
    e += [PageBreak(), Paragraph("Scalability assessment (1M, 10M, 100M threats)", st["h1"]),
          Paragraph("Measured storage per threat (compressed, including indexes / every query table): " +
                    ", ".join(f"{DBN[k]} <b>{sz[k]['per_threat']} B</b> ({esc(sz[k]['method'])})" for k in K3) + ".", st["p"]),
          tbl([["Threats"] + [P(f"{DBN[k]}<br/>storage / RAM / nodes", st, "hcell") for k in K3] + ["Ingest peak"]] +
              [[f"{x['threats']:,}"] + [f"{gb(x[k]['storage_gb'])} / {gb(x[k]['ram_gb'])} / {x[k]['nodes']}" for k in K3] + [f"{x['ingest_needed_per_s']:,}/s"] for x in sc["rows"]],
              [22 * mm, 42 * mm, 42 * mm, 42 * mm, W - 148 * mm]),
          Paragraph("Assumptions: " + esc("; ".join(sc["assumptions"])) + ". Measured single-node ingest: " +
                    esc(", ".join(f"{DBN[k]} {sc['measured_throughput'][k]:,.0f}/s" for k in K3)) + ". " +
                    esc(_ingest_sentence(sc)), st["p"]),
          Paragraph("Horizontal scaling mechanisms", st["h2"]),
          tbl([["Database", "Mechanism", "Hardware estimate at 100M threats"]] +
              [[DBN[k], P(esc(sc["rows"][-1][k]["how"]), st), P(f"{gb(sc['rows'][-1][k]['storage_gb'])} storage (RF 3), {gb(sc['rows'][-1][k]['ram_gb'])} RAM, {sc['rows'][-1][k]['nodes']} nodes", st)] for k in K3],
              [22 * mm, W - 82 * mm, 60 * mm]),
          Paragraph("MongoDB shards a collection by a shard key (Lab 11): (from_source, timestamp) would co-locate a feed's history but concentrate new writes; a hashed "
                    "threat_id spreads writes and makes Q1 a scatter-gather - the embedded recent_threats document avoids that. Cassandra places partitions on a token "
                    "ring with virtual nodes: adding a node moves a share of every range to it, with no coordinator and linear write scaling; the price is the "
                    "denormalised tables and repair/compaction work. Neo4j Community runs one instance; Enterprise adds clustering with read replicas and composite "
                    "databases (Fabric) for sharding by subgraph - multi-hop traversals are fastest when the traversed part fits in one machine's page cache.", st["p"])]
    # ------------------------------------------------------------------ (5) ergonomics / operations
    e += [Paragraph("5. Ergonomics and operational complexity", st["h1"]),
          tbl([["", "MongoDB", "Cassandra", "Neo4j"]] + [[P(f"<b>{esc(x[0])}</b>", st)] + [P(esc(c), st) for c in x[1:]] for x in r["ergonomics"]],
              [24 * mm, (W - 24 * mm) / 3, (W - 24 * mm) / 3, (W - 24 * mm) / 3])]
    # ------------------------------------------------------------------ (6) recommendation
    e += [Paragraph("6. Recommendation per access pattern", st["h1"]),
          tbl([["Access pattern", "Measured fastest", "Recommend", "Why"]] +
              [[P(esc(x["pattern"]), st), DBN.get(x["measured_winner"], x["measured_winner"]), P(f"<b>{esc(x['recommend'])}</b>", st), P(esc(x["why"]), st)] for x in rec],
              [40 * mm, 24 * mm, 24 * mm, W - 88 * mm]),
          Paragraph("<b>Final recommendation.</b> For the Kavach threat-intelligence service at today's scale, MongoDB (already the platform's database, Labs 7-15) "
                    "remains the system of record; Neo4j is added as an analysis store for feed quality, false-positive patterns and campaign detection, fed by "
                    "the same ingest; Cassandra is not needed until the write rate or the data volume exceeds what a sharded MongoDB cluster handles economically. "
                    "This keeps one operational database to back up and monitor (Lab 14) and adds the graph only where it changes the answer.", st["p"])]
    ap = r.get("applied") or {}
    if ap:
        e.append(Paragraph(f"<b>Applied to Lab 7.</b> {ap.get('written', 0):,} verdicts written to {esc(ap.get('collection', ''))}; {ap.get('matching_kavach_indicators', 0):,} of them are "
                           "indicators of the Kavach platform.", st["p"]))
    return build_doc(results / "Exercise-18_Report.pdf", TITLE, 18, e)


if __name__ == "__main__":
    res = Path(os.getenv("RESULTS_DIR", str(Path(__file__).resolve().parents[2] / "results")))
    print(build(json.loads((res / "lab18_results.json").read_text()), res, os.getenv("DOCKER_URL", "")))
