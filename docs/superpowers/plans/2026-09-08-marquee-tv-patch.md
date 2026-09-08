# Marquee.tv Three-Part Patch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three independent improvements to Marquee.tv — (1) stop relaunching a stream that just ended, (2) run the daemon whenever the UI is open so info is always visible, with Start/Stop toggling playback only, (3) time-of-day priority rules in `streamers.txt`.

**Architecture:** All three extend the existing daemon/UI split and file-based IPC (`.status.json`, `.control`, `.last_seen.json`). §1 adds end-of-stream detection + a per-streamer cooldown to the daemon loop. §2 adds an in-memory `playback_enabled` flag gating the daemon's launch logic, plus two new `.control` tokens. §3 turns the parsed priority list into a structure that resolves against the current time, re-resolved by both daemon and UI each tick.

**Tech Stack:** Python 3, Textual 8.2.8 (UI), pytest (+ pytest-asyncio, `asyncio_mode = "auto"`). No linter/type-checker is configured; `.venv/bin/pytest` is the only quality gate. Twitch data comes from the `twitch` CLI via `subprocess`.

**Spec:** `docs/superpowers/specs/2026-09-08-marquee-tv-patch-design.md`

**Branch:** `feature/marquee-patch` (already created, spec already committed).

**Test command (all tasks):** `cd /mnt/Wrestler_Ted/claudes_room/marquee-tv && .venv/bin/pytest -q`
Single test: `.venv/bin/pytest tests/test_FILE.py::test_NAME -v`

**Do not** stage or commit `streamers.txt` — it has a pre-existing uncommitted edit unrelated to this work. Stage files explicitly by path in every commit step.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `marquee_daemon.py` | Background daemon: poll, launch, switch, status | Modified — §1 cooldown/death detection, §2 playback flag + loop gate, §3 time-resolved priority + reorder switching |
| `priority_list.py` | Parse `streamers.txt` into an ordered model | Modified — §3 `TimeBlock`/`TimeRule`, block parsing + validation, `resolve_entries()` |
| `marquee_ui.py` | Textual dashboard | Modified — §2 start-daemon-on-mount, S/X → play/stop, header states, quit wording; §3 render time blocks + warnings |
| `marquee_render.py` | Pure line rendering for the UI | Modified — §3 `RowData` gains separator label/warning fields |
| `marquee.sh` | Entrypoint / control script | Modified — §2 `play` / `stop-playback` subcommands + help |
| `marquee.service` | systemd user unit | Unchanged (README note only) |
| `README.md` | User docs | Modified — §2 behaviour change, §3 `@block` syntax |
| `tests/test_marquee_daemon.py` | Daemon unit tests | Modified — §1, §2, §3 daemon tests |
| `tests/test_priority_list.py` | Parser tests | Modified — §3 block parsing/validation/resolution |
| `tests/test_marquee_ui.py` | UI tests | Modified — §2, §3 UI tests |
| `tests/test_marquee_render.py` | Render tests | Modified — §3 separator label rendering |

---

# PHASE 1 — §1: Stop relaunching a stream that just ended

## Task 1: Targeted single-stream live query helper

**Files:**
- Modify: `marquee_daemon.py` (add method to `TwitchTVController`, add constants near line 28-30)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_marquee_daemon.py`:

```python
def test_query_single_live_returns_info_when_live(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert "user_login=alpha" in cmd
        return mock.Mock(returncode=0, stdout=json.dumps({"data": [
            {"user_login": "alpha", "title": "t", "game_name": "g", "viewer_count": 3,
             "started_at": "2026-01-01T00:00:00Z"},
        ]}))
    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    assert ctrl._query_single_live("alpha") == {
        "title": "t", "game": "g", "viewers": 3, "started_at": "2026-01-01T00:00:00Z",
    }


def test_query_single_live_returns_none_when_offline(monkeypatch):
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: mock.Mock(returncode=0, stdout=json.dumps({"data": []})))
    ctrl = TwitchTVController.__new__(TwitchTVController)
    assert ctrl._query_single_live("alpha") is None


def test_query_single_live_returns_none_on_unparseable_output(monkeypatch):
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: mock.Mock(returncode=1, stdout="not json", stderr="boom"))
    ctrl = TwitchTVController.__new__(TwitchTVController)
    assert ctrl._query_single_live("alpha") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_query_single_live_returns_info_when_live -v`
Expected: FAIL — `AttributeError: 'TwitchTVController' object has no attribute '_query_single_live'`

- [ ] **Step 3: Add constants and the method**

In `marquee_daemon.py`, after `GRACE_PERIOD = 300` (line ~30) add:

```python
RELAUNCH_COOLDOWN = 300  # seconds a just-ended streamer is skipped for before we'll relaunch it
MIN_REAL_SESSION = 30  # a stream that ran shorter than this "never really started"
```

Add this method to `TwitchTVController` (put it right after `get_live_streams`):

```python
def _query_single_live(self, streamer: str) -> Optional[Dict]:
    """One targeted Twitch query for a single streamer's live info.
    Returns the stream-info dict, or None if offline / query failed.
    Mirrors get_live_streams' "judge success by whether stdout parses,
    not the exit code" handling (the twitch CLI can crash post-output)."""
    try:
        result = subprocess.run(
            ["twitch", "api", "get", "streams", "-q", f"user_login={streamer}"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    streams = data.get("data", [])
    if not streams:
        return None
    s = streams[0]
    return {
        "title": s["title"], "game": s["game_name"],
        "viewers": s["viewer_count"], "started_at": s.get("started_at"),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -k query_single_live -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): add targeted single-stream live query helper

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Per-streamer relaunch cooldown

**Files:**
- Modify: `marquee_daemon.py` — `get_highest_priority_live` (line ~248-253)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_highest_priority_skips_cooled_down_streamer(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("marquee_daemon.time.time", lambda: now[0])
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {"alpha": 1200.0}
    live = {"alpha": {}, "beta": {}}
    assert ctrl.get_highest_priority_live(live) == "beta"


def test_highest_priority_returns_streamer_after_cooldown_expires(monkeypatch):
    now = [1300.0]  # past the 1200 expiry
    monkeypatch.setattr("marquee_daemon.time.time", lambda: now[0])
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {"alpha": 1200.0}
    assert ctrl.get_highest_priority_live({"alpha": {}, "beta": {}}) == "alpha"


def test_highest_priority_no_cooldowns_attr_safe():
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha"]
    ctrl.cooldowns = {}
    assert ctrl.get_highest_priority_live({"alpha": {}}) == "alpha"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_highest_priority_skips_cooled_down_streamer -v`
Expected: FAIL — returns `"alpha"` instead of `"beta"`

- [ ] **Step 3: Implement**

In `marquee_daemon.py`, replace `get_highest_priority_live`:

```python
def get_highest_priority_live(self, live_streams: Dict) -> Optional[str]:
    """Return the highest priority streamer that's currently live and not
    on relaunch cooldown (see _handle_stream_death)."""
    now = time.time()
    for streamer in self.priority_list:
        if streamer in live_streams and self.cooldowns.get(streamer, 0.0) < now:
            return streamer
    return None
```

Also initialise the attribute in `__init__` — add near `self.live_streams` (line ~65):

```python
self.cooldowns: Dict[str, float] = {}  # streamer -> unix expiry; skipped by get_highest_priority_live
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -k highest_priority -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): skip cooled-down streamers in priority selection

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: End-of-stream detection

**Files:**
- Modify: `marquee_daemon.py` — new `_handle_stream_death`, `launch_stream` (line ~261-332), `__init__` (line ~55-72)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
def _dead_ctrl(monkeypatch, still_live, ran_for):
    monkeypatch.setattr("marquee_daemon.time.time", lambda: 1000.0)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {}
    ctrl.live_streams = {"alpha": {"title": "t"}, "beta": {"title": "t2"}}
    ctrl.current_stream = "alpha"
    ctrl.current_stream_started_at = 1000.0 - ran_for  # monotonic base; helper patches monotonic too
    ctrl.last_api_update = 999.0
    monkeypatch.setattr("marquee_daemon.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr(ctrl, "_query_single_live",
                        lambda s: {"title": "t"} if still_live else None)
    return ctrl


def test_stream_death_when_offline_cools_down_and_drops(monkeypatch):
    ctrl = _dead_ctrl(monkeypatch, still_live=False, ran_for=3600)
    ctrl._handle_stream_death()
    assert ctrl.cooldowns["alpha"] == 1000.0 + 300  # RELAUNCH_COOLDOWN
    assert "alpha" not in ctrl.live_streams
    assert ctrl.last_api_update == 0.0  # forced refresh next tick


def test_stream_death_short_session_still_live_cools_down(monkeypatch):
    ctrl = _dead_ctrl(monkeypatch, still_live=True, ran_for=5)  # < MIN_REAL_SESSION
    ctrl._handle_stream_death()
    assert "alpha" in ctrl.cooldowns
    assert "alpha" not in ctrl.live_streams


def test_stream_death_real_session_still_live_is_deliberate_close(monkeypatch):
    ctrl = _dead_ctrl(monkeypatch, still_live=True, ran_for=3600)  # >= MIN_REAL_SESSION
    ctrl._handle_stream_death()
    assert "alpha" not in ctrl.cooldowns  # no cooldown — user hand-closed it
    assert "alpha" in ctrl.live_streams   # left in place for next-priority logic
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_stream_death_when_offline_cools_down_and_drops -v`
Expected: FAIL — `AttributeError: ... '_handle_stream_death'`

- [ ] **Step 3: Implement**

In `marquee_daemon.py` `__init__`, add near `self.current_socket_path` (line ~67):

```python
self.current_stream_started_at: Optional[float] = None
self._handled_current_death: bool = False
```

In `launch_stream`, at the end (right after `self.current_stream = streamer`, line ~329) add:

```python
self.current_stream_started_at = time.monotonic()
self._handled_current_death = False
```

Add the method (after `is_stream_alive`, line ~259):

```python
def _handle_stream_death(self) -> None:
    """Called once when the current mpv/streamlink process has exited.
    Distinguishes "the stream ended" (cool the streamer down so we don't
    immediately relaunch a dead channel while the API still reports it
    live) from "the user hand-closed mpv to skip" (fall through to normal
    next-priority selection, no cooldown)."""
    streamer = self.current_stream
    if streamer is None:
        return
    self.last_api_update = 0.0  # force a full API refresh next tick
    ran_for = time.monotonic() - (self.current_stream_started_at or 0.0)
    still_live = self._query_single_live(streamer) is not None
    if (not still_live) or ran_for < MIN_REAL_SESSION:
        self.cooldowns[streamer] = time.time() + RELAUNCH_COOLDOWN
        self.live_streams.pop(streamer, None)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {streamer} ended "
              f"(ran {int(ran_for)}s, still_live={still_live}) — cooling down {RELAUNCH_COOLDOWN}s")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -k stream_death -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): detect stream-end vs hand-close on mpv exit

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Wire death detection into the loop; manual switch clears cooldown

