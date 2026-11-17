"""Test data shaped like the real Kavach database (cti_platform): vulnerabilities, sightings, reports, indicators."""
import sys
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

UTC = timezone.utc


def seed(uri: str, db_name: str = "cti_platform"):
    c = MongoClient(uri, tz_aware=True)
    db = c[db_name]
    for n in ("vulnerabilities", "gcve_sightings", "reports", "indicators", "sightings", "fp_verdicts"):
        db[n].delete_many({})
    now = datetime.now(UTC)
    d = lambda days: now - timedelta(days=days)   # noqa: E731
    V = [
        # Active: CISA KEV + exploited sightings, Windows
        {"_id": "CVE-2025-1001", "kev": {"vendor": "Microsoft", "product": "Windows", "name": "Microsoft Windows Win32k Privilege Escalation Vulnerability",
                                         "date_added": d(10), "due_date": d(-11), "ransomware_use": "Known", "description": "Microsoft Windows Win32k contains a use-after-free that allows local privilege escalation to SYSTEM."},
         "kev_catalogs": [{"catalog": "CISA", "listed_at": d(10).isoformat()}],
         "sighting_summary": {"seen": 20, "poc": 2, "exploited": 6, "confirmed": 1, "total": 29, "window_days": 30, "last_sighted": d(1)},
         "gcve": {"cvss": {"score": 7.8, "severity": "HIGH"}, "epss": {"score": 0.42}, "affected": [{"vendor": "Microsoft", "product": "Windows 11"}], "published": d(12).isoformat()}, "report_count": 3},
        # Active: Linux kernel
        {"_id": "CVE-2025-1002", "kev": {"vendor": "Linux", "product": "Kernel", "name": "Linux Kernel Use-After-Free Vulnerability", "date_added": d(20), "due_date": d(-1),
                                         "ransomware_use": "Unknown", "description": "The Linux kernel netfilter nf_tables component contains a use-after-free allowing local privilege escalation."},
         "sighting_summary": {"seen": 9, "poc": 1, "exploited": 2, "confirmed": 0, "total": 12, "window_days": 30, "last_sighted": d(2)},
         "gcve": {"cvss": {"score": 7.8, "severity": "HIGH"}, "affected": [{"vendor": "Linux", "product": "Linux"}]}},
        # Resolved: old KEV, due date passed, no sightings, Windows
        {"_id": "CVE-2021-34527", "kev": {"vendor": "Microsoft", "product": "Windows", "name": "Microsoft Windows Print Spooler Remote Code Execution Vulnerability (PrintNightmare)",
                                          "date_added": d(1200), "due_date": d(1180), "ransomware_use": "Known", "description": "Windows Print Spooler improperly performs privileged file operations."}},
        # Evolving: PoC published, not exploited, Android
        {"_id": "CVE-2025-1003", "sighting_summary": {"seen": 6, "poc": 3, "exploited": 0, "confirmed": 0, "total": 9, "window_days": 30, "last_sighted": d(1)},
         "gcve": {"title": "Android Framework privilege escalation", "description": "In Android Framework there is a possible way to bypass permissions due to a logic error.",
                  "cvss": {"score": 8.4, "severity": "HIGH"}, "affected": [{"vendor": "Google", "product": "Android"}]}},
        # Anticipated: seen only, high CVSS, iOS
        {"_id": "CVE-2025-1004", "sighting_summary": {"seen": 4, "poc": 0, "exploited": 0, "confirmed": 0, "total": 4, "window_days": 30, "last_sighted": d(3)},
         "gcve": {"title": "Apple iOS WebKit memory corruption", "description": "Processing maliciously crafted web content may lead to arbitrary code execution in iOS and iPadOS.",
                  "cvss": {"score": 8.8, "severity": "HIGH"}, "epss": {"score": 0.08}, "affected": [{"vendor": "Apple", "product": "iOS"}]}},
        # Active: network device, Fortinet, non-CISA catalog
        {"_id": "CVE-2025-1005", "kev_catalogs": [{"catalog": "ENISA", "listed_at": d(5).isoformat()}],
         "sighting_summary": {"seen": 30, "poc": 4, "exploited": 12, "confirmed": 2, "total": 48, "window_days": 30, "last_sighted": d(0)},
         "gcve": {"title": "Fortinet FortiOS SSL-VPN out-of-bounds write", "description": "An out-of-bounds write in FortiOS SSL-VPN allows a remote unauthenticated attacker to execute code.",
                  "cvss": {"score": 9.8, "severity": "CRITICAL"}, "affected": [{"vendor": "Fortinet", "product": "FortiOS"}]}},
        # Anticipated: macOS
        {"_id": "CVE-2025-1006", "report_count": 2, "gcve": {"title": "Apple macOS Gatekeeper bypass", "description": "A logic issue in macOS allowed an app to bypass Gatekeeper checks.",
                                                             "cvss": {"score": 7.1, "severity": "HIGH"}, "affected": [{"vendor": "Apple", "product": "macOS"}]}},
        # Active and in the scanner demo: libwebp / pillow
        {"_id": "CVE-2023-4863", "kev": {"vendor": "Google", "product": "Chromium WebP", "name": "Google Chromium WebP Heap-Based Buffer Overflow Vulnerability",
                                         "date_added": d(700), "due_date": d(680), "ransomware_use": "Unknown", "description": "Heap buffer overflow in libwebp."},
         "sighting_summary": {"seen": 3, "poc": 0, "exploited": 1, "confirmed": 0, "total": 4, "window_days": 30, "last_sighted": d(6)}},
        # Rejected
        {"_id": "CVE-2025-1007", "gcve": {"state": "REJECTED", "title": "Rejected record"}},
    ]
    db.vulnerabilities.insert_many(V)
    S = []
    for vid, last7, prev7 in (("CVE-2025-1003", 8, 1), ("CVE-2025-1001", 10, 9), ("CVE-2025-1005", 30, 18)):
        S += [{"_id": f"{vid}|a", "vulnerability": vid, "type": "seen", "count": last7, "date": d(2)},
              {"_id": f"{vid}|b", "vulnerability": vid, "type": "seen", "count": prev7, "date": d(10)}]
    db.gcve_sightings.insert_many(S)
    R = [
        {"title": "LockBit affiliates hit Windows servers of hospitals with new ransomware variant", "summary": "A new variant of LockBit ransomware encrypts Windows servers after RDP brute force.",
         "url": "https://example.org/r1", "published_at": d(3), "source_id": "bleepingcomputer", "classification": {"category": "Ransomware", "severity": "Critical", "verdict": "Actual threat"}, "tags": ["ransomware"], "cve_ids": []},
        {"title": "Android banking trojan spreads through fake delivery SMS", "summary": "A banking trojan abuses Accessibility on Android phones to steal OTP codes.",
         "url": "https://example.org/r2", "published_at": d(5), "source_id": "thehackernews", "classification": {"category": "Malware", "severity": "High", "verdict": "Actual threat"}, "tags": ["android"], "cve_ids": []},
        {"title": "Researchers warn of proof-of-concept for Linux sudo flaw", "summary": "Researchers found a flaw in sudo on Linux; no attacks seen, a proof-of-concept could follow.",
         "url": "https://example.org/r3", "published_at": d(7), "source_id": "securityweek", "classification": {"category": "Vulnerability", "severity": "High", "verdict": "Potential threat"}, "tags": [], "cve_ids": ["CVE-2025-1002"]},
        {"title": "Police dismantle infostealer network targeting macOS users", "summary": "An infostealer operation against macOS was taken down and operators arrested.",
         "url": "https://example.org/r4", "published_at": d(9), "source_id": "krebs", "classification": {"category": "Malware", "severity": "Medium", "verdict": "Potential threat"}, "tags": [], "cve_ids": []},
        {"title": "Company publishes quarterly results", "summary": "Nothing about security.", "url": "https://example.org/r5", "published_at": d(2), "source_id": "news",
         "classification": {"category": "General security", "severity": "Low", "verdict": "Informational"}, "tags": [], "cve_ids": []},
        {"title": "Old phishing wave against iPhone users", "summary": "A phishing campaign stole Apple ID passwords from iPhone users.", "url": "https://example.org/r6", "published_at": d(90),
         "source_id": "news", "classification": {"category": "Phishing", "severity": "Medium", "verdict": "Actual threat"}, "tags": [], "cve_ids": []},
    ]
    db.reports.insert_many(R)
    db.indicators.insert_one({"_id": "ipv4:185.220.101.1", "type": "ipv4", "value": "185.220.101.1", "tags": ["tor-exit", "blocklist"], "first_seen": d(40), "last_seen": d(0)})
    db.sightings.insert_many([{"_id": "ipv4:185.220.101.1|ipsum", "indicator_id": "ipv4:185.220.101.1", "source_id": "ipsum", "observed_at": d(0)},
                              {"_id": "ipv4:185.220.101.1|feodo", "indicator_id": "ipv4:185.220.101.1", "source_id": "feodo", "observed_at": d(1)}])
    return {"vulnerabilities": len(V), "reports": len(R)}


if __name__ == "__main__":
    print(seed(sys.argv[1] if len(sys.argv) > 1 else "mongodb://127.0.0.1:27017/?replicaSet=rs0"))
