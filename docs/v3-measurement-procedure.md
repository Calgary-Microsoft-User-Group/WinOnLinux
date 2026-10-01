# V3 measurement procedure: NFR-1 cold start and NFR-4 idle RSS

This document is a **procedure**, not a result. No measurement has been taken yet — this machine
has no Python or PyGObject installed and cannot run the application. Per CLAUDE.md's "verify,
don't assert," treat NFR-1 and NFR-4 as **decided-but-unverified** for the PyGObject stack (D-16)
until someone runs this procedure on a real Linux + PyGObject environment and records the result.

This satisfies tasks.md task 6.1 (document the RSS measurement procedure; cold-start timing is
already recorded by `app.py`'s `_first_activate`, see below). Task 6.2 — actually capturing the
first V3 baseline measurement — remains unchecked; it requires a Linux host with PyGObject
installed, which does not exist in this environment.

## What's being measured

- **NFR-1**: cold start to interactive main window, p95 ≤ 2 s.
- **NFR-4**: idle resident set size (RSS), ≤ 250 MB.

Both are verification V3 in spec.md, and both can fire decision D-16's revisit trigger (fallback
stack: .NET + Avalonia + MSAL.NET) if they fail. Keep that possibility in mind when reading the
result — a failing measurement is a legitimate, expected outcome to report honestly, not something
to explain away.

## Prerequisites

- A real Linux desktop session (X11 or Wayland) with GTK4, libadwaita, and PyGObject installed —
  the same environment `tests/manual/app_uniqueness.md` requires.
- The `winonlinux` package installed (an editable venv install is fine): `pip install -e .`
- No other `winonlinux` process already running.
- Standard process-inspection tools available: `ps` (procps) is enough; `pgrep`/`pkill` are
  convenient but not required.

## NFR-1: cold-start-to-interactive timing

`WinOnLinuxApplication._first_activate` in `src/winonlinux/app.py` already records this: it takes
a `time.monotonic()` reference as early as possible in `main()` (before the `Adw.Application` is
even constructed) and, the first time the main window is presented, logs the elapsed time via the
stdlib `logging` module at INFO level as:

```
cold_start_ms=<N>
```

### Procedure

1. Ensure no `winonlinux` process is running: `pgrep -fa winonlinux` should print nothing.
2. Launch the application from a cold shell and capture its log output, e.g.:

   ```bash
   python -m winonlinux 2>&1 | tee /tmp/winonlinux-coldstart-$(date +%s).log
   ```

3. Once the window appears, quit the application (close the window, or SIGTERM the process).
4. Extract the timing:

   ```bash
   grep -o 'cold_start_ms=[0-9]*' /tmp/winonlinux-coldstart-*.log
   ```

5. Repeat steps 1–4 for **at least 20 cold-start runs** (a fresh process each time — do not reuse
   a warm page/inode cache run as a substitute for another; note whether the OS disk cache was hot
   or cold across runs, since that affects reproducibility). Record every value.
6. Compute the p95 (the value below which 95% of the recorded runs fall — with 20 runs, sort them
   and take the 19th value; more runs give a less noisy p95).
7. Compare against the NFR-1 threshold: **p95 ≤ 2000 ms**.

### Notes

- `cold_start_ms` is emitted through `logging`, not `winonlinux.logging_setup`, and is a plain
  integer — nothing in this line is redaction-sensitive, so it is safe to grep from raw logs.
- If the number of samples or the run environment changes between sessions (different hardware,
  cold vs. warm disk cache, a Flatpak sandbox vs. a bare venv), record that alongside the numbers;
  do not merge samples from materially different environments into one p95.

## NFR-4: idle RSS

There is no in-app instrumentation for this (RSS is an OS-level property of the process, not
something the process usefully self-reports). Measure it externally with `ps`.

### Procedure

1. Launch the application: `python -m winonlinux &`
2. Let it sit idle at the main window for **at least 60 seconds** with no user interaction, so any
   startup-only allocations (import machinery, initial GTK widget realization, the FreeRDP probe
   task, JIT/bytecode caches) have settled before sampling.
3. Find the process and sample its RSS in kilobytes:

   ```bash
   ps -o rss= -p $(pgrep -f winonlinux)
   ```

   (If `pgrep -f winonlinux` matches more than one line — e.g. a leftover shell wrapper — narrow
   it with `pgrep -f 'python -m winonlinux'` or inspect `pgrep -fa winonlinux` and pick the actual
   interpreter PID by hand.)

4. Convert kilobytes to megabytes (`ps -o rss=` reports KiB on Linux): `RSS_MB = RSS_KB / 1024`.
5. Sample at least 3 times, a few seconds apart, while still idle, to confirm the value has
   plateaued rather than still climbing (a still-climbing RSS after 60 s idle is itself worth
   recording — it may indicate a leak, which is a separate finding from the NFR-4 threshold check).
6. Compare against the NFR-4 threshold: **idle RSS ≤ 250 MB**.
7. Quit the application (`kill <pid>` or close the window) once sampling is done.

### Alternative tooling

`smem`, `/proc/<pid>/status` (`VmRSS:` line), or GNOME System Monitor are equally valid if `ps`
is unavailable — record which tool was used, since RSS accounting can differ slightly between
them (e.g. shared-library pages counted differently).

## Where to file the result

1. Record the raw samples (all cold-start runs, all RSS samples) and the computed p95/idle-RSS
   figures verbatim — do not just record pass/fail.
2. File the result against **Gate STACK** in Linear, referencing issue **BIG-264** (the V3
   verification tracking issue).
3. State explicitly whether each of NFR-1 and NFR-4 **passed** or **failed** its threshold.
4. If either fails: this fires decision **D-16**'s revisit trigger (spec.md §14). Do not
   soft-pedal a failure as "close enough" — a genuine failure here is a legitimate outcome per
   this change's design.md risk register, and D-16 pre-names the fallback stack (.NET + Avalonia +
   MSAL.NET) to evaluate if it fires.
5. Comment on the Linear issues this measurement resolves or informs, per CLAUDE.md's "Keep
   Linear and the spec in sync" — say which decision (D-16, and V3 specifically) the measurement
   result speaks to.
6. If spec.md's decision register or risk section needs updating as a result (e.g. marking V3 as
   verified, or opening a stack-reversal discussion), make that edit as a separate, explicit step
   — do not let the measurement sit only in Linear while spec.md still reads "unverified."

## Explicitly out of scope for this document

- Taking the actual measurement — this repository/environment has no Python or PyGObject
  installed and cannot run `winonlinux` at all (see CLAUDE.md's "IMPORTANT ENVIRONMENT
  CONSTRAINT" for this task). This document only prepares the procedure so it is repeatable the
  moment a suitable Linux host is available.
- Statistical methodology beyond a plain sorted-sample p95 — if more rigor is wanted later
  (confidence intervals, more samples, automated harness), that is a follow-up, not a blocker for
  the first baseline.