**Files:**
- Modify: `marquee_daemon.py` — `run()` (line ~406-513), control-signal handling (line ~449-459)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_explicit_switch_clears_target_cooldown(monkeypatch):
    now = [2000.0]
    monkeypatch.setattr("marquee_daemon.time.time", lambda: now[0])
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {"beta": 9999.0}
    ctrl.clear_cooldown("beta")
    assert "beta" not in ctrl.cooldowns
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_explicit_switch_clears_target_cooldown -v`
Expected: FAIL — `AttributeError: ... 'clear_cooldown'`

- [ ] **Step 3: Implement**

Add helper to `TwitchTVController` (after `_handle_stream_death`):

```python
def clear_cooldown(self, streamer: str) -> None:
    self.cooldowns.pop(streamer, None)
```

In `run()`, insert the death check immediately before `if not self.is_stream_alive():` (line ~464):

```python
if (self.current_stream is not None and self.current_process is not None
        and not self.is_stream_alive() and not self._handled_current_death):
    self._handled_current_death = True
    self._handle_stream_death()
    highest_priority = self.get_highest_priority_live(self.live_streams)
```

(The re-assignment of `highest_priority` matters — `_handle_stream_death` may have just dropped the dead streamer from `live_streams`.)

In the control-signal handling block, where a targeted switch is accepted (line ~458, the `elif target and target in self.live_streams:` branch), add a cooldown clear:

```python
elif target and target in self.live_streams:
    self.clear_cooldown(target)
    control_target, control_mode = target, mode
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -q`
Expected: all pass

- [ ] **Step 5: Manual smoke check (document only, no code)**

Note in the commit body that a live smoke test is: start a stream via the UI, end it from the Twitch side (or `pkill` the streamlink process), confirm Chatterino does **not** flap 3× and the daemon logs `… ended … cooling down`.

- [ ] **Step 6: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): stop relaunching a stream that just ended

Wires _handle_stream_death into the loop on mpv exit and re-picks the
next priority from the pruned live set. An explicit switch:<streamer>
clears that streamer's cooldown.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

# PHASE 2 — §2: Always-on daemon, Start/Stop toggle playback only

## Task 5: `.control` learns `play` / `stop` tokens

**Files:**
- Modify: `marquee_daemon.py` — `parse_control_command` (line ~37-52)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_play_token():
    assert parse_control_command("play") == (None, "play")

def test_parse_stop_token():
    assert parse_control_command("stop") == (None, "stop")

def test_parse_play_is_case_insensitive():
    assert parse_control_command("  PLAY \n") == (None, "play")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_parse_play_token -v`
Expected: FAIL — returns `None`

- [ ] **Step 3: Implement**

In `parse_control_command`, after `if raw == "switch": return ("", None)`:

```python
    if raw in ("play", "stop"):
        return (None, raw)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -k "parse_" -v`
Expected: all pass (existing switch tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): parse play/stop control tokens

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: `playback_enabled` flag + teardown

**Files:**
- Modify: `marquee_daemon.py` — `__init__` (line ~55-72), new `_stop_playback`, `save_status` (line ~388-404)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_stop_playback_tears_down_and_clears_state(monkeypatch):
    killed = []
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: killed.append(cmd) or mock.Mock(returncode=0))
    proc = mock.Mock()
    proc.poll.return_value = None
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.playback_enabled = True
    ctrl.current_process = proc
    ctrl.current_stream = "alpha"
    ctrl.switching_soon = "beta"
    ctrl.grace_period_start = "whatever"
    ctrl.manual_override = True
    ctrl._stop_playback()
    assert ctrl.playback_enabled is False
    assert ctrl.current_stream is None
    assert ctrl.switching_soon is None
    assert ctrl.grace_period_start is None
    assert ctrl.manual_override is False
    proc.terminate.assert_called_once()
    assert ["pkill", "-x", "chatterino"] in killed


