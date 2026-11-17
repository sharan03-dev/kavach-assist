"""Exercise 17 - AI-native MongoDB: vector search and RAG foundations. Runs every task and saves the evidence.

    python labs/lab17/run_lab17.py              # 17.1 + memory activity + 17.2 (5,000 docs) + Ask Kavach (about 3-6 minutes)
    python labs/lab17/run_lab17.py --quick      # 17.2 with 1,500 documents
    python labs/lab17/run_lab17.py --only lab_17_2
    python labs/lab17/run_lab17.py --report     # also results/Exercise-17_Report.pdf
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runner_kit as K  # noqa: E402
from backend.app import lab17 as L  # noqa: E402

PARTS = [("lab_17_1", "Lab 17.1 - a simple RAG pipeline (chunk, embed, $vectorSearch, prompt, generate)", lambda q: L.run_171()),
         ("memory", "Activity 17.1 - conversation memory in MongoDB (last 5 exchanges in the prompt)", lambda q: L.run_memory()),
         ("lab_17_2", "Lab 17.2 - HNSW vs IVF: build time, latency, recall, latency-recall curve", lambda q: L.run_172(q)),
         ("applied", "Applied to Lab 7 - Ask Kavach: RAG over the platform's own threat reports", lambda q: L.run_applied())]


def checks(o):
    c = []
    a = o.get("lab_17_1") or {}
    if a.get("evaluation"):
        c.append((f"17.1 retrieval: right document in the top 3 for {a['hit_at_3']}/{a['questions']} questions ({a.get('mode')})", a.get("pass", False)))
    a = o.get("memory") or {}
    if a.get("turns"):
        c.append((f"Activity: {a['stored_messages']} messages stored, follow-up questions resolved with the history", a.get("pass", False)))
    a = o.get("lab_17_2") or {}
    if a.get("rows"):
        h = [r for r in a["rows"] if r["index"].startswith("HNSW")]
        i = [r for r in a["rows"] if r["index"].startswith("IVF")]
        c.append((f"17.2: {len(h)} HNSW and {len(i)} IVF measurements over {a['queries']} queries x {a['n']} documents", bool(i) and (bool(h) or not a.get("search_available"))))
        if h:
            c.append((f"17.2: HNSW best recall@20 {max(r['recall'] for r in h):.1f} %", max(r["recall"] for r in h) >= 90))
    a = o.get("applied") or {}
    if a.get("build"):
        c.append((f"Applied to Lab 7: {a['build']['reports']} Kavach reports searchable by meaning", a.get("pass", False)))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=[p[0] for p in PARTS])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    from backend.app import polyglot as PG
    try:
        PG.wait_for(("mongodb", "vector"), 300)
    except RuntimeError as exc:
        print(f"!! {exc} - continuing with the NumPy fallback where possible")
    sys.exit(0 if K.run(17, PARTS, a, checks, "Exercise 17 - Vector search and RAG on MongoDB Atlas Local (mongod + mongot)") else 1)


if __name__ == "__main__":
    main()
