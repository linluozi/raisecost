"""Command line entry point: ``python -m raisecost run SCRIPT``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from time import perf_counter

from .core import Profiler, UnsupportedPython, __name__ as _core_name  # noqa: F401


def _run_once(script: str, argv: list[str]) -> int:
    """Run a script like ``python script.py`` without importing runpy.

    ``runpy.run_path`` imports pkgutil/zipimport machinery while monitoring is
    on, which used to add ~50 unrelated raises to every report.  Compiling and
    exec'ing the source directly keeps the report limited to the script and
    whatever it imports itself.
    """
    old_argv = sys.argv
    old_path = list(sys.path)
    sys.argv = [script] + argv
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)) or os.getcwd())
    try:
        with open(script, "rb") as fh:
            source = fh.read()
        code = compile(source, script, "exec")
        globs = {
            "__name__": "__main__",
            "__file__": script,
            "__package__": None,
            "__spec__": None,
            "__loader__": None,
            "__builtins__": __builtins__,
        }
        exec(code, globs, globs)
    except SystemExit as exc:
        rc = exc.code
        if rc is None:
            return 0
        return rc if isinstance(rc, int) else 1
    finally:
        sys.argv = old_argv
        sys.path[:] = old_path
    return 0


def _fmt_ms(seconds):
    if seconds is None:
        return "n/a"
    return "%.3f ms" % (seconds * 1e3)


def _print_report(rep, top):
    print("raisecost %s  CPython %s" % (rep["tool"], rep["python"]))
    print("raises: %d   handled: %d   calibration: %.1f ns/raise"
          % (rep["total_raises"], rep["total_handled"],
             rep["calibration_ns_per_raise"] or 0.0))
    print("estimated exception tax: %s" % _fmt_ms(rep["estimated_tax_seconds"]))
    if rep["baseline_seconds"] is not None:
        print("baseline runtime: %s   tax share: %.2f%%"
              % (_fmt_ms(rep["baseline_seconds"]),
                 100.0 * (rep["tax_share_of_baseline"] or 0.0)))
    print("raise types: %s" % (", ".join("%s=%d" % kv for kv in rep["raise_types"].items()) or "none"))
    print("\ntop raise sites:")
    if not rep["top_raise_sites"]:
        print("  (none)")
    for i, site in enumerate(rep["top_raise_sites"][:top], 1):
        print("  %2d. %s:%d in %s  %d raises  ~%s"
              % (i, site["file"], site["line"], site["function"],
                 site["raises"], _fmt_ms(site["estimated_seconds"])))
    print("\ntop handler sites:")
    if not rep["top_handler_sites"]:
        print("  (none)")
    for i, site in enumerate(rep["top_handler_sites"][:top], 1):
        print("  %2d. %s:%d in %s  %d handled"
              % (i, site["file"], site["line"], site["function"], site["handled"]))


def cmd_run(args):
    script = args.script
    if not os.path.isfile(script):
        print("raisecost: no such script: %s" % script, file=sys.stderr)
        return 2
    baseline = None
    if args.baseline:
        t0 = perf_counter()
        rc = _run_once(script, args.argv)
        baseline = perf_counter() - t0
        if rc != 0:
            print("raisecost: baseline run exited with %d" % rc, file=sys.stderr)
            return rc
    try:
        prof = Profiler(measure_overhead=True)
    except UnsupportedPython as exc:
        print("raisecost: %s" % exc, file=sys.stderr)
        return 3
    with prof:
        rc = _run_once(script, args.argv)
    rep = prof.report(top=args.top, baseline_seconds=baseline)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True)
            fh.write("\n")
    _print_report(rep, args.top)
    if args.json:
        print("\nwrote %s" % args.json)
    return rc


def cmd_calibrate(args):
    from .core import calibrate
    ns = calibrate(repeats=args.repeats)
    print("calibrated raise+catch cost: %.1f ns (differential, median of 5; %d repeats)"
          % (ns, args.repeats))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="raisecost", description=__doc__)
    p.add_argument("--version", action="version", version="raisecost 0.1.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="profile one script and print per-site raise counts")
    r.add_argument("script", help="path to a Python script (run with runpy)")
    r.add_argument("argv", nargs=argparse.REMAINDER, help="arguments passed to the script")
    r.add_argument("--json", metavar="FILE", help="write the full report as JSON")
    r.add_argument("--top", type=int, default=10, help="sites shown per table (default 10)")
    r.add_argument("--baseline", action="store_true",
                   help="run the script once without monitoring first, to compute tax share")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("calibrate", help="print this machine's raise+catch cost")
    c.add_argument("--repeats", type=int, default=20000)
    c.set_defaults(func=cmd_calibrate)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)