def test_save_status_includes_playback_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr("marquee_daemon.STATUS_FILE", tmp_path / ".status.json")
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.playback_enabled = False
    ctrl.current_stream = None
    ctrl.current_process = None
    ctrl.switching_soon = None
    ctrl.grace_period_start = None
    ctrl.live_streams = {}
    ctrl.save_status()
    data = json.loads((tmp_path / ".status.json").read_text())
    assert data["playback_enabled"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_save_status_includes_playback_enabled -v`
Expected: FAIL — `KeyError: 'playback_enabled'`

- [ ] **Step 3: Implement**

`__init__`, near `self.manual_override` (line ~63):

```python
self.playback_enabled: bool = False  # daemon boots in monitor-only mode
```

Add method (after `launch_stream`):

```python
def _stop_playback(self) -> None:
    """Stop playing a stream but keep monitoring. Toggled by the `stop`
    control token / the UI's (X)."""
    self.playback_enabled = False
    if self.current_process is not None:
        try:
            self.current_process.terminate()
            self.current_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.current_process.kill()
    subprocess.run(["pkill", "-x", "chatterino"], capture_output=True)
    if self.current_socket_path is not None:
        self.current_socket_path.unlink(missing_ok=True)
        self.current_socket_path = None
    self.current_process = None
    self.current_stream = None
    self.switching_soon = None
    self.grace_period_start = None
    self.manual_override = False
```

In `save_status`, add to the `status` dict:

```python
'playback_enabled': self.playback_enabled,
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -k "stop_playback or playback_enabled" -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): playback_enabled flag with teardown helper

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Gate the loop on `playback_enabled`; kill Chatterino on shutdown

**Files:**
- Modify: `marquee_daemon.py` — `run()` (line ~406-526)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

The launch half of `run()` is loop-shaped and hard to unit-test directly, so extract the play/stop control application into a testable method and test that.

```python
def test_apply_control_play_enables_playback():
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.playback_enabled = False
    ctrl._apply_playback_token("play")
    assert ctrl.playback_enabled is True


def test_apply_control_stop_calls_stop_playback(monkeypatch):
    ctrl = TwitchTVController.__new__(TwitchTVController)
    called = []
    monkeypatch.setattr(ctrl, "_stop_playback", lambda: called.append(True))
    ctrl._apply_playback_token("stop")
    assert called == [True]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_apply_control_play_enables_playback -v`
Expected: FAIL — `AttributeError: ... '_apply_playback_token'`

- [ ] **Step 3: Implement**

Add method (after `_stop_playback`):

```python
def _apply_playback_token(self, token: str) -> None:
    if token == "play":
        self.playback_enabled = True
    elif token == "stop":
        self._stop_playback()
```

Now edit `run()`. After `control_signal = self.check_control_signal()` and the `if control_signal is not None:` unpacking (line ~451), handle the new tokens **before** the streamer branches:

```python
if control_signal is not None:
    target, mode = control_signal
    if target is None and mode in ("play", "stop"):
        self._apply_playback_token(mode)
    elif mode == "oneshot":
        pass  # UI handles one-shot streams entirely on its own
    elif target == "" and mode is None:
        control_target, control_mode = highest_priority, None
    elif target and target in self.live_streams:
        self.clear_cooldown(target)
        control_target, control_mode = target, mode
```

Immediately after the whole control-signal block, before `if not self.is_stream_alive():`, add the gate:

```python
# An explicit switch:<streamer> implies "play".
if control_target is not None:
    self.playback_enabled = True

if not self.playback_enabled:
    self.switching_soon = None
    self.grace_period_start = None
    self.save_status()
    time.sleep(CHECK_INTERVAL)
    continue
```

In the `run()` cleanup block at the end (line ~522-526), add a Chatterino kill:

```python
if self.current_process:
    self.current_process.terminate()
subprocess.run(["pkill", "-x", "chatterino"], capture_output=True)
STATUS_FILE.unlink(missing_ok=True)
CONTROL_FILE.unlink(missing_ok=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -q`
Expected: all pass

- [ ] **Step 5: Manual smoke check (document only)**

`.venv/bin/python3 marquee_daemon.py` in a terminal → confirm it prints the startup banner, polls, writes `.status.json` with `"playback_enabled": false`, and does **not** launch mpv. `echo play > .control` → mpv launches. `echo stop > .control` → mpv + Chatterino close, daemon keeps polling.

- [ ] **Step 6: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): monitor-only unless playback is enabled

Daemon now boots monitoring the priority list and writing status without
launching anything. `play`/`stop` control tokens (and any explicit
switch:<streamer>) toggle actual playback.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: `marquee.sh` — `play` / `stop-playback` subcommands

**Files:**
- Modify: `marquee.sh` (case block line ~28-114)

- [ ] **Step 1: Add the subcommands**

In `marquee.sh`, add two cases before the `watch)` case:

```bash
    play)
        echo "play" > "$CONTROL_FILE"
        echo "Signaled daemon to start playback"
        ;;

    stop-playback)
        echo "stop" > "$CONTROL_FILE"
        echo "Signaled daemon to stop playback (daemon keeps monitoring)"
        ;;
```

- [ ] **Step 2: Update the help text**

In the `*)` help heredoc, under `Commands:`, add:

```
  play           Start playing the highest-priority stream (daemon keeps running)
  stop-playback  Stop playing, but keep the daemon monitoring
```

And change the description of `start` / `stop` to:

```
  start      Start the daemon (monitors the priority list; does not auto-play)
  stop       Stop the daemon entirely
```

- [ ] **Step 3: Verify executable + syntax**

Run: `bash -n marquee.sh && ls -l marquee.sh`
Expected: no output from `-n` (syntax OK); file mode shows `-rwxr-xr-x`. If not executable: `chmod +x marquee.sh`.

- [ ] **Step 4: Commit**

```bash
git add marquee.sh
git commit -m "feat(cli): add play / stop-playback subcommands

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: UI — start daemon on mount, S/X toggle playback, header states

**Files:**
- Modify: `marquee_ui.py` — `on_mount` (line ~249-255), `action_start_service` (line ~759-767), `action_stop_service` (line ~769-779), `stop_service` (line ~753-757), `_load_status_file` (line ~356-377), `_header_data` (line ~425-440), `__init__` (line ~222-244), `QuitConfirmModal.OPTIONS` (line ~74-77)
- Test: `tests/test_marquee_ui.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_on_mount_starts_daemon_when_absent(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer|Test\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    started = []
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: started.append(True))
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: False)
    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
    assert started == [True]


@pytest.mark.asyncio
async def test_s_writes_play_x_writes_stop(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer|Test\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: None)
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)
    killed = []
    monkeypatch.setattr("marquee_ui.subprocess.run", lambda *a, **kw: killed.append(a))
    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert control_file.read_text() == "play"
        await pilot.press("x")
        await pilot.pause()
        assert control_file.read_text() == "stop"
    # X must not blanket-pkill mpv (would kill ad-hoc one-shots)
    assert not any("mpv" in str(a) for a in killed)


@pytest.mark.asyncio
async def test_header_shows_playback_stopped_when_daemon_up_flag_off(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer|Test\n")
    status_file = tmp_path / ".status.json"
    status_file.write_text(json.dumps({
        "current_stream": None, "stream_alive": False, "live_streams": {},
        "playback_enabled": False,
    }))
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: None)
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)
    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Playback stopped" in app.query_one("#frame").content
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -k "on_mount_starts_daemon or writes_play or playback_stopped" -v`
Expected: all FAIL

- [ ] **Step 3: Implement**

`__init__` — add near `self.stream_alive = False` (line ~232):

```python
self.playback_enabled = False
```

`on_mount` — insert the daemon-start before `self.refresh_data(force=True)`:

```python
def on_mount(self) -> None:
    self.load_entries()
    self._terminal_width = self.size.width
    self._terminal_height = self.size.height
    if not self.daemon_running():
        self.start_service()  # always monitor so info is visible without playing
    self.refresh_data(force=True)
    self.render_frame()
    self.set_interval(REFRESH_INTERVAL, self.tick)
