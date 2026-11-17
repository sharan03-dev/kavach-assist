"""Lab 16.2 - unit tests for FraudQueryService with unittest.mock (no database needed).

    python -m unittest labs/lab16/test_fraud_service.py -v
Each database driver is replaced by a MagicMock, so the tests check what the service ASKS each database and how it
MERGES the answers - and the circuit breaker, with a fake clock (no real 30-second waits).
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fraud_service import CircuitBreaker, CircuitOpen, FraudQueryService  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def make_service(clock=None):
    mongo = mock.MagicMock(name="MongoClient")
    db = mongo.__getitem__.return_value
    cass = mock.MagicMock(name="CassandraSession")
    neo = mock.MagicMock(name="Neo4jDriver")
    session = neo.session.return_value.__enter__.return_value
    svc = FraudQueryService(mongo, cass, neo, clock=clock or FakeClock())
    return svc, db, cass, session


def neo_result(rows):
    r = mock.MagicMock()
    r.data.return_value = rows
    return r


class TestMongoDB(unittest.TestCase):
    def test_get_transaction_queries_by_txn_id(self):
        svc, db, _, _ = make_service()
        db.transactions.find_one.return_value = {"txn_id": "T1", "amount": 120.0, "merchant": "M", "risk_score": 0.2}
        self.assertEqual(svc.get_transaction("T1")["amount"], 120.0)
        db.transactions.find_one.assert_called_once_with({"txn_id": "T1"}, {"_id": 0})

    def test_unknown_transaction_is_none(self):
        svc, db, _, _ = make_service()
        db.transactions.find_one.return_value = None
        self.assertIsNone(svc.get_transaction("nope"))


class TestCassandra(unittest.TestCase):
    def test_profile_uses_prepared_statement_on_the_user_partition(self):
        svc, _, cass, _ = make_service()
        now = datetime(2026, 11, 13, 12, 0)
        cass.execute.return_value = [{"user_id": "U1", "event_time": now, "txn_id": "T1", "event_type": "payment",
                                      "amount": 10.0, "merchant": "M", "country": "IN"}]
        rows = svc.get_user_profile("U1", 10)
        self.assertEqual(rows[0]["txn_id"], "T1")
        cql = cass.prepare.call_args[0][0]
        self.assertIn("WHERE user_id = ?", cql)
        self.assertIn("ORDER BY event_time DESC", cql)
        self.assertEqual(cass.execute.call_args[0][1], ("U1", 10))

    def test_statement_prepared_once(self):
        svc, _, cass, _ = make_service()
        cass.execute.return_value = []
        svc.get_user_profile("U1"); svc.get_user_profile("U2")
        self.assertEqual(cass.prepare.call_count, 1)


class TestNeo4j(unittest.TestCase):
    def test_rings_query_uses_hops_and_parameter(self):
        svc, _, _, s = make_service()
        s.run.side_effect = [neo_result([{"user": "U2", "hops": 1, "paths": 1}]),
                             neo_result([{"ring": ["U1", "U2", "U3", "U1"], "total": 300.0}])]
        out = svc.detect_fraud_rings("U1", max_hops=3)
        self.assertEqual(out["rings"][0]["ring"][0], "U1")
        first_q = s.run.call_args_list[0][0][0]
        self.assertIn("TRANSFERRED_TO*1..3", first_q)
        self.assertEqual(s.run.call_args_list[0][1]["uid"], "U1")      # the user id is a parameter, never in the string

    def test_hops_are_validated(self):
        svc, _, _, _ = make_service()
        for bad in (0, 7, "1; DROP"):
            with self.assertRaises((ValueError, TypeError)):
                svc.detect_fraud_rings("U1", bad)


class TestMerge(unittest.TestCase):
    def test_analyze_merges_three_sources(self):
        svc, db, cass, s = make_service()
        db.transactions.find.return_value.sort.return_value.limit.return_value = [{"txn_id": "T1", "risk_score": 0.9}]
        base = datetime(2026, 11, 13, 12, 0)
        cass.execute.return_value = [{"event_time": base - timedelta(minutes=i), "txn_id": f"T{i}"} for i in range(5)]
        s.run.side_effect = [neo_result([{"user": "U2", "hops": 1, "paths": 1}]),
                             neo_result([{"ring": ["U1", "U2", "U1"], "total": 50.0}])]
        r = svc.analyze_user("U1")
        self.assertEqual(r["degraded"], [])
        self.assertEqual(r["risk"]["level"], "high")
        self.assertEqual(set(r["sources"]), {"mongodb", "cassandra", "neo4j"})

    def test_one_backend_down_gives_partial_result(self):
        svc, db, cass, s = make_service()
        db.transactions.find.return_value.sort.return_value.limit.return_value = []
        cass.execute.side_effect = ConnectionError("cassandra down")
        s.run.side_effect = [neo_result([]), neo_result([])]
        r = svc.analyze_user("U1")
        self.assertEqual(r["degraded"], ["cassandra"])
        self.assertIsNotNone(r["transactions"])
        self.assertIsNone(r["profile"])


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_three_consecutive_failures_and_skips(self):
        clock = FakeClock()
        svc, _, cass, _ = make_service(clock)
        cass.execute.side_effect = ConnectionError("down")
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                svc.get_user_profile("U1")
        self.assertEqual(svc.breakers["cassandra"].state, "open")
        calls = cass.execute.call_count
        with self.assertRaises(CircuitOpen):
            svc.get_user_profile("U1")
        self.assertEqual(cass.execute.call_count, calls)          # skipped: the database was not called

    def test_half_open_after_30_seconds_then_closes(self):
        clock = FakeClock()
        svc, _, cass, _ = make_service(clock)
        cass.execute.side_effect = ConnectionError("down")
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                svc.get_user_profile("U1")
        clock.advance(29.9)
        self.assertEqual(svc.breakers["cassandra"].state, "open")
        clock.advance(0.2)
        self.assertEqual(svc.breakers["cassandra"].state, "half_open")
        cass.execute.side_effect = None
        cass.execute.return_value = []
        self.assertEqual(svc.get_user_profile("U1"), [])
        self.assertEqual(svc.breakers["cassandra"].state, "closed")

    def test_failed_trial_reopens(self):
        clock = FakeClock()
        b = CircuitBreaker("x", 3, 30, clock)
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                b.call(lambda: (_ for _ in ()).throw(RuntimeError("e")))
        clock.advance(31)
        with self.assertRaises(RuntimeError):
            b.call(lambda: (_ for _ in ()).throw(RuntimeError("e")))
        self.assertEqual(b.state, "open")

    def test_success_resets_the_counter(self):
        b = CircuitBreaker("x", 3, 30, FakeClock())
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                b.call(lambda: (_ for _ in ()).throw(RuntimeError("e")))
        b.call(lambda: 1)
        with self.assertRaises(RuntimeError):
            b.call(lambda: (_ for _ in ()).throw(RuntimeError("e")))
        self.assertEqual(b.state, "closed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
