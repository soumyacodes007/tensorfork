"""Inspect the Chopsticks SQLite cache: schema and what is stored per block."""

import os
import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.tensorfork/cache/finney.sqlite")
con = sqlite3.connect(db)
for name, sql in con.execute("select name, sql from sqlite_master where type in ('table','index')"):
    print(name, "::", sql)
for (table,) in con.execute("select name from sqlite_master where type='table'"):
    print(table, con.execute(f"select count(*) from {table}").fetchone()[0], "rows")
try:
    rows = con.execute(
        "select blockHash, count(*), sum(length(value)) from key_value_pair group by blockHash"
    ).fetchall()
    for r in rows:
        print("  block", r[0], "keys", r[1], "bytes", r[2])
    sample = con.execute("select * from key_value_pair limit 2").fetchall()
    print("sample:", [tuple(str(c)[:80] for c in row) for row in sample])
except sqlite3.Error as e:
    print("query error:", e)