```

`_load_status_file` — after `self.stream_alive = status.get('stream_alive', False)` (line ~364):

```python
self.playback_enabled = status.get('playback_enabled', False)
```

`_header_data` — replace the method body's first two guards:

```python
def _header_data(self) -> HeaderData:
    if not self._daemon_was_running:
        return HeaderData(active=False, inactive_message="Daemon Offline")
    if not self.playback_enabled:
        return HeaderData(active=False, inactive_message="Playback stopped — press (S) to start")
    if not self.current_stream:
        return HeaderData(active=False)
    info = self.live_streams.get(self.current_stream, {})
    return HeaderData(
        active=True,
        name=self.current_stream,
        is_live=self.current_stream in self.live_streams,
        viewers=info.get('viewers'),
        game=info.get('game', ''),
        started_at=info.get('started_at'),
        title=info.get('title', ''),
        ad_hoc_mode=self.ad_hoc_mode,
    )
```

`action_start_service` — replace:

```python
async def action_start_service(self) -> None:
    self.last_footer_key = "s"
    if not self.daemon_running():
        import asyncio
        await asyncio.to_thread(self.start_service)
    with open(CONTROL_FILE, 'w') as f:
        f.write("play")
    self.render_frame()
```

`action_stop_service` — replace:

```python
async def action_stop_service(self) -> None:
    self.last_footer_key = "x"
    with open(CONTROL_FILE, 'w') as f:
        f.write("stop")
    self.current_stream = None
    self.stream_alive = False
    self.render_frame()
```

`stop_service` — this is still used by the quit modal's "stop" path; narrow it so it no longer blanket-kills mpv:

```python
def stop_service(self) -> None:
    import subprocess as sp
    sp.run([str(SCRIPT_DIR / "marquee.sh"), "stop"], capture_output=True)
    sp.run(["pkill", "-x", "chatterino"], capture_output=True)
```

`QuitConfirmModal.OPTIONS`:

```python
    OPTIONS = [
        ("keep", "Quit — leave daemon running"),
        ("stop", "Quit and stop the daemon"),
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -q`
Expected: all pass. **Existing tests that assert old behaviour will need updating** — specifically any that press `s`/`x` and check for a stream launch, or check the old quit-option strings. Update those tests to the new semantics (S/X write `play`/`stop`; quit options `keep`/`stop`). Run the full file and fix each failure to match the new contract.

- [ ] **Step 5: Commit**

```bash
git add marquee_ui.py tests/test_marquee_ui.py
git commit -m "feat(ui): always start daemon; S/X toggle playback only

on_mount ensures the daemon is running so stream info shows without a
stream playing. (S) writes `play`, (X) writes `stop`. Header shows a
'Playback stopped' idle state. Quit modal wording updated.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: README — §2 behaviour change

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the docs**

- In **Features**, adjust the "Priority-based streaming" / usage bullets to note the daemon now starts automatically with the UI and only monitors until you press `(S)`.
- In the **Usage** section (TUI keys), document `(S)tart` = begin playback, `(X)Stop` = stop playback (daemon keeps monitoring).
- Add a line under the systemd section: enabling `marquee.service` runs the daemon in monitor-only mode; it won't auto-play until it receives `play` (via the UI's `(S)` or `marquee.sh play`).
- Document the new `marquee.sh play` / `stop-playback` subcommands in the command list.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: daemon is always-on; S/X toggle playback

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

# PHASE 3 — §3: Time-based priority rules

## Task 11: Time-window helpers in `priority_list.py`

**Files:**
- Modify: `priority_list.py` — imports, new `_parse_hhmm`, `_in_window`
- Test: `tests/test_priority_list.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_priority_list.py`:

