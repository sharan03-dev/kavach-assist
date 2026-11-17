"""Unit tests of Kavach Assist that need no database:  python -m unittest tests.test_assist -v

    threat status rules (Active / Evolving / Anticipated / Resolved) and platform detection
    understanding of questions (platform, status, intent, follow-ups)
    password hashing and the password policy
    the scanner: parsing, findings, fix, verification by re-scan, rollback
"""
import os
import shutil
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("EMBEDDER", "hashing")

from backend.app.assist import auth, engine, scanner, threats  # noqa: E402

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def d(days):
    return NOW - timedelta(days=days)


class ThreatStatus(unittest.TestCase):
    def test_active_when_listed_as_exploited_and_sighted(self):
        v = {"_id": "CVE-1", "kev": {"date_added": d(10), "due_date": d(-11)}, "sighting_summary": {"exploited": 3, "total": 9, "window_days": 30}}
        status, why = threats.classify_vuln(v, {}, NOW)
        self.assertEqual(status, "Active")
        self.assertIn("exploited", why)

    def test_resolved_when_deadline_passed_and_no_sightings(self):
        v = {"_id": "CVE-2", "kev": {"date_added": d(900), "due_date": d(880)}}
        self.assertEqual(threats.classify_vuln(v, {}, NOW)[0], "Resolved")

    def test_evolving_with_public_proof_of_concept(self):
        v = {"_id": "CVE-3", "sighting_summary": {"poc": 2, "seen": 4, "total": 6}}
        self.assertEqual(threats.classify_vuln(v, {}, NOW)[0], "Evolving")

    def test_evolving_when_sightings_rise(self):
        v = {"_id": "CVE-4", "sighting_summary": {"seen": 9, "total": 9}}
        self.assertEqual(threats.classify_vuln(v, {"last7": 8, "prev7": 1}, NOW)[0], "Evolving")

    def test_anticipated_when_only_discussed(self):
        v = {"_id": "CVE-5", "sighting_summary": {"seen": 3, "total": 3}, "gcve": {"cvss": {"score": 8.8}}}
        status, why = threats.classify_vuln(v, {"last7": 1, "prev7": 2}, NOW)
        self.assertEqual(status, "Anticipated")
        self.assertIn("CVSS 8.8", why)

    def test_rejected_record_is_resolved(self):
        self.assertEqual(threats.classify_vuln({"_id": "CVE-6", "gcve": {"state": "REJECTED"}}, {}, NOW)[0], "Resolved")

    def test_reports(self):
        r = {"title": "Ransomware gang hits hospitals", "summary": "", "published_at": d(2), "classification": {"verdict": "Actual threat"}}
        self.assertEqual(threats.classify_report(r, NOW)[0], "Active")
        r["published_at"] = d(100)
        self.assertEqual(threats.classify_report(r, NOW)[0], "Resolved")
        self.assertIsNone(threats.classify_report({"title": "Quarterly results", "published_at": d(1), "classification": {"verdict": "Informational"}}, NOW))

    def test_platforms(self):
        self.assertEqual(threats.platforms_of("Microsoft Windows Win32k privilege escalation"), ["Windows"])
        self.assertEqual(threats.platforms_of("Linux kernel nf_tables use-after-free"), ["Linux"])
        self.assertIn("iOS", threats.platforms_of("Apple iOS and iPadOS WebKit memory corruption"))
        self.assertEqual(threats.platforms_of("Android Framework permission bypass"), ["Android"])
        self.assertEqual(threats.platforms_of("Fortinet FortiOS SSL-VPN out-of-bounds write"), ["Network devices"])
        self.assertEqual(threats.platforms_of("something unrelated"), ["Other"])


