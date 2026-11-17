# raisecost

`raisecost` counts every Python exception raised while a block of code runs, attributes each one to
`file:line:function`, and turns the counts into a calibrated cost estimate. It uses **PEP 669
(`sys.monitoring`)** in CPython 3.12+, so it does not single-step or trace ordinary lines: only
`RAISE` and `EXCEPTION_HANDLED` events are enabled.

Upstream: none

## Why

Using exceptions for normal control flow (`try: return row[col] except KeyError`, `try: int(tok)
except ValueError`, ...) is common and invisible in normal profilers: cProfile shows the *function*
that raised, not how much of its time was exception dispatch, and it cannot tell a 2% miss path from
a 90% miss path. `raisecost` answers three questions with one run:

1. exactly how many exceptions were raised, per source line;
2. which exception types, and how many were handled;
3. a calibrated estimate of their cost in milliseconds (and as a share of a clean baseline run).

## Quick start (no install needed)

Requires CPython 3.12 or newer (`python3 -V`).

```bash
python3 -m raisecost run --baseline examples/demo_miss.py
```

Real output from this repository (AMD EPYC 7K62, CPython 3.12.3, 2026-11-18):

```
raisecost raisecost  CPython 3.12.3
raises: 12004   handled: 12004   calibration: 201.0 ns/raise
estimated exception tax: 2.413 ms
baseline runtime: 9.569 ms   tax share: 25.22%
raise types: KeyError=12004

top raise sites:
   1. examples/demo_miss.py:17 in resolve  12004 raises  ~2.413 ms

top handler sites:
   1. examples/demo_miss.py:18 in resolve  12004 handled
```

The demo resolves 6 optional columns over 4,000 records (24,000 lookups, half of them misses).
The count is exact; the millisecond figure is an estimate (see Limitations).

Options go **before** the script so they are not forwarded to it:

```bash
python3 -m raisecost run --json report.json --top 5 --baseline path/to/script.py arg1 arg2
python3 -m raisecost calibrate          # print this machine's raise+catch cost
```

Install as a command if you prefer:

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/raisecost run --baseline examples/demo_miss.py
```

## Library API

```python
from raisecost import Profiler

with Profiler() as prof:          # calibrates first, then enables monitoring
    do_the_work()
report = prof.report(baseline_seconds=0.0096)
report["total_raises"]            # exact count
report["top_raise_sites"][0]      # {file, line, function, raises, estimated_seconds}
```

`Profiler(measure_overhead=False)` skips calibration and gives counts only. `report()` returns a
JSON-serialisable dict and stops the profiler if it is still running.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

8 tests, all passing on CPython 3.12.3. They cover exact counts (37 raises -> 37), raise/handler
line attribution (`return d[key]` vs `except KeyError`), a hit-only path recording nothing, JSON
serialisation, tool-id release, calibration sanity, and the CLI end to end (JSON + baseline share).

## Evidence

- `evidence/demo_report.json` — full JSON report of the run quoted above.
- `evidence/demo_run.txt` — verbatim terminal output.

## How it works

- On `start()`, the profiler takes a free `sys.monitoring` tool id and registers `RAISE` and
  `EXCEPTION_HANDLED` callbacks; `stop()` clears events, unregisters both callbacks and frees the id.
- A raise site is `(co_filename, line-at-offset, co_name)`; a handler site uses the next source line
  at or after the handler offset, because CPython points the event at `PUSH_EXC_INFO` (which has no
  line of its own).
- Calibration is differential: the same loop is run once with `d[k]` on an empty dict and once with
  `d.get(k)`, and the difference per iteration is taken as raise+catch cost (median of 5 runs).
- Counts are exact for exceptions raised while the tool is active; they include exceptions raised
  inside imported modules and the standard library that the profiled code triggers.

## Limitations

- CPython 3.12+ only. There is no fallback to `sys.settrace`.
- The cost estimate is a machine-local differential calibration, not a measurement of the profiled
  run: real cost varies with traceback depth and handler complexity. Compare sites, not absolutes.
- The profiled run itself is slower (every raise and handler invokes a Python callback). Only the
  baseline run is timed.
- `--baseline` executes the script twice (once unmonitored, once monitored); do not use it for
  scripts with side effects.
- Handler events are counted per handler site; the profiler does not pair a specific raise with the
  handler that caught it.

## License

MIT. Copyright (c) 2026 linluozi.