```python
import datetime
import pytest
from priority_list import _parse_hhmm, _in_window


def test_parse_hhmm_valid():
    assert _parse_hhmm("08:00") == datetime.time(8, 0)
    assert _parse_hhmm("23:59") == datetime.time(23, 59)
    assert _parse_hhmm("00:00") == datetime.time(0, 0)


@pytest.mark.parametrize("bad", ["25:00", "08:60", "8:00", "0800", "8am", "", "12:5"])
def test_parse_hhmm_invalid_returns_none(bad):
    assert _parse_hhmm(bad) is None


def test_in_window_same_day():
    s, e = datetime.time(8, 0), datetime.time(20, 0)
    assert _in_window(datetime.time(12, 0), s, e) is True
    assert _in_window(datetime.time(8, 0), s, e) is True    # start inclusive
    assert _in_window(datetime.time(20, 0), s, e) is False   # end exclusive
    assert _in_window(datetime.time(7, 59), s, e) is False


def test_in_window_wraps_midnight():
    s, e = datetime.time(20, 0), datetime.time(8, 0)
    assert _in_window(datetime.time(23, 0), s, e) is True
    assert _in_window(datetime.time(3, 0), s, e) is True
    assert _in_window(datetime.time(8, 0), s, e) is False    # end exclusive
    assert _in_window(datetime.time(12, 0), s, e) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_priority_list.py -k "hhmm or in_window" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `priority_list.py`, update imports:

```python
import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union
```

Add:

```python
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _parse_hhmm(text: str) -> Optional[datetime.time]:
    """Parse strict 'HH:MM' 24-hour time. Returns None on anything malformed."""
    m = _HHMM_RE.match(text.strip())
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _in_window(t: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    """True if `t` is in [start, end): start inclusive, end exclusive.
    If start > end the window wraps past midnight."""
    if start <= end:
        return start <= t < end
    return t >= start or t < end
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_priority_list.py -k "hhmm or in_window" -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add priority_list.py tests/test_priority_list.py
git commit -m "feat(priority): HH:MM parsing + time-window membership helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 12: `TimeRule` / `TimeBlock` dataclasses

**Files:**
- Modify: `priority_list.py` — new dataclasses + `_apply_order`
- Test: `tests/test_priority_list.py`

- [ ] **Step 1: Write the failing test**

```python
from priority_list import StreamerEntry, TimeRule, TimeBlock


def _members(*names):
    return [StreamerEntry(username=n) for n in names]


def test_timeblock_resolve_uses_matching_window():
    block = TimeBlock(
        members=_members("a", "b"),
        rules=[TimeRule(datetime.time(20, 0), datetime.time(8, 0), ["b", "a"])],
    )
    assert [e.username for e in block.resolve(datetime.time(22, 0))] == ["b", "a"]
    assert [e.username for e in block.resolve(datetime.time(12, 0))] == ["a", "b"]  # default order


def test_timeblock_resolve_appends_omitted_members_in_default_order():
    block = TimeBlock(
        members=_members("a", "b", "c"),
        rules=[TimeRule(datetime.time(20, 0), datetime.time(8, 0), ["c"])],
    )
    assert [e.username for e in block.resolve(datetime.time(22, 0))] == ["c", "a", "b"]


def test_timeblock_with_warning_always_returns_default_order():
    block = TimeBlock(
        members=_members("a", "b"),
        rules=[TimeRule(datetime.time(20, 0), datetime.time(8, 0), ["b", "a"])],
        warning="overlapping windows",
    )
    assert [e.username for e in block.resolve(datetime.time(22, 0))] == ["a", "b"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_priority_list.py -k timeblock -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `priority_list.py`, after the `StreamerEntry` dataclass:

```python
@dataclass
class TimeRule:
    start: datetime.time
    end: datetime.time
    order: List[str]  # usernames, priority order while this window is active


@dataclass
class TimeBlock:
    members: List[StreamerEntry]          # default (file) order
    rules: List[TimeRule] = field(default_factory=list)
    warning: Optional[str] = None         # set => rules are invalid, treat as a plain group

    def resolve(self, now: datetime.time) -> List[StreamerEntry]:
        if self.warning is None:
            for rule in self.rules:
                if _in_window(now, rule.start, rule.end):
                    return _apply_order(self.members, rule.order)
        return list(self.members)


def _apply_order(members: List[StreamerEntry], order: List[str]) -> List[StreamerEntry]:
    """Members named in `order` come first in that order; any not named are
    appended in their original (default) order."""
    by_name = {m.username: m for m in members}
    ordered = [by_name[name] for name in order if name in by_name]
    named = set(order)
    ordered += [m for m in members if m.username not in named]
    return ordered
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_priority_list.py -k timeblock -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add priority_list.py tests/test_priority_list.py
git commit -m "feat(priority): TimeRule/TimeBlock model with time resolution

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 13: Parse `@block` … `@end` (happy path)

**Files:**
- Modify: `priority_list.py` — `parse_streamers_file` return type + block parsing
- Test: `tests/test_priority_list.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_valid_block(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text(
        "northernlion|NL\n"
        "@block\n"
        "jerma985|Jerma\n"
        "cosmonaut_variety_hour|Cosmo\n"
        "@when 20:00-08:00: cosmonaut_variety_hour, jerma985\n"
        "@end\n"
        "shaun_vids\n"
    )
    entries = parse_streamers_file(f)
    assert len(entries) == 3
    assert entries[0].username == "northernlion"
    block = entries[1]
    assert isinstance(block, TimeBlock)
    assert block.warning is None
    assert [m.username for m in block.members] == ["jerma985", "cosmonaut_variety_hour"]
    assert block.members[0].nickname == "Jerma"
    assert len(block.rules) == 1
    assert block.rules[0].start == datetime.time(20, 0)
    assert block.rules[0].end == datetime.time(8, 0)
    assert block.rules[0].order == ["cosmonaut_variety_hour", "jerma985"]
    assert entries[2].username == "shaun_vids"


def test_parse_no_block_unchanged(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a|A\n---\nb\n")
    entries = parse_streamers_file(f)
    assert [type(e).__name__ for e in entries] == ["StreamerEntry", "StreamerEntry", "StreamerEntry"]
    assert usernames(entries) == ["a", "b"]


def test_parse_block_allows_comments_and_blanks_inside(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text(
        "@block\n"
        "# the pair\n"
        "a\n"
        "\n"
        "b\n"
        "@when 08:00-20:00: a, b\n"
        "@end\n"
    )
    block = parse_streamers_file(f)[0]
    assert [m.username for m in block.members] == ["a", "b"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_priority_list.py::test_parse_valid_block -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Rewrite `parse_streamers_file` in `priority_list.py`. Keep the existing single-line parsing for `StreamerEntry` factored into a helper, and add a block sub-parser:

```python
def _parse_streamer_line(line: str) -> StreamerEntry:
    if "|" in line:
        username, nickname = line.split("|", 1)
        username = username.strip().lower()
        nickname = nickname.strip() or None
    else:
        username = line.lower()
        nickname = None
    return StreamerEntry(username=username, nickname=nickname)


_WHEN_RE = re.compile(r"^@when\s+(\S+)\s*-\s*(\S+)\s*:\s*(.+)$", re.IGNORECASE)


def parse_streamers_file(path: Path) -> List[Union[StreamerEntry, "TimeBlock"]]:
    entries: List[Union[StreamerEntry, TimeBlock]] = []
    with open(path, "r") as f:
        lines = [ln.rstrip("\n") for ln in f]

    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if not raw or raw.startswith("#"):
            i += 1
            continue
        if raw == "---":
            entries.append(StreamerEntry(username="", is_separator=True))
            i += 1
            continue
        if raw.lower() == "@block":
            block, i = _parse_block(lines, i + 1)
            entries.append(block)
            continue
        entries.append(_parse_streamer_line(raw))
        i += 1
    return entries


def _parse_block(lines: List[str], i: int) -> tuple:
    """Parse from the line after `@block` up to and including `@end`.
    Returns (TimeBlock, index_after_end). Always returns a TimeBlock, with
    .warning set if anything is malformed (validation is Task 14)."""
    members: List[StreamerEntry] = []
    raw_rules: List[tuple] = []  # (start_str, end_str, [names]) — validated in Task 14
    structural_warning: Optional[str] = None
    n = len(lines)
    while i < n:
        raw = lines[i].strip()
        i += 1
        if not raw or raw.startswith("#"):
            continue
        low = raw.lower()
        if low == "@end":
            break
        if low == "@block":
            structural_warning = "nested @block is not allowed"
            continue
        if raw == "---":
            structural_warning = "--- separators are not allowed inside a block"
            continue
        if low.startswith("@when"):
            m = _WHEN_RE.match(raw)
            if not m:
                structural_warning = f"malformed @when line: {raw!r}"
                continue
            names = [x.strip().lower() for x in m.group(3).split(",") if x.strip()]
            raw_rules.append((m.group(1), m.group(2), names))
            continue
        members.append(_parse_streamer_line(raw))
    else:
        structural_warning = structural_warning or "@block without a matching @end"

    block = _build_block(members, raw_rules)
    if structural_warning and block.warning is None:
        block.warning = structural_warning
    return block, i
```

Add a stub `_build_block` that (for now) just builds rules without validation — Task 14 fills in validation:

```python
def _build_block(members, raw_rules) -> "TimeBlock":
    rules = []
    for start_str, end_str, names in raw_rules:
        s, e = _parse_hhmm(start_str), _parse_hhmm(end_str)
        rules.append(TimeRule(s, e, names))  # may contain None times until Task 14
    return TimeBlock(members=members, rules=rules)
```

Move the `TimeRule` / `TimeBlock` / `_apply_order` definitions above `parse_streamers_file` if needed for name resolution (or keep the quoted forward refs shown above).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_priority_list.py -q`
Expected: all pass (existing separator/nickname tests still green)

- [ ] **Step 5: Commit**

```bash
git add priority_list.py tests/test_priority_list.py
git commit -m "feat(priority): parse @block / @when / @end syntax

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 14: Block validation → `.warning`

**Files:**
- Modify: `priority_list.py` — `_build_block`
- Test: `tests/test_priority_list.py`

- [ ] **Step 1: Write the failing test**

```python
def _block(text):
    return _parse_block(("@block\n" + text + "@end\n").split("\n"), 1)[0]


def test_block_invalid_time_warns_and_keeps_members():
    b = _block("a\nb\n@when 25:00-08:00: a, b\n")
    assert b.warning is not None and "25:00" in b.warning
    assert [m.username for m in b.members] == ["a", "b"]
    assert [e.username for e in b.resolve(datetime.time(2, 0))] == ["a", "b"]


def test_block_zero_length_window_warns():
    b = _block("a\nb\n@when 08:00-08:00: a, b\n")
    assert b.warning is not None


def test_block_overlapping_windows_warn():
    b = _block("a\nb\n@when 08:00-21:00: a, b\n@when 20:00-08:00: b, a\n")
    assert b.warning is not None and "overlap" in b.warning.lower()


def test_block_touching_windows_are_ok():
    b = _block("a\nb\n@when 08:00-20:00: a, b\n@when 20:00-08:00: b, a\n")
    assert b.warning is None


def test_block_unknown_member_name_warns():
    b = _block("a\nb\n@when 08:00-20:00: a, zzz\n")
    assert b.warning is not None and "zzz" in b.warning


def test_block_duplicate_name_in_when_warns():
    b = _block("a\nb\n@when 08:00-20:00: a, a\n")
    assert b.warning is not None


def test_block_no_when_rules_warns():
    b = _block("a\nb\n")
    assert b.warning is not None and "no @when" in b.warning.lower()


def test_block_no_members_warns():
    b = _block("@when 08:00-20:00: a\n")
    assert b.warning is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_priority_list.py -k "block_invalid_time or overlapping or unknown_member" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Replace `_build_block`:

```python
def _build_block(members: List[StreamerEntry], raw_rules: list) -> "TimeBlock":
    member_names = {m.username for m in members}
    warning = None
    parsed_rules: List[TimeRule] = []

    def warn(msg):
        nonlocal warning
        warning = warning or msg

    if not members:
        warn("block has no member streamers")
    if not raw_rules:
        warn("block has no @when rules")

    coverage = [0] * (24 * 60)  # minute-of-day double-booking detector
    for start_str, end_str, names in raw_rules:
        s, e = _parse_hhmm(start_str), _parse_hhmm(end_str)
        if s is None:
            warn(f"invalid time {start_str!r} (expected HH:MM, 00:00-23:59)")
            continue
        if e is None:
            warn(f"invalid time {end_str!r} (expected HH:MM, 00:00-23:59)")
            continue
        if s == e:
            warn(f"window {start_str}-{end_str} is zero-length; omit @when for all-day")
            continue
        if len(set(names)) != len(names):
            warn(f"@when {start_str}-{end_str} lists a streamer twice")
        unknown = [x for x in names if x not in member_names]
        if unknown:
            warn(f"@when {start_str}-{end_str} names non-member streamer(s): {', '.join(unknown)}")
        # mark coverage (wrap-aware)
        s_min, e_min = s.hour * 60 + s.minute, e.hour * 60 + e.minute
        minutes = range(s_min, e_min) if s_min < e_min else \
            [m % 1440 for m in range(s_min, e_min + 1440)]
        for m in minutes:
            coverage[m] += 1
        parsed_rules.append(TimeRule(s, e, names))

    if any(c > 1 for c in coverage):
        warn("@when windows overlap")

    block = TimeBlock(members=members, rules=parsed_rules, warning=warning)
    if warning:
        # Prefix with the member list so the UI/log message identifies which block.
        who = ", ".join(m.username for m in members) or "(no members)"
        block.warning = f"block ({who}): {warning}"
    return block
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_priority_list.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add priority_list.py tests/test_priority_list.py
git commit -m "feat(priority): validate @block syntax, warn + fall back on error

Invalid times, zero-length or overlapping windows, unknown/duplicate
member names, missing @end, nested blocks and --- inside a block all set
block.warning; resolve() then just returns the default member order.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 15: `resolve_entries()` flattener

**Files:**
- Modify: `priority_list.py` — new `resolve_entries`; keep `usernames` working
- Test: `tests/test_priority_list.py`

- [ ] **Step 1: Write the failing test**

```python
from priority_list import resolve_entries


def test_resolve_entries_flattens_block_by_time(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text(
        "top\n"
        "@block\n"
        "a\n"
        "b\n"
        "@when 20:00-08:00: b, a\n"
        "@end\n"
        "bottom\n"
    )
    entries = parse_streamers_file(f)
    day = resolve_entries(entries, datetime.time(12, 0))
    assert [e.username for e in day] == ["top", "a", "b", "bottom"]
    night = resolve_entries(entries, datetime.time(23, 0))
    assert [e.username for e in night] == ["top", "b", "a", "bottom"]


def test_resolve_entries_no_block_is_identity(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a\n---\nb\n")
    entries = parse_streamers_file(f)
    out = resolve_entries(entries, datetime.time(12, 0))
    assert usernames(out) == ["a", "b"]


def test_usernames_still_accepts_flat_list(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a\nb\n")
    assert usernames(parse_streamers_file(f)) == ["a", "b"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_priority_list.py -k resolve_entries -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

```python
def resolve_entries(entries, now: Optional[datetime.time] = None) -> List[StreamerEntry]:
    """Flatten a parsed priority list to a plain ordered list of StreamerEntry,
    expanding each TimeBlock into its effective order for `now`
    (default: the current wall-clock time). Separators are preserved."""
    if now is None:
        now = datetime.datetime.now().time()
    out: List[StreamerEntry] = []
    for item in entries:
        if isinstance(item, TimeBlock):
            out.extend(item.resolve(now))
        else:
            out.append(item)
    return out
```

Confirm `usernames` still reads (unchanged):

```python
def usernames(entries: List[StreamerEntry]) -> List[str]:
    return [e.username for e in entries if not e.is_separator]
```

Note: callers pass `usernames(resolve_entries(entries, now))` when they need the time-resolved order. `usernames` itself must only ever receive a flat list — a `TimeBlock` has no `.is_separator`, so passing an unresolved list would raise; that's acceptable (it's a programming error) but Task 16 / Task 18 always resolve first.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_priority_list.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add priority_list.py tests/test_priority_list.py
git commit -m "feat(priority): resolve_entries() flattens time blocks for a given time

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 16: Daemon uses the time-resolved list + boundary switching

**Files:**
- Modify: `marquee_daemon.py` — imports (line ~18), `__init__` (line ~55-72), `load_priority_list` (line ~74-88), `maybe_reload_priority_list` (line ~90-102), `run()` (line ~415-513)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
import datetime as _dt
from priority_list import StreamerEntry, TimeRule, TimeBlock


def test_resolve_priority_list_reorders_on_time(monkeypatch):
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_entries = [
        StreamerEntry(username="top"),
        TimeBlock(members=[StreamerEntry(username="a"), StreamerEntry(username="b")],
                  rules=[TimeRule(_dt.time(20, 0), _dt.time(8, 0), ["b", "a"])]),
    ]
    ctrl._prev_resolved_order = None
    ctrl._reorder_event = False

    ctrl._resolve_priority_list(_dt.time(12, 0))
    assert ctrl.priority_list == ["top", "a", "b"]
    assert ctrl._reorder_event is False  # first resolution is not a "change"

    ctrl._resolve_priority_list(_dt.time(23, 0))
    assert ctrl.priority_list == ["top", "b", "a"]
    assert ctrl._reorder_event is True   # boundary crossed

    ctrl._resolve_priority_list(_dt.time(23, 30))
    assert ctrl._reorder_event is False  # steady state
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py::test_resolve_priority_list_reorders_on_time -v`
Expected: FAIL — `AttributeError: ... '_resolve_priority_list'`

- [ ] **Step 3: Implement**

Imports (line ~18):

```python
from priority_list import parse_streamers_file, resolve_entries, usernames as pl_usernames
```

`__init__` — replace the priority-list bootstrap (lines ~70-72):

```python
self.priority_entries = []            # List[StreamerEntry | TimeBlock]
self._prev_resolved_order = None      # for boundary-crossing detection
self._reorder_event = False
self.load_priority_list()
self._resolve_priority_list()
self._streamers_mtime = STREAMERS_FILE.stat().st_mtime
```

`load_priority_list` — parse into `self.priority_entries` and log warnings; don't set `self.priority_list` directly:

```python
def load_priority_list(self):
    if not STREAMERS_FILE.exists():
        print(f"ERROR: {STREAMERS_FILE} not found!")
        sys.exit(1)
    self.priority_entries = parse_streamers_file(STREAMERS_FILE)
    for item in self.priority_entries:
        warning = getattr(item, "warning", None)
        if warning:
            print(f"[streamers.txt] WARNING: {warning}")
    if not pl_usernames(resolve_entries(self.priority_entries)):
        print("ERROR: streamers.txt has no streamers!")
        sys.exit(1)
    print(f"Loaded priority list from {STREAMERS_FILE.name}")
```

Add:

```python
def _resolve_priority_list(self, now=None):
    """Recompute self.priority_list from the parsed entries for `now`
    (default: current time). Sets self._reorder_event when the resolved
    order changed since the last call (a time boundary was crossed)."""
    if now is None:
        now = datetime.now().time()
    resolved = pl_usernames(resolve_entries(self.priority_entries, now))
    self._reorder_event = (
        self._prev_resolved_order is not None and resolved != self._prev_resolved_order
    )
    self._prev_resolved_order = resolved
    self.priority_list = resolved
```

`maybe_reload_priority_list` — reparse into `priority_entries`:

```python
def maybe_reload_priority_list(self):
    try:
        mtime = STREAMERS_FILE.stat().st_mtime
    except FileNotFoundError:
        return
    if mtime != self._streamers_mtime:
        new_entries = parse_streamers_file(STREAMERS_FILE)
        if pl_usernames(resolve_entries(new_entries)):
            self.priority_entries = new_entries
            self._streamers_mtime = mtime
            for item in new_entries:
                warning = getattr(item, "warning", None)
                if warning:
                    print(f"[streamers.txt] WARNING: {warning}")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Reloaded priority list")
```

`run()` — call `_resolve_priority_list()` at the top of the loop, right after `self.maybe_reload_priority_list()`:

```python
self.maybe_reload_priority_list()
self._resolve_priority_list()
```

`run()` — in the auto-switch section, change the newly-live gate (line ~492) to also fire on a boundary crossing:

```python
if highest_priority in newly_live or self._reorder_event:
    if self.switching_soon != highest_priority:
        self.switching_soon = highest_priority
        self.grace_period_start = datetime.now()
        reason = "reorder" if self._reorder_event and highest_priority not in newly_live else "live"
        self.show_notification(highest_priority, self.live_streams[highest_priority], reason=reason)
```

(`show_notification` gets the `reason` param in Task 17 — write this call now; Task 17's test covers the signature. If running tests between tasks, land Task 17 first or add `reason="live"` default immediately.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -q`
Expected: all pass (do Task 17 in the same batch if `show_notification` signature breaks other tests)

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): resolve priority list by time; switch on boundary crossing

Crossing a @when window boundary is treated like a higher-priority stream
coming online: normal 5-minute grace period + notification. Steady state
never yanks an in-progress stream. manual_override still suppresses it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 17: `show_notification` reason parameter

**Files:**
- Modify: `marquee_daemon.py` — `show_notification` (line ~334-350)
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test**

```python
def test_show_notification_reason_reorder_message(monkeypatch):
    sent = {}
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: sent.update(msg=cmd[-1]) or mock.Mock())
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.show_notification("beta", {"title": "t", "game": "g"}, reason="reorder")
    assert "priority" in sent["msg"].lower()
    assert "beta" in sent["msg"]


def test_show_notification_default_reason_is_live(monkeypatch):
    sent = {}
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: sent.update(msg=cmd[-1]) or mock.Mock())
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.show_notification("beta", {"title": "t", "game": "g"})
    assert "live" in sent["msg"].lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -k show_notification -v`
Expected: FAIL — `TypeError: show_notification() got an unexpected keyword argument 'reason'`

- [ ] **Step 3: Implement**

```python
def show_notification(self, new_streamer: str, new_stream_info: Dict, reason: str = "live"):
    """Desktop notification about an upcoming switch. `reason` is "live"
    (the stream just came online) or "reorder" (a time rule just promoted
    it above what's playing)."""
    if reason == "reorder":
        message = f"priority shifted — {new_streamer} now takes precedence, switching in 5 minutes"
    else:
        message = f"{new_streamer} went live! switching in 5 minutes"

    subprocess.run(
        ["notify-send", "-u", "normal", "-t", "0", "Marquee.tv", message],
        capture_output=True,
    )
    print(f"\n{'!'*60}")
    print(f"UPCOMING SWITCH: {new_streamer} ({reason})")
    print(f"Title: {new_stream_info['title']}")
    print(f"Game:  {new_stream_info['game']}")
    print(f"Will auto-switch in 5 minutes (or close mpv to switch now)")
    print(f"{'!'*60}\n")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat(daemon): reason-specific switch notification text

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 18: UI renders time blocks + warnings

**Files:**
- Modify: `marquee_render.py` — `RowData` (line ~31-41)
- Modify: `marquee_ui.py` — `__init__`, `load_entries` (line ~263-266), new `_reresolve_entries`, `tick` (line ~394-396), `_row_data` (line ~442-460), `render_frame` separator branch (line ~607-616), colour constants (line ~48-52)
- Test: `tests/test_marquee_render.py`, `tests/test_marquee_ui.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_marquee_render.py`:

```python
from marquee_render import RowData

def test_rowdata_carries_separator_label_and_warning():
    r = RowData(name="", is_live=False, is_separator=True,
                separator_label="⏱ 20:00-08:00", separator_warning=False)
    assert r.separator_label == "⏱ 20:00-08:00"
    assert r.separator_warning is False
```

`tests/test_marquee_ui.py`:

```python
@pytest.mark.asyncio
async def test_ui_renders_block_in_resolved_order_with_window_label(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text(
        "top|Top\n@block\naaa|Aaa\nbbb|Bbb\n@when 00:00-23:59: bbb, aaa\n@end\n"
    )
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: None)
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)
    app = MarqueeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        content = app.query_one("#frame").content
        assert content.index("Bbb") < content.index("Aaa")  # resolved order
        assert "00:00" in content  # window label somewhere


@pytest.mark.asyncio
async def test_ui_renders_block_warning_line(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text(
        "@block\naaa\nbbb\n@when 08:00-21:00: aaa, bbb\n@when 20:00-08:00: bbb, aaa\n@end\n"
    )
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: None)
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)
    app = MarqueeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        content = app.query_one("#frame").content
        assert "⚠" in content and "overlap" in content.lower()
        # members still listed (not dropped)
        assert "aaa" in content and "bbb" in content


@pytest.mark.asyncio
async def test_ui_navigation_skips_block_rule_lines(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text(
        "top\n@block\naaa\nbbb\n@when 00:00-23:59: aaa, bbb\n@end\nbottom\n"
    )
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: None)
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)
    app = MarqueeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        seen = set()
        for _ in range(8):
            seen.add(app.entries[app.nav.index].username)
            await pilot.press("down")
            await pilot.pause()
        assert "" not in seen  # never lands on a synthetic rule line
        assert {"top", "aaa", "bbb", "bottom"} <= seen
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_marquee_render.py::test_rowdata_carries_separator_label_and_warning tests/test_marquee_ui.py -k "block_in_resolved or block_warning or skips_block_rule" -v`
Expected: all FAIL

- [ ] **Step 3: Implement**

`marquee_render.py` — `RowData` gains two fields:

```python
@dataclass
class RowData:
    name: str
    is_live: bool
    viewers: Optional[int] = None
    game: str = ""
    title: str = ""
    started_at: Optional[str] = None
    last_seen: Optional[dict] = None
    username: str = ""
    is_separator: bool = False
    separator_label: Optional[str] = None   # label drawn into a block's top rule line
    separator_warning: bool = False         # render the label as a warning (yellow)
