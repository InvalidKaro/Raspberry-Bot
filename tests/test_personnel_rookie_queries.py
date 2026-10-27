"""Tests for rookie reporting without a running Discord bot."""
import sqlite3
import unittest
from datetime import date
from pathlib import Path


class RookieQueriesTest(unittest.TestCase):
    def test_rookie_is_subset_of_personnel_totals(self):
        db = sqlite3.connect(":memory:")
        db.executescript("""
            CREATE TABLE personnel_members (id INTEGER PRIMARY KEY, display_name TEXT, active INTEGER);
            CREATE TABLE personnel_records (
                id INTEGER PRIMARY KEY, personnel_id INTEGER, record_date TEXT,
                bwg INTEGER, inductions INTEGER
            );
            CREATE TABLE personnel_rookie_memberships (
                id INTEGER PRIMARY KEY, personnel_id INTEGER, joined_on TEXT,
                left_on TEXT, changed_by INTEGER, created_at TEXT
            );
            INSERT INTO personnel_members VALUES(1,'Rookie',1),(2,'Perso',1);
            INSERT INTO personnel_records VALUES
                (1,1,'2026-10-20',3,2),(2,2,'2026-10-20',5,1);
            INSERT INTO personnel_rookie_memberships VALUES
                (1,1,'2026-10-01',NULL,123,'2026-10-01');
        """)
        total = db.execute("SELECT SUM(bwg),SUM(inductions) FROM personnel_records").fetchone()
        rookie = db.execute("""
            SELECT SUM(r.bwg),SUM(r.inductions) FROM personnel_records r
            WHERE EXISTS (SELECT 1 FROM personnel_rookie_memberships rm
                WHERE rm.personnel_id=r.personnel_id AND r.record_date>=rm.joined_on
                AND (rm.left_on IS NULL OR r.record_date<=rm.left_on))
        """).fetchone()
        self.assertEqual(total, (8, 3))
        self.assertEqual(rookie, (3, 2))
        db.close()


if __name__ == "__main__":
    unittest.main()
