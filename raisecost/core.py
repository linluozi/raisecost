"""Core profiler: per-site raise counting with a calibrated cost estimate.

Uses PEP 669 (``sys.monitoring``) so no tracing overhead is paid on ordinary
lines.  Counting is exact for exceptions raised while the tool is active;
the *cost* estimate is a differential measurement of ``raise + catch`` in the
same interpreter and is reported as an estimate, never as measured time.
"""

from __future__ import annotations

import dis
import sys
from collections import Counter
from time import perf_counter

__all__ = ["Profiler", "UnsupportedPython", "calibrate", "TOOL_NAME"]

TOOL_NAME = "raisecost"
_TOOL_IDS = (2, 3, 4, 5)  # PROFILER, DEBUGGER, COVERAGE, OPTIMIZER
_CALIBRATION_REPEATS = 20000


class UnsupportedPython(RuntimeError):
    """Raised when the running interpreter has no ``sys.monitoring``."""


def _events():
    mon = sys.monitoring
    return mon.events.RAISE, mon.events.EXCEPTION_HANDLED, mon


def _line_maps(code):
    """Two offset->line maps for one code object.

    ``forward`` carries the last seen source line (correct for a raise).
    ``next`` carries the next source line at or after the offset; handler
    offsets point at ``PUSH_EXC_INFO`` which has no line of its own, so the
    next line is the ``except`` clause that will handle the exception.
    """
    forward = {}
    line = code.co_firstlineno
    instrs = list(dis.get_instructions(code))
    for instr in instrs:
        if instr.starts_line is not None:
            line = instr.starts_line
        forward[instr.offset] = line
    nxt = {}
    upcoming = None
    for instr in reversed(instrs):
        if instr.starts_line is not None:
            upcoming = instr.starts_line
        nxt[instr.offset] = upcoming
    return forward, nxt


def calibrate(repeats: int = _CALIBRATION_REPEATS) -> float:
    """Differential cost of one raise+catch, in nanoseconds.

    Compares a loop whose body raises ``KeyError`` with the same loop using
    ``dict.get``.  Both loops pay the call and loop overhead, so the
    difference isolates raise + unwind + handler dispatch.  Median of five
    paired runs is returned; the result is an estimate on this machine.
    """
    empty = {}

    def miss():
        try:
            return empty[0]
        except KeyError:
            return None

    def hit():
        return empty.get(0)

    for fn in (miss, hit):  # warm up
        for _ in range(1000):
            fn()

    samples = []
    for _ in range(5):
        t0 = perf_counter()
        for _ in range(repeats):
            miss()
        miss_s = (perf_counter() - t0) / repeats
        t0 = perf_counter()
        for _ in range(repeats):
            hit()
        hit_s = (perf_counter() - t0) / repeats
        samples.append(max(0.0, miss_s - hit_s) * 1e9)
    samples.sort()
    return samples[len(samples) // 2]


class Profiler:
    """Count raises/handlers per source site for a block of code.

    ``with Profiler() as prof: ...`` enables RAISE and EXCEPTION_HANDLED
    callbacks and releases the monitoring tool id on exit.

    The profiler is not re-entrant and measures only exceptions raised while
    active.  It does not attribute a handler to a particular raise; it counts
    both sides separately (raise site and handler site).
    """

    def __init__(self, measure_overhead: bool = True, repeats: int = _CALIBRATION_REPEATS):
        if not hasattr(sys, "monitoring"):
            raise UnsupportedPython(
                "raisecost needs CPython 3.12+ (sys.monitoring); found %s"
                % sys.version.split()[0]
            )
        self.overhead_ns = calibrate(repeats) if measure_overhead else None
        self._tool_id = None
        self._active = False
        self._lines = {}
        self._raises = Counter()      # (file, line, func) -> count
        self._handlers = Counter()    # (file, line, func) -> count
        self._raise_types = Counter()
        self._total_handled = 0
        self._t0 = None
        self._elapsed = None

    # -- event callbacks -------------------------------------------------
    def _site(self, code, offset, handler=False):
        maps = self._lines.get(code)
        if maps is None:
            maps = self._lines[code] = _line_maps(code)
        if handler:
            line = maps[1].get(offset) or maps[0].get(offset, code.co_firstlineno)
        else:
            line = maps[0].get(offset, code.co_firstlineno)
        return (code.co_filename, line, code.co_name)

    def _on_raise(self, code, offset, exception):
        self._raises[self._site(code, offset)] += 1
        self._raise_types[type(exception).__name__] += 1

    def _on_handled(self, code, offset, exception):
        self._handlers[self._site(code, offset, handler=True)] += 1
        self._total_handled += 1

    # -- lifecycle -------------------------------------------------------
    def start(self):
        if self._active:
            raise RuntimeError("Profiler is already active")
        mon = sys.monitoring
        raise_ev, handled_ev, _ = _events()
        for tid in _TOOL_IDS:
            if mon.get_tool(tid) is None:
                mon.use_tool_id(tid, TOOL_NAME)
                self._tool_id = tid
                break
        if self._tool_id is None:
            raise RuntimeError("no free sys.monitoring tool id")
        mon.register_callback(self._tool_id, raise_ev, self._on_raise)
        mon.register_callback(self._tool_id, handled_ev, self._on_handled)
        self._t0 = perf_counter()
        mon.set_events(self._tool_id, raise_ev | handled_ev)
        self._active = True
        return self

    def stop(self):
        if not self._active:
            return self
        mon = sys.monitoring
        raise_ev, handled_ev, _ = _events()
        mon.set_events(self._tool_id, 0)
        mon.register_callback(self._tool_id, raise_ev, None)
        mon.register_callback(self._tool_id, handled_ev, None)
        mon.free_tool_id(self._tool_id)
        self._elapsed = perf_counter() - self._t0
        self._active = False
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False

    # -- results ---------------------------------------------------------
    @property
    def total_raises(self):
        return sum(self._raises.values())

    def report(self, top: int = 10, baseline_seconds: float | None = None):
        if self._active:
            self.stop()
        sites = [
            {
                "file": site[0],
                "line": site[1],
                "function": site[2],
                "raises": n,
                "estimated_seconds": (n * self.overhead_ns / 1e9) if self.overhead_ns else None,
            }
            for site, n in self._raises.most_common(top)
        ]
        handlers = [
            {"file": s[0], "line": s[1], "function": s[2], "handled": n}
            for s, n in self._handlers.most_common(top)
        ]
        tax = None
        share = None
        if self.overhead_ns is not None:
            tax = self.total_raises * self.overhead_ns / 1e9
            if baseline_seconds:
                share = tax / baseline_seconds
        return {
            "tool": TOOL_NAME,
            "python": sys.version.split()[0],
            "total_raises": self.total_raises,
            "total_handled": self._total_handled,
            "raise_types": dict(self._raise_types.most_common()),
            "calibration_ns_per_raise": self.overhead_ns,
            "estimated_tax_seconds": tax,
            "baseline_seconds": baseline_seconds,
            "tax_share_of_baseline": share,
            "profiled_seconds": self._elapsed,
            "top_raise_sites": sites,
            "top_handler_sites": handlers,
        }