```

`marquee_ui.py` — colour constant (near line ~52):

```python
WARNING_STYLE = "#f9e2af"  # Catppuccin Mocha Yellow — malformed @block warnings
```

`__init__` — add:

```python
self.priority_entries = []
```

`load_entries` — parse into `priority_entries`, then resolve to display rows:

```python
def load_entries(self) -> None:
    from priority_list import parse_streamers_file as _parse
    self.priority_entries = _parse(STREAMERS_FILE)
    self._reresolve_entries()
```

Add `_reresolve_entries` — flatten `priority_entries` into `self.entries`, injecting synthetic separator entries around each block:

```python
def _reresolve_entries(self) -> None:
    """Rebuild self.entries (a flat StreamerEntry list) from the parsed
    priority list, resolved against the current time. Each TimeBlock is
    bracketed by synthetic separator entries; the top one carries a label
    (the active @when window, or the warning text)."""
    import datetime
    from priority_list import StreamerEntry, TimeBlock
    now = datetime.datetime.now().time()
    flat: List[StreamerEntry] = []
    for item in self.priority_entries:
        if isinstance(item, TimeBlock):
            if item.warning:
                label, warn = f"⚠ {item.warning}", True
            else:
                active = next((r for r in item.rules
                               if _in_window_ui(now, r.start, r.end)), None)
                label = (f"⏱ {active.start:%H:%M}-{active.end:%H:%M}" if active
                         else "⏱ default order")
                warn = False
            flat.append(StreamerEntry(username="", is_separator=True))
            flat[-1].block_label = label
            flat[-1].block_warning = warn
            flat.extend(item.resolve(now))
            tail = StreamerEntry(username="", is_separator=True)
            tail.block_label = None
            tail.block_warning = False
            flat.append(tail)
        else:
            if not hasattr(item, "block_label"):
                item.block_label = None
                item.block_warning = False
            flat.append(item)
    self.entries = flat
    separator_indices = {i for i, e in enumerate(self.entries) if e.is_separator}
    self.nav.set_count(len(self.entries), skip_indices=separator_indices)
