# Manual test: application uniqueness

Covers spec.md's "Single application instance" requirement (add-app-foundation change,
FR-4-AC-7, §6.3), scenario **"Second launch activates the running instance"**, and tasks.md
task 2.4's manual-verification item.

This cannot be exercised by the unit-test suite: `Gio.Application` uniqueness needs a real D-Bus
session bus and (for the window to actually present) a real display server, both of which are
outside the §13.4 CI boundary ("tests requiring ... a display server SHALL be excluded from
per-commit CI"). Run this by hand on a Linux desktop session (X11 or Wayland) with PyGObject,
GTK4, and libadwaita installed.

## Preconditions

- A graphical session is active (you can see a desktop).
- The `winonlinux` package is installed (editable install from a venv is fine) so
  `python -m winonlinux.app` runs.
- No other WinOnLinux process is currently running (`pgrep -fa winonlinux.app` shows nothing).

## Steps

1. **First launch.** In a terminal, run:

   ```bash
   python -m winonlinux.app
   ```

   **Expected:** a window titled "WinOnLinux" appears on screen. The terminal's log output
   includes a line of the form `cold_start_ms=<N>` at INFO level (this is task 6.1's NFR-1
   instrumentation, not part of this scenario's pass/fail criteria but useful to sanity-check
   while you're here).

2. **Second launch.** Leave the first process running. In a **second** terminal, run the exact
   same command:

   ```bash
   python -m winonlinux.app
   ```

   **Expected:**
   - The second command returns to the shell prompt almost immediately (well under a second) --
     it does not sit there running a second copy of the app.
   - The *existing* window from step 1 is raised/focused (brought to the foreground), not a new
     second window.
   - `pgrep -fa winonlinux.app` (or `ps aux | grep winonlinux`) still shows only **one**
     `winonlinux.app` process -- the second invocation's process has already exited.
   - The second terminal's output does **not** contain a second `cold_start_ms=` line -- the
     second process never reaches the window-creation code path at all (see `app.py`'s module
     docstring: a forwarded activation never locally emits "activate" in the second process, so
     nothing there constructs a token cache, state store, or task registry).

3. **Repeat step 2 two or three more times.** Each time, the same single window should simply be
   re-presented; the process count must stay at one throughout.

4. **Quit and relaunch.** Close the window (or send the running process SIGTERM/SIGINT). Confirm
   the process exits. Run the launch command once more and confirm a fresh window appears (i.e.
   the *first*-instance path still works after a clean shutdown, not just the second-instance
   path).

## Pass criteria

- Exactly one process, and one window, exist no matter how many times the launch command is run
  concurrently with an already-running instance.
- A second launch never re-initializes application state (no second `cold_start_ms=` log line,
  no evidence of a second state-store or token-cache initialization in the logs).
- After the single running instance quits, launching again starts a genuinely fresh instance.

## If it fails

- If a *second window* appears: `Gio.ApplicationFlags.NON_UNIQUE` may have been set somewhere, or
  the application ID differs between the two invocations (check `APPLICATION_ID` in `app.py` is
  the literal constant, not something computed per-process). Record the failure against
  FR-4-AC-7 and this scenario.
- If no window is raised on the second launch (nothing visibly happens): check that the
  `"activate"` handler is actually connected and that `_window.present()` is reached; a D-Bus
  session-bus issue in the test environment (e.g. a container without one) can also cause this --
  distinguish an environment problem from an application bug before filing.

## Manual test: GTK stays responsive during a blocking call

Covers spec.md's "Blocking call does not stall the loop" scenario as it actually manifests in the
real GLib main loop -- `test_task_registry.py`'s automated version of this exercises
`run_blocking`/`TaskRegistry` in isolation with a plain asyncio loop, not `asyncio_bridge`'s GLib
pump, which cannot be exercised without PyGObject installed (see this file's own module
docstring). This is the one place that integration actually needs eyes on a real display.

### Steps

1. Launch the app (`python -m winonlinux.app`). At startup, `_first_activate()` schedules the
   FreeRDP version probe as a background task, which spawns `xfreerdp /version` via
   `run_in_executor` -- a real (if normally brief) subprocess call running off the loop thread.
2. Immediately after the window appears, try to move/resize the window and interact with it
   (click anywhere it accepts input) while that probe is still in flight.
3. **Expected:** the window remains responsive to input the whole time -- no visible freeze, even
   momentarily. If `xfreerdp` is not installed, this step still exercises the absent-binary path
   quickly; to actually observe a slower blocking call, temporarily rename/replace `xfreerdp` with
   a short wrapper script that sleeps for 1-2 seconds before exiting, relaunch, and confirm the
   window still doesn't freeze during that window.

### If it fails

If the window freezes for the duration of the blocking call, `asyncio_bridge.py`'s heartbeat
(`_PUMP_HEARTBEAT_SECONDS`) is not doing its job -- check that `_reschedule_heartbeat` is actually
being called from `install()` before the idle pump starts, and that `_PUMP_HEARTBEAT_SECONDS`
hasn't been set to something large. Record the failure against the "Blocking call does not stall
the loop" scenario.
