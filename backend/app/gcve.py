"""GCVE Vulnerability-Lookup (https://db.gcve.eu) - trusted, real-world vulnerability intelligence.

db.gcve.eu is the Vulnerability-Lookup instance of the GCVE project, run by CIRCL (the Computer
Incident Response Center Luxembourg, Luxembourg's national CERT). It correlates the CVE List,
NVD, GitHub/OSV advisories, CSAF providers, national CERTs and the GCVE numbering authorities,
and records *sightings*: evidence that a vulnerability was seen, exploited or has a public PoC.
The public read API needs no key. Endpoints used here (paths from the open-source server,
github.com/vulnerability-lookup/vulnerability-lookup):

  GET  /api/sighting/stats?group_by=vulnerability,date&type=..   daily sighting counts (the site's Charts tab)
  GET  /api/kev/?per_page=1000&page=N                             KEV catalogs: GCVE, CISA, CIRCL, ENISA, ... (BCP-07)
  POST /api/vulnerability/bulk  {"ids": [...<=100], "with_meta": true}   full records + EPSS in one call
  GET  /api/vulnerability/<id>?with_meta=true                      one record (fallback + live dossier)
  GET  /api/sighting/?vuln_id=<id>                                 individual sightings for one vulnerability
  GET  /api/epss/<cve>                                             EPSS exploit-prediction score

MongoDB layout (see labs.RELATIONSHIPS):
  gcve_sightings   one document per (vulnerability, day, sighting type) - an unbounded time series,
                   so it is its own collection and REFERENCES vulnerabilities._id
  vulnerabilities  .gcve              EMBEDDED 1:1 normalised record (title, CVSS, CWE, products, EPSS)
                   .kev_catalogs      EMBEDDED 1:few, one entry per KEV catalog that lists the CVE
                   .sighting_summary  EMBEDDED 1:1 pre-computed counts for the current window
"""
import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from pymongo import UpdateOne

from . import config

UTC = timezone.utc
BASE = config.GCVE_BASE_URL.rstrip("/")
HEADERS = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}

# The four sighting types shown on the db.gcve.eu Charts tab, strongest signal last.
SIGHTING_TYPES = ["seen", "published-proof-of-concept", "exploited", "confirmed"]
SIGHTING_LABELS = {"seen": "Seen", "published-proof-of-concept": "Published PoC", "exploited": "Exploited",
                   "confirmed": "Confirmed"}
SUMMARY_KEY = {"seen": "seen", "published-proof-of-concept": "poc", "exploited": "exploited", "confirmed": "confirmed"}

# KEV catalogs mirrored by db.gcve.eu (catalog_uuid = vulnerability_lookup_origin of the assertion).
KEV_CATALOGS = {
    "405284c2-e461-4670-8979-7fd2c9755a60": "CISA",
    "1a89b78e-f703-45f3-bb86-59eb712668bd": "CIRCL",
    "cce329bf-df49-4c6e-a027-80be2e6483bd": "ENISA",
    "c8fb6bf1-f81f-4cb8-95b1-eadbb3b54ee8": "Shadowserver",
    "caeb2787-0d58-4236-9039-7c86c3e566f3": "Previdian",
}
CISA_ORIGIN = "405284c2-e461-4670-8979-7fd2c9755a60"

VULN_ID_RX = re.compile(r"^(CVE-\d{4}-\d{4,}|GCVE-\d+-\d{4}-\d+|GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}|PYSEC-\d{4}-\d+)$", re.I)


def now():
    return datetime.now(UTC)


def norm_id(v: str) -> str:
    """Canonical id: upper case, except GHSA ids whose suffix is conventionally lower case."""
    v = (v or "").strip()
    if v.upper().startswith("GHSA-"):
        return "GHSA-" + v[5:].lower()
    return v.upper()


def vuln_url(vid: str) -> str:
    return f"{BASE}/vuln/{vid}"