```

Add a tiny local time helper import at module level (reuse `priority_list._in_window`):

```python
from priority_list import parse_streamers_file, StreamerEntry, _in_window as _in_window_ui
```

Add `block_label` / `block_warning` fields to `StreamerEntry` in `priority_list.py` so the attributes are declared rather than monkey-patched:

```python
@dataclass
class StreamerEntry:
    username: str
    nickname: Optional[str] = None
    is_separator: bool = False
    block_label: Optional[str] = None    # UI-only: label for a block's top rule line
    block_warning: bool = False          # UI-only: render block_label as a warning
```

(Then drop the `flat[-1].block_label = ...` monkey-patch style in `_reresolve_entries` and pass them to the constructor instead.)

`tick` — re-resolve each tick so a boundary crossing updates the display:

```python
def tick(self) -> None:
    self.refresh_data()
    self._reresolve_entries()
    self.render_frame()
```

`_row_data` — carry the label through for separator entries:

```python
def _row_data(self) -> List[RowData]:
    rows = []
    for entry in self.entries:
        if entry.is_separator:
            rows.append(RowData(
                name="", is_live=False, is_separator=True,
                separator_label=getattr(entry, "block_label", None),
                separator_warning=getattr(entry, "block_warning", False),
            ))
            continue
        info = self.live_streams.get(entry.username)
        is_live = info is not None
        rows.append(RowData(
            name=entry.display_name,
            is_live=is_live,
            viewers=info.get('viewers') if info else None,
            game=info.get('game', '') if info else '',
            title=info.get('title', '') if info else '',
            started_at=info.get('started_at') if info else None,
            last_seen=self.last_seen.get(entry.username),
            username=entry.username,
        ))
    return rows
