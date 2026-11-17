# Kavach CTI - Cyber Threat Intelligence Platform
Exercise-7, 23CSE528 NoSQL Database using MongoDB (Labs 7.1 + 7.2 and both activities)

A working CTI website (FastAPI backend + vanilla JS frontend) on MongoDB 8.0, modelled on
[ctidigest.com](https://ctidigest.com/): threat news feed, sources, IOC database - plus search,
automatic threat classification, and a verdict for **any** IP address, even one never seen before.

## Quick start (macOS)
```bash
brew services start mongodb-community@8.0
cd Exercise7_CTI_Platform
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python backend/scripts/setup_database.py          # builds the database (1-3 min); --offline without internet
python backend/scripts/setup_database.py --gcve   # (optional) refresh only the db.gcve.eu vulnerability data
python -m uvicorn backend.app.main:app --port 8000 # or ./run.sh
```
Open http://127.0.0.1:8000

## Auto-update every 5 minutes
Open a **second terminal** (the website can keep running in the first one):
```bash
cd Exercise7_CTI_Platform && source .venv/bin/activate
python backend/scripts/auto_update.py      # or ./run_updater.sh
```
Every 5 minutes it pulls new news reports, CISA KEV and GCVE data (the large IP blacklists every 30 min) and writes
a heartbeat document to `cti_platform.ingest_runs` (`kind: "auto_update"`) - watch it in MongoDB Compass.
Options: `--interval 60` (demo speed), `--once`, `--quiet`, `--feeds-every N`, `--gcve-every N` (or the `AUTO_UPDATE_*` values in `.env`).

## Labs
| Task | Command | Output |
|---|---|---|
| Lab 7.1 schema analysis + dump | `python labs/lab7_1/lab_01_production_schema.py --dump` | `dump/`, `labs/lab7_1/output/` |
| Lab 7.1 from the dump | `python labs/lab7_1/lab_01_production_schema.py --from-dump` | analyses a restored copy |
| Activity 7.1 embedded redesign | `python labs/lab7_1/activity_7_1_embedded_redesign.py` | `threat_verdicts` collection |
| Lab 7.2 working set | `python labs/lab7_2/lab_02_working_set_analysis.py --small-cache 128` | 100,000 x 2 KB docs in `cti_lab72.ws_reports` |
| Activity 7.2 monitor | `python labs/lab7_2/activity_7_2_monitor.py --workload` | hit ratio every 10 s + new reports |

Written answers: `labs/lab7_1/ANSWERS.md`, `labs/lab7_2/ANSWERS.md`. The website shows the same results
live on the **Schema lab 7.1** and **Cache lab 7.2** pages.

## Website pages
- **Overview** - actual threats, category mix, severity per day, top CVEs/actors, trending vulnerabilities, source health.
- **Vulnerabilities** - real-world vulnerability intelligence from [db.gcve.eu](https://db.gcve.eu/), modelled on its
  dashboard: *Trending* (minimum sightings, boost recent, sighting-type filter, sparklines), *Charts* (sightings per day,
  top 10 seen / PoC / exploited / confirmed), *KEV and weaknesses* (KEV catalogs, CVSS mix, top CWEs and vendors) and an
  *Explorer*. Any CVE / GCVE / GHSA id opens a dossier: live record, CVSS, EPSS, CISA SSVC, KEV listings, sighting
  timeline, latest sightings, affected products and the Kavach reports that name it.
- **Threat feed** - `$text` search (or CVE / IP -> referenced ids), filters, report drawer explaining the classification.
- **IP intelligence** - 10+ local feeds (exact IP + CIDR range queries) + live ip-api, Shodan InternetDB,
  GreyNoise, reverse DNS (+ AbuseIPDB / VirusTotal / OTX with free keys). Explainable 0-100 score:
  Malicious >= 70, Suspicious >= 40, Low risk >= 15, else No known threat. Results cached with a TTL index.
- **Classifier** - paste any text: category, severity, actual-threat verdict, Admiralty code, extracted IOCs.
- **Sources** - reliability (A-F), status, one-click refresh of reports and feeds.

## Trusted real-world source: GCVE Vulnerability-Lookup (db.gcve.eu)
db.gcve.eu is operated by CIRCL, the Computer Incident Response Center Luxembourg (Luxembourg's national CERT), so it is
rated **A (completely reliable)** on the Admiralty scale. Its public read API needs no key. Endpoints used
(`backend/app/gcve.py`):

| Endpoint | Used for |
|---|---|
| `GET /api/sighting/stats?group_by=vulnerability,date&type=...` | daily sighting counts per type (the site's Charts tab) -> `gcve_sightings` |
| `GET /api/kev/?per_page=1000` | KEV catalogs of GCVE, CISA, CIRCL, ENISA, Shadowserver, Previdian -> `vulnerabilities.kev_catalogs` |
| `POST /api/vulnerability/bulk` (100 ids per call, `with_meta`) | CVSS, CWE, products, EPSS, CISA SSVC -> `vulnerabilities.gcve` |
| `GET /api/vulnerability/<id>`, `/api/sighting/?vuln_id=`, `/api/epss/<cve>` | live dossier |

The classifier now treats a CVE as *known exploited* if it is in any KEV catalog or has an *exploited* / *confirmed*
GCVE sighting (not only CISA KEV). Settings in `.env`: `GCVE_BASE_URL`, `GCVE_DAYS` (window, default 30),
`GCVE_ENRICH_TOP` (records enriched per refresh, default 200). Refresh any time from the Vulnerabilities or Sources page.

## Architecture
```
frontend/ (HTML/CSS/JS, served by FastAPI)  ->  backend/app/main.py (REST API /api/*)
  ingest.py   RSS feeds, IOC feeds, CISA KEV, ctidigest.com discovery, annual reports
  gcve.py     db.gcve.eu client: sightings, KEV catalogs, bulk records (CVE 5 / OSV / CSAF parsing), live dossier
  classifier.py  explainable rules: category / severity / verdict / Admiralty
  ipintel.py  local MongoDB evidence + live lookups -> scored verdict
  labs.py     Lab 7.1 / 7.2 logic shared by the website and the scripts
MongoDB cti_platform: sources, reports, indicators, sightings, vulnerabilities, gcve_sightings, threat_actors,
  annual_reports, ip_lookups (TTL), lookup_history, ingest_runs, cache_metrics (capped),
  threat_verdicts (Activity 7.1), lab_results
MongoDB cti_lab72: ws_reports (Lab 7.2 test data)
```

## Data sources (all free, no key required)
GCVE Vulnerability-Lookup by CIRCL (db.gcve.eu): sightings, KEV catalogs, CVSS, EPSS.
30 security news RSS feeds (BleepingComputer, The Hacker News, Krebs, Microsoft Security, CISA, Talos, Unit 42 ...),
IPsum, abuse.ch Feodo / ThreatFox / URLhaus, Spamhaus DROP, FireHOL Level 1, Emerging Threats, DShield,
blocklist.de, GreenSnow, BruteForceBlocker, Tor exits, CISA KEV, awesome-annual-security-reports.

Note: two details differ from the course sample code on purpose - `$bsonSize` is an aggregation operator
(there is no `bsonsize` server command), and the cache hit ratio is
`1 - (pages read into cache / pages requested from the cache)` measured over an interval.