# ======================================================================= HTTP
async def _request(http, method, path, *, params=None, json=None, tries=3):
    """GET/POST with a small retry budget for 429 / 5xx (the public instance rate-limits bursts)."""
    delay = 1.5
    for attempt in range(tries):
        r = await http.request(method, BASE + path, params=params, json=json)
        if r.status_code in (429, 502, 503, 504) and attempt < tries - 1:
            wait = float(r.headers.get("Retry-After", delay)) if str(r.headers.get("Retry-After", "")).isdigit() else delay
            await asyncio.sleep(min(wait, 20))
            delay *= 2
            continue
        r.raise_for_status()
        return r.json() if r.content else None
    return None


def client(timeout=None):
    return httpx.AsyncClient(headers=HEADERS, timeout=timeout or config.HTTP_TIMEOUT * 2, follow_redirects=True)


# ======================================================================= record parsing
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def _band(score):
    if score is None:
        return None
    return "Critical" if score >= 9 else "High" if score >= 7 else "Medium" if score >= 4 else "Low" if score > 0 else "None"


_CVSS_KEYS = [("cvssV4_0", "4.0"), ("cvssV3_1", "3.1"), ("cvssV3_0", "3.0"), ("cvssV2_0", "2.0")]


def _cvss_from_metrics(metric_lists):
    """Pick the newest CVSS version available across the CNA and ADP containers."""
    for key, ver in _CVSS_KEYS:
        for metrics in metric_lists:
            for m in metrics or []:
                c = m.get(key) if isinstance(m, dict) else None
                if isinstance(c, dict) and _f(c.get("baseScore")) is not None:
                    score = _f(c.get("baseScore"))
                    return {"version": ver, "score": score, "severity": (c.get("baseSeverity") or _band(score) or "").title(),
                            "vector": c.get("vectorString")}
    return None


def _ssvc_and_kev(adp_list):
    """CISA's ADP container carries SSVC decision points and, when listed, the KEV date."""
    ssvc, kev_added = {}, None
    for adp in adp_list or []:
        for m in adp.get("metrics", []) or []:
            other = m.get("other") if isinstance(m, dict) else None
            if not isinstance(other, dict):
                continue
            content = other.get("content") or {}
            if other.get("type") == "ssvc":
                for opt in content.get("options", []) or []:
                    if isinstance(opt, dict):
                        ssvc.update({k.lower().replace(" ", "_"): v for k, v in opt.items()})
            elif other.get("type") == "kev":
                kev_added = content.get("dateAdded")
    return ssvc, kev_added


def _parse_epss(meta):
    e = (meta or {}).get("epss") if isinstance(meta, dict) else None
    if isinstance(e, dict) and _f(e.get("epss")) is not None:
        return {"score": _f(e.get("epss")), "percentile": _f(e.get("percentile")), "date": e.get("date")}
    return None