```

`render_frame` — in the `if row.is_separator:` branch (line ~609), draw the label into the rule when present:

```python
if row.is_separator:
    if row.separator_label:
        lbl = f" {row.separator_label} "
        # clamp to available width
        max_lbl = list_inner - 2
        if cell_len(lbl) > max_lbl:
            lbl = set_cell_size(lbl, max_lbl)
        dashes = "─" * (list_inner + 2 - cell_len(lbl))
        style = WARNING_STYLE if row.separator_warning else B
        lines.append(self._styled_line(
            ("║" + " " * MARGIN + "├", B), (lbl, style), (dashes + "┤" + " " * MARGIN + "║", B),
        ))
    else:
        lines.append(Text(
            "║" + " " * MARGIN + "├" + "─" * (list_inner + 2) + "┤" + " " * MARGIN + "║",
            style=B,
        ))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_marquee_render.py tests/test_marquee_ui.py -q`
Expected: all pass. Fix any existing UI tests that counted separator/entry indices and now see the extra synthetic rows.

- [ ] **Step 5: Commit**

```bash
git add marquee_ui.py marquee_render.py priority_list.py tests/test_marquee_render.py tests/test_marquee_ui.py
git commit -m "feat(ui): render @block members in time-resolved order with labels

Blocks show between labelled rule lines (active window, or a yellow
warning line for a malformed block — members still listed). Navigation
skips the rule lines. Re-resolved each tick so a boundary crossing
updates the display live.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 19: Docs + example — §3 `@block` syntax

**Files:**
- Modify: `README.md`, `priority_list.py` (module docstring is absent — skip), `streamers.txt` **do not touch** (uncommitted user edit). Instead add a commented example to the README only.

- [ ] **Step 1: Update README**

Add a subsection under the priority-list docs, "Time-based priority (`@block`)", with:

```
@block
jerma985|Jerma
cosmonaut_variety_hour|Cosmonaut
@when 20:00-08:00: cosmonaut_variety_hour, jerma985
@end
```

Explain: members in default order between `@block`/`@end`; `@when HH:MM-HH:MM: name, name` overrides the order during that window (24-hour clock, start inclusive / end exclusive, may wrap midnight); windows may not overlap; omitted members keep their default order after the listed ones; a malformed block shows a ⚠ warning in the UI and falls back to default order (no streamer is dropped); crossing a boundary while watching triggers the normal 5-minute grace-period switch.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document @block time-based priority syntax

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 20: Full suite + manual integration pass

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all green. Fix any regressions before proceeding.

- [ ] **Step 2: Manual integration checklist** (document results in the commit body if anything is adjusted)

- Launch UI with no daemon running → daemon starts, priority list populates, header shows "Playback stopped".
- Press `(S)` → highest-priority live stream launches (mpv + Chatterino once).
- End that stream from Twitch's side → **no** 3× Chatterino flap; daemon logs `… ended … cooling down`; drops to next priority.
- Press `(X)` → mpv + Chatterino close; header returns to "Playback stopped"; priority list still updating.
- Quit UI → two options: "leave daemon running" / "stop the daemon".
- Add an `@block` with an overnight `@when` to `streamers.txt`; `e`-edit and save → block renders in resolved order with the `⏱` label; break it (overlap) → yellow `⚠` line, members still shown.

- [ ] **Step 3: Final commit (if any fixups)**

```bash
git add -A ':!streamers.txt'
git commit -m "test: fixups from full-suite + integration pass

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** §1 → Tasks 1-4. §2 → Tasks 5-10. §3 → Tasks 11-19. Verification → Task 20. All spec sections mapped.
- **`streamers.txt` guard:** every commit step stages files explicitly by path; Task 20's `git add -A` excludes it via pathspec.
- **`show_notification` signature:** introduced with a `reason="live"` default in Task 17; Task 16 calls it with `reason=` — land Tasks 16+17 in one batch, or add the defaulted param first.
- **`_in_window` reuse:** the UI imports `priority_list._in_window` (underscore-private but same package) rather than duplicating wrap-around logic.
- **Type consistency:** `parse_streamers_file` → `List[StreamerEntry | TimeBlock]`; `resolve_entries` → `List[StreamerEntry]`; `usernames` unchanged and always fed a resolved list; daemon `self.priority_list` stays `List[str]`, recomputed by `_resolve_priority_list`.
- **Existing tests will break** in `test_marquee_ui.py` (S/X behaviour, quit wording) and possibly index-sensitive render tests — Tasks 9 and 18 call this out explicitly and require updating them to the new contract.