class Understanding(unittest.TestCase):
    def u(self, text, ctx=None, ui=None):
        return engine.understand(text, ctx or {}, ui)

    def test_platform_and_status(self):
        u = self.u("What are the active threats for Windows?")
        self.assertEqual((u["intent"], u["platform"], u["statuses"]), ("threats", "Windows", ["Active"]))
        u = self.u("Which Android threats are evolving and how do I mitigate them")
        self.assertEqual((u["platform"], u["statuses"], u["mitigate"]), ("Android", ["Evolving"], True))
        self.assertEqual(self.u("anticipated ios threats")["statuses"], ["Anticipated"])
        self.assertEqual(self.u("resolved threats on macOS")["platform"], "macOS")

    def test_follow_up_keeps_context(self):
        ctx = {"intent": "threats", "platform": "Windows", "statuses": ["Active"], "last_list": "threats", "last_ids": ["CVE-1", "CVE-2"]}
        u = self.u("what about linux?", ctx)
        self.assertEqual((u["platform"], u["statuses"]), ("Linux", ["Active"]))
        u = self.u("and the resolved ones", ctx)
        self.assertEqual((u["platform"], u["statuses"]), ("Windows", ["Resolved"]))
        self.assertEqual(self.u("how do I fix the first one?", ctx)["intent"], "threat_detail")

    def test_other_intents(self):
        self.assertEqual(self.u("tell me about CVE-2024-3400")["intent"], "cve")
        self.assertEqual(self.u("is 185.220.101.1 malicious?")["intent"], "ioc")
        self.assertEqual(self.u("what did lab 13 prove?")["intent"], "lab")
        self.assertEqual(self.u("explain write concern")["intent"], "lab")
        self.assertEqual(self.u("give me a summary of all platforms")["intent"], "stats")
        self.assertEqual(self.u("what did the scanner bot find?")["intent"], "scan_findings")
        self.assertEqual(self.u("scan now")["intent"], "scan_now")
        self.assertEqual(self.u("fix all")["intent"], "scan_fix")
        self.assertEqual(self.u("undo last fix")["intent"], "scan_undo")
        self.assertEqual(self.u("who won the world cup in 2011?")["intent"], "general")

    def test_confirmation_words(self):
        for w in ("yes", "Yes please", "go ahead", "confirm"):
            self.assertTrue(engine.YES.match(w), w)
        for w in ("no", "cancel", "stop"):
            self.assertTrue(engine.NO.match(w), w)
        self.assertFalse(engine.YES.match("yes but what is sharding"))

    def test_topic_terms(self):
        self.assertEqual(engine.topic_terms("what should I do about ransomware on Windows"), ["ransomware"])
        self.assertEqual(engine.topic_terms("show the active threats for linux"), [])


class Passwords(unittest.TestCase):
    def test_hash_and_check(self):
        h = auth.hash_password("Passw0rd!x")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertNotIn("Passw0rd", h)
        self.assertTrue(auth.check_password("Passw0rd!x", h))
        self.assertFalse(auth.check_password("passw0rd!x", h))
        self.assertNotEqual(h, auth.hash_password("Passw0rd!x"))      # a new salt every time

    def test_policy(self):
        self.assertIsNotNone(auth.password_problem("short1"))
        self.assertIsNotNone(auth.password_problem("onlyletters"))
        self.assertIsNone(auth.password_problem("Passw0rd!x"))


class Scanner(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        scanner.seed_demo(self.dir)
        self._osv = scanner.osv_advisories
        scanner.osv_advisories = lambda pkg, http: (_ for _ in ()).throw(ConnectionError("offline in tests"))   # use the bundled list

    def tearDown(self):
        scanner.osv_advisories = self._osv
        shutil.rmtree(self.dir, ignore_errors=True)

    def scan(self):
        return {f["_id"]: f for f in scanner.scan(self.dir, exploited={"CVE-2023-4863": "Active"}, system=False)["findings"]}

    def test_finds_the_planted_problems(self):
        fs = self.scan().values()
        kinds = {f["kind"] for f in fs}
        self.assertEqual(kinds, {"dependency", "secret", "permission", "config"})
        pillow = next(f for f in fs if f.get("package") == "pillow")
        self.assertEqual(pillow["severity"], "Critical")
        self.assertEqual(pillow["exploited"], ["CVE-2023-4863"])
        self.assertEqual(pillow["fixed_version"], "10.2.0")
        secret = next(f for f in fs if f["kind"] == "secret")
        self.assertFalse(secret["fixable"])
        self.assertNotIn("Sup3rSecret", secret["detail"])             # the secret itself is never stored

    def test_fix_verify_and_rollback(self):
        before = self.scan()
        fixable = [f for f in before.values() if f["fixable"]]
        self.assertGreaterEqual(len(fixable), 12)
        for fid, f in before.items():
            if f["fixable"]:
                r = scanner.apply({**f, "_id": fid}, "job_test", self.dir)
                self.assertTrue(r["ok"], f"{f['title']}: {r['message']}")
        after = self.scan()
        self.assertTrue(all(not f["fixable"] for f in after.values()), [f["title"] for f in after.values() if f["fixable"]])
        self.assertIn("flask==2.2.5", Path(self.dir, "requirements.txt").read_text())
        self.assertEqual(stat.S_IMODE(Path(self.dir, ".env").stat().st_mode), 0o600)
        self.assertIn(".env", Path(self.dir, ".gitignore").read_text().split())
        r = scanner.rollback("job_test", self.dir)
        self.assertTrue(r["ok"])
        self.assertIn("flask==2.0.1", Path(self.dir, "requirements.txt").read_text())
        self.assertEqual(len(self.scan()), len(before))

    def test_fix_refuses_paths_outside_the_folder(self):
        r = scanner.apply({"fix": {"type": "chmod", "file": "../../etc/passwd", "mode": "600"}}, "job_x", self.dir)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