def parse_record(rec, vid=None):
    """Normalise a Vulnerability-Lookup record (CVE JSON 5, OSV or CSAF) into a small document."""
    if not isinstance(rec, dict) or not rec:
        return None
    meta = rec.get("vulnerability-lookup:meta")
    out = {"epss": _parse_epss(meta)}

    if "cveMetadata" in rec:                                     # CVE JSON 5 (cvelistv5, GCVE GNAs)
        md = rec.get("cveMetadata") or {}
        cna = (rec.get("containers") or {}).get("cna") or {}
        adp = (rec.get("containers") or {}).get("adp") or []
        descs = cna.get("descriptions") or []
        desc = next((d.get("value") for d in descs if str(d.get("lang", "")).lower().startswith("en")),
                    descs[0].get("value") if descs else "")
        cwes = []
        for pt in cna.get("problemTypes") or []:
            for d in pt.get("descriptions") or []:
                cw = d.get("cweId") or (re.search(r"CWE-\d+", d.get("description", "")) or [None])[0]
                if cw and cw not in cwes:
                    cwes.append(cw)
        affected = []
        for a in cna.get("affected") or []:
            vp = {"vendor": a.get("vendor") or "", "product": a.get("product") or a.get("packageName") or ""}
            if (vp["vendor"] or vp["product"]) and vp not in affected:
                affected.append(vp)
        ssvc, kev_added = _ssvc_and_kev(adp)
        out.update({
            "id": md.get("cveId") or vid, "format": "CVE 5", "state": md.get("state"),
            "assigner": md.get("assignerShortName"),
            "published": _iso(md.get("datePublished")), "updated": _iso(md.get("dateUpdated")),
            "title": (cna.get("title") or desc or "")[:240], "description": (desc or "")[:2000],
            "cvss": _cvss_from_metrics([cna.get("metrics")] + [a.get("metrics") for a in adp]),
            "cwes": cwes[:6], "affected": affected[:12],
            "references": [r.get("url") for r in (cna.get("references") or []) if r.get("url")][:12],
            "ssvc": ssvc or None, "cisa_kev_added": kev_added,
        })
    elif "schema_version" in rec or ("id" in rec and ("details" in rec or "summary" in rec)):   # OSV (GitHub, PyPI ...)
        sev = None
        for s in rec.get("severity") or []:
            m = re.search(r"CVSS:(\d\.\d)", str(s.get("score", "")))
            if m:
                sev = {"version": m.group(1), "score": None, "severity": None, "vector": s.get("score")}
        ds = rec.get("database_specific") or {}
        if ds.get("severity") and sev is not None:
            sev["severity"] = str(ds["severity"]).title()
        out.update({
            "id": rec.get("id") or vid, "format": "OSV", "state": "PUBLISHED",
            "published": _iso(rec.get("published")), "updated": _iso(rec.get("modified")),
            "title": (rec.get("summary") or rec.get("details") or "")[:240],
            "description": (rec.get("details") or rec.get("summary") or "")[:2000],
            "cvss": sev, "cwes": (ds.get("cwe_ids") or [])[:6],
            "affected": [{"vendor": (a.get("package") or {}).get("ecosystem", ""),
                          "product": (a.get("package") or {}).get("name", "")} for a in (rec.get("affected") or [])][:12],
            "aliases": rec.get("aliases") or [],
            "references": [r.get("url") for r in (rec.get("references") or []) if r.get("url")][:12],
        })
    elif "document" in rec:                                      # CSAF advisory
        doc = rec.get("document") or {}
        vulns = rec.get("vulnerabilities") or [{}]
        notes = vulns[0].get("notes") or doc.get("notes") or []
        out.update({
            "id": vid or (doc.get("tracking") or {}).get("id"), "format": "CSAF", "state": "PUBLISHED",
            "published": _iso((doc.get("tracking") or {}).get("initial_release_date")),
            "updated": _iso((doc.get("tracking") or {}).get("current_release_date")),
            "title": (doc.get("title") or "")[:240],
            "description": (next((n.get("text") for n in notes if n.get("text")), "") or "")[:2000],
            "cvss": None, "cwes": [c.get("cwe", {}).get("id") for c in vulns if (c.get("cwe") or {}).get("id")][:6],
            "affected": [], "references": [r.get("url") for r in (doc.get("references") or []) if r.get("url")][:12],
        })
    else:
        return None
    out["id"] = norm_id(out.get("id") or vid or "")
    out["url"] = vuln_url(out["id"])
    if out.get("cvss") and not out["cvss"].get("severity"):
        out["cvss"]["severity"] = _band(out["cvss"].get("score"))
    return out


def parse_epss_payload(payload):
    """/api/epss/<cve> returns FIRST's format: {"data": [{"cve", "epss", "percentile", "date"}]}."""
    try:
        row = (payload or {}).get("data", [])[0]
        return {"score": _f(row.get("epss")), "percentile": _f(row.get("percentile")), "date": row.get("date")}
    except (IndexError, AttributeError, TypeError):
        return None


# ======================================================================= API calls
async def sighting_rows(http, date_from, date_to, stype):
    """Daily counts per vulnerability for one sighting type (the endpoint behind the Charts tab)."""
    data = await _request(http, "GET", "/api/sighting/stats", params={
        "group_by": "vulnerability,date", "type": stype,
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat()})
    return (data or {}).get("data", [])


