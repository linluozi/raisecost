"""Demo workload: optional record fields resolved with try/except KeyError.

Each record carries only a subset of the optional columns, so most lookups
miss and take the exception path.  This is the pattern raisecost is meant to
surface (it is *not* a benchmark of raisecost itself).
"""

import random

COLUMNS = ("id", "name", "email", "age", "country", "plan")
ROWS = 4000
SEED = 4321


def resolve(row, column):
    try:
        return row[column]
    except KeyError:
        return ""


def build(rows):
    return {column: [resolve(row, column) for row in rows] for column in COLUMNS}


def main():
    rng = random.Random(SEED)
    rows = []
    for i in range(ROWS):
        row = {"id": i, "name": "user-%d" % i}
        for column in ("email", "age", "country", "plan"):
            if rng.random() < 0.25:
                row[column] = "x"
        rows.append(row)
    table = build(rows)
    print("rows=%d columns=%d cells=%d"
          % (len(rows), len(table), sum(len(v) for v in table.values())))


if __name__ == "__main__":
    main()