async def kev_entries(http, max_pages=20, per_page=1000):
    """Every current KEV assertion (exploited=true) across all catalogs db.gcve.eu mirrors."""
    out, total = [], None
    for page in range(1, max_pages + 1):
        data = await _request(http, "GET", "/api/kev/", params={"page": page, "per_page": per_page})
        rows = (data or {}).get("data") or []
        total = ((data or {}).get("metadata") or {}).get("count", total)
        out.extend(rows)
        if not rows or len(rows) < per_page or (total is not None and len(out) >= total):
            break
    return out, total


async def fetch_records(http, ids, log=print):
    """Full records (+ meta incl. EPSS) for many ids: POST /bulk in chunks of 100, GET per id as fallback."""
    found = {}
    ids = list(dict.fromkeys(ids))
    use_bulk = True
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        if use_bulk:
            try:
                data = await _request(http, "POST", "/api/vulnerability/bulk", json={"ids": chunk, "with_meta": True})
                for row in (data or {}).get("data", []):
                    if row.get("vulnerability"):
                        found[norm_id(row.get("id"))] = row["vulnerability"]
                continue
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (400, 404, 405):
                    raise
                use_bulk = False      # older server without /bulk -> one GET per id
                log("  [info] bulk endpoint unavailable, fetching records one by one")
        sem = asyncio.Semaphore(4)

        async def one(vid):
            async with sem:
                try:
                    rec = await _request(http, "GET", f"/api/vulnerability/{vid}", params={"with_meta": "true"})
                    if rec:
                        found[norm_id(vid)] = rec
                except httpx.HTTPError:
                    pass
        await asyncio.gather(*(one(v) for v in chunk))
    return found


# ======================================================================= importer
def _kev_entry(row):
    origin = ((row.get("gcve") or {}).get("origin_uuid") or "").lower()
    ts = row.get("timestamps") or {}
    st = row.get("status") or {}
    return {
        "catalog": KEV_CATALOGS.get(origin, "GCVE"), "origin_uuid": origin or None,
        "status_reason": st.get("status_reason"),
        "listed_at": _iso(ts.get("first_seen_at") or ts.get("recorded_at") or st.get("status_updated_at")),
        "updated_at": _iso(st.get("status_updated_at")),
        "evidence": len(row.get("evidence") or []),
    }


async def import_gcve(db, days=None, enrich_top=None, log=print):
    """Pull real-world data from db.gcve.eu into MongoDB. Each part fails independently."""
    days = days or config.GCVE_DAYS
    enrich_top = config.GCVE_ENRICH_TOP if enrich_top is None else enrich_top
    run_at = now()
    date_to = run_at.date()
    date_from = date_to - timedelta(days=days - 1)
    result = {"status": "ok", "window_days": days, "sightings": 0, "vulns_sighted": 0, "kev_entries": 0,
              "kev_vulns": 0, "enriched": 0, "errors": []}

    async with client() as http:
        # ---------------------------------------------------------------- 1. sightings (Charts tab data)
        per_vuln = defaultdict(lambda: defaultdict(int))
        last_seen, docs = {}, {}
        for stype in SIGHTING_TYPES:
            try:
                rows = await sighting_rows(http, date_from, date_to, stype)
            except Exception as exc:
                result["errors"].append(f"sightings/{stype}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            for r in rows:
                vid, day, n = norm_id(r.get("vulnerability")), str(r.get("date", ""))[:10], int(r.get("count") or 0)
                if not vid or not day or not n:
                    continue
                key = f"{vid}|{day}|{stype}"
                if key in docs:                         # same id filed in two spellings -> one row
                    docs[key]["count"] += n
                else:
                    docs[key] = {"vulnerability": vid, "type": stype, "count": n,
                                 "date": datetime.fromisoformat(day).replace(tzinfo=UTC)}
                per_vuln[vid][stype] += n
                last_seen[vid] = max(last_seen.get(vid, day), day)
            log(f"  [ok]   GCVE sightings {SIGHTING_LABELS[stype]:<15} {len(rows):>7,} vulnerability-days")
        if docs:
            ops = [UpdateOne({"_id": k}, {"$set": {**v, "fetched_at": run_at}}, upsert=True) for k, v in docs.items()]
            for i in range(0, len(ops), 5000):
                db.gcve_sightings.bulk_write(ops[i:i + 5000], ordered=False)
            # rows inside the refreshed window that GCVE no longer reports (deleted sightings) are removed;
            # older days are kept as history.
            db.gcve_sightings.delete_many({"date": {"$gte": datetime.combine(date_from, datetime.min.time(), UTC)},
                                           "fetched_at": {"$lt": run_at}})
            sops = []
            for vid, counts in per_vuln.items():
                summ = {SUMMARY_KEY[t]: counts.get(t, 0) for t in SIGHTING_TYPES}
                summ.update(total=sum(counts.values()), window_days=days, updated_at=run_at,
                            last_sighted=datetime.fromisoformat(last_seen[vid]).replace(tzinfo=UTC))
                sops.append(UpdateOne({"_id": vid}, {"$set": {"sighting_summary": summ},
                                                     "$setOnInsert": {"first_seen": run_at, "report_count": 0}},
                                      upsert=True))
            for i in range(0, len(sops), 5000):
                db.vulnerabilities.bulk_write(sops[i:i + 5000], ordered=False)
            db.vulnerabilities.update_many({"sighting_summary.updated_at": {"$lt": run_at}},
                                           {"$unset": {"sighting_summary": ""}})
        result["sightings"] = sum(d["count"] for d in docs.values())
        result["vulns_sighted"] = len(per_vuln)

        # ---------------------------------------------------------------- 2. KEV catalogs (BCP-07)
        try:
            rows, total = await kev_entries(http)
            by_vuln = defaultdict(list)
            for row in rows:
                vid = norm_id((row.get("vulnerability") or {}).get("vulnId"))
                if vid and (row.get("status") or {}).get("exploited", True):
                    entry = _kev_entry(row)
                    if entry["catalog"] not in {e["catalog"] for e in by_vuln[vid]}:
                        by_vuln[vid].append(entry)
            kops, cisa_ops = [], []
            for vid, entries in by_vuln.items():
                kops.append(UpdateOne({"_id": vid}, {"$set": {"kev_catalogs": entries},
                                                     "$setOnInsert": {"first_seen": run_at, "report_count": 0}},
                                      upsert=True))
                cisa = next((e for e in entries if e["origin_uuid"] == CISA_ORIGIN), None)
                if cisa:   # keep the richer direct CISA download if present; otherwise use GCVE's mirror of it
                    cisa_ops.append(UpdateOne({"_id": vid, "kev": {"$exists": False}}, {"$set": {"kev": {
                        "date_added": cisa["listed_at"], "source": "db.gcve.eu mirror of CISA KEV"}}}))
            for ops_ in (kops, cisa_ops):           # second batch only touches documents the first one created
                for i in range(0, len(ops_), 2000):
                    db.vulnerabilities.bulk_write(ops_[i:i + 2000], ordered=False)
            if rows and (total is None or len(rows) >= total):   # complete download -> drop withdrawn listings
                db.vulnerabilities.update_many({"_id": {"$nin": list(by_vuln)}, "kev_catalogs": {"$exists": True}},
                                               {"$unset": {"kev_catalogs": ""}})
            result["kev_entries"], result["kev_vulns"] = len(rows), len(by_vuln)
            cats = defaultdict(int)
            for es in by_vuln.values():
                for e in es:
                    cats[e["catalog"]] += 1
            log(f"  [ok]   GCVE KEV catalogs                  {len(by_vuln):>7,} vulnerabilities "
                f"({', '.join(f'{k} {v:,}' for k, v in sorted(cats.items(), key=lambda x: -x[1]))})")
        except Exception as exc:
            result["errors"].append(f"kev: {type(exc).__name__}: {str(exc)[:120]}")
            log(f"  [skip] GCVE KEV catalogs                  {type(exc).__name__}: {str(exc)[:60]}")

        # ---------------------------------------------------------------- 3. enrich the most relevant records
        try:
            weight = {"exploited": 6, "confirmed": 4, "published-proof-of-concept": 3, "seen": 1}
            ranked = sorted(per_vuln, key=lambda v: -sum(weight[t] * n for t, n in per_vuln[v].items()))
            fresh_cut = run_at - timedelta(hours=20)
            recent = {d["_id"] for d in db.vulnerabilities.find(
                {"_id": {"$in": ranked[:enrich_top * 2]}, "gcve.fetched_at": {"$gte": fresh_cut}}, {"_id": 1})}
            wanted = [v for v in ranked if v not in recent and VULN_ID_RX.match(v)][:enrich_top]
            # CVEs that local news reports mention are worth a record too
            wanted += [d["_id"] for d in db.vulnerabilities.find(
                {"report_count": {"$gt": 0}, "gcve": {"$exists": False}}, {"_id": 1}).sort("report_count", -1).limit(80)]
            records = await fetch_records(http, wanted, log=log) if wanted else {}
            eops = []
            for vid, rec in records.items():
                parsed = parse_record(rec, vid)
                if parsed:
                    parsed["fetched_at"] = run_at
                    eops.append(UpdateOne({"_id": vid}, {"$set": {"gcve": parsed},
                                                         "$setOnInsert": {"first_seen": run_at, "report_count": 0}},
                                          upsert=True))
            if eops:
                db.vulnerabilities.bulk_write(eops, ordered=False)
            result["enriched"] = len(eops)
            log(f"  [ok]   GCVE vulnerability records             {len(eops):>7,} enriched (CVSS, CWE, EPSS, products)")
        except Exception as exc:
            result["errors"].append(f"records: {type(exc).__name__}: {str(exc)[:120]}")
            log(f"  [skip] GCVE vulnerability records             {type(exc).__name__}: {str(exc)[:60]}")

    if result["errors"] and not (result["sightings"] or result["kev_entries"] or result["enriched"]):
        result["status"] = "error"
    return result


# ======================================================================= live dossier
async def live_vulnerability(vid):
    """Everything db.gcve.eu knows about one id, fetched live (used by the vulnerability dossier)."""
    vid = norm_id(vid)
    out = {"record": None, "epss": None, "sightings": [], "kev": [], "errors": []}
    async with client(timeout=config.HTTP_TIMEOUT) as http:
        async def rec():
            try:
                out["record"] = parse_record(await _request(http, "GET", f"/api/vulnerability/{vid}",
                                                            params={"with_meta": "true"}), vid)
            except Exception as exc:
                out["errors"].append(f"record: {type(exc).__name__}")

        async def sig():
            try:
                data = await _request(http, "GET", "/api/sighting/", params={"vuln_id": vid, "per_page": 100})
                out["sightings"] = [{"type": s.get("type"), "source": s.get("source"),
                                     "at": _iso(s.get("creation_timestamp"))} for s in (data or {}).get("data", [])]
            except Exception as exc:
                out["errors"].append(f"sightings: {type(exc).__name__}")

        async def kev():
            try:
                data = await _request(http, "GET", "/api/kev/", params={"vuln_id": vid, "per_page": 50})
                out["kev"] = [_kev_entry(r) for r in (data or {}).get("data", [])]
            except Exception as exc:
                out["errors"].append(f"kev: {type(exc).__name__}")

        async def epss():
            if not vid.startswith("CVE-"):
                return
            try:
                out["epss"] = parse_epss_payload(await _request(http, "GET", f"/api/epss/{vid}"))
            except Exception:
                pass   # many CVEs simply have no EPSS score

        await asyncio.gather(rec(), sig(), kev(), epss())
    if out["record"] and not out["record"].get("epss") and out["epss"]:
        out["record"]["epss"] = out["epss"]
    return out
