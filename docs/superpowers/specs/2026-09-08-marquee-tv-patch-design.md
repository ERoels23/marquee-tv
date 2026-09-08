# Marquee.tv Patch — Design Spec

Date: 2026-09-08

## Summary

Three independent changes to Marquee.tv, implemented and reviewed one at a time:

1. **Stop relaunching a stream that just ended.** When a stream ends, the daemon
   currently relaunches the now-dead streamer ~3 times (flickering Chatterino)
   before its stale live-cache clears. Add end-of-stream detection so it drops
   straight to the next priority.
2. **Always-on daemon.** Launching the UI always starts the daemon so stream info
   is visible without playing anything. `(S)tart` / `(X)Stop` now toggle
   *playback* only, not the daemon's existence.
3. **Time-based priority rules.** A `@block` construct in `streamers.txt` lets a
   contiguous group of streamers be reordered by wall-clock time (e.g. Jerma over
   Cosmonaut 08:00–20:00, reversed overnight), with syntax validation and a UI
   warning on malformed blocks.

The daemon/UI split and file-based IPC (`.status.json`, `.control`,
`.last_seen.json`) are unchanged — all three changes extend the existing
architecture.

---

## 1. Stop relaunching a stream that just ended

### Problem

`marquee_daemon.py` polls the Twitch API every `API_UPDATE_INTERVAL` (60s) but
runs its event loop every `CHECK_INTERVAL` (10s). When a stream ends,
streamlink/mpv exits within seconds, but `self.live_streams` still lists that
streamer as live for up to a minute. So `get_highest_priority_live()` keeps
returning the dead streamer and `if not self.is_stream_alive()` relaunches it;
streamlink fast-fails (stream offline), mpv closes, repeat — ~3 times over ~30s
until the next API poll drops them. Each relaunch kills and respawns Chatterino,
hence the visible flicker.

### Approach: targeted recheck + timing/cooldown backstop

**New daemon state (`TwitchTVController.__init__`):**

- `self.cooldowns: Dict[str, float]` — `streamer -> unix expiry timestamp`. A
  streamer on cooldown is invisible to auto-selection.
- `self.current_stream_started_at: Optional[float]` — `time.monotonic()` stamp,
  set in `launch_stream()`.
- `self._handled_current_death: bool` — ensures a given mpv exit is reacted to
  once, not every 10s tick. Set `False` in `launch_stream()`.

**New constants:**

- `RELAUNCH_COOLDOWN = 300` — seconds a just-ended streamer is skipped for.
- `MIN_REAL_SESSION = 30` — seconds; a stream that ran shorter than this "never
  really started".

**New helper `_query_single_live(streamer) -> Optional[Dict]`:** one targeted
`twitch api get streams -q user_login=<streamer>` call, mirroring the UI's
`poll_single_stream_from_api`. Returns the stream-info dict or `None`. Same
"judge success by whether stdout parses, not the exit code" handling used
elsewhere in the daemon.

**Loop logic** — near the top of the `while self.running` body, after the API
refresh block, before the launch/switch block:

```
died = (self.current_stream is not None
        and self.current_process is not None
        and not self.is_stream_alive()
        and not self._handled_current_death)
if died:
    self._handled_current_death = True
    self.last_api_update = 0  # force a full refresh next tick
    ran_for = time.monotonic() - (self.current_stream_started_at or 0.0)
    still_live = self._query_single_live(self.current_stream) is not None
    if (not still_live) or ran_for < MIN_REAL_SESSION:
        # ended, or never started and the API is lagging behind the real end
        self.cooldowns[self.current_stream] = time.time() + RELAUNCH_COOLDOWN
        self.live_streams.pop(self.current_stream, None)
    # else: ran a real session and is still live -> deliberate hand-close /
    # skip. No cooldown; fall through to normal next-priority selection.
```

**`get_highest_priority_live()`** skips cooled-down streamers:

```
def get_highest_priority_live(self, live_streams):
    now = time.time()
    for streamer in self.priority_list:
        if streamer in live_streams and self.cooldowns.get(streamer, 0.0) < now:
            return streamer
    return None
```

Expired cooldown entries are pruned opportunistically in the same pass (or in
`save_status`) — housekeeping only, correctness doesn't depend on it.

**Manual override:** when an explicit `switch:<streamer>` control command is
honored (target present in `live_streams`), `self.cooldowns.pop(target, None)` —
a direct user request always wins.

**`switching_soon` / grace logic:** inherits the cooldown filter automatically
via `highest_priority`, so a just-ended streamer that the API still reports live
won't generate a spurious "switch in 5 minutes" notification.

### Behaviour after the change

- Common case (targeted recheck sees the stream is gone): **zero** Chatterino
  flicker — straight to next priority.
- API-lag case (recheck still says live but the session was too short to be
  real): **one** flicker at most, then cooldown suppresses further attempts.
- Deliberate hand-close after a real viewing session: unchanged — falls through
  to next-priority / pending grace target, no cooldown.

### Tests (`tests/test_marquee_daemon.py`)

- Dead process + `_query_single_live` returns `None` → streamer cooled down and
  removed from `live_streams`.
- Dead process + recheck says live + `ran_for < MIN_REAL_SESSION` → cooled down.
- Dead process + recheck says live + `ran_for >= MIN_REAL_SESSION` → **not**
  cooled down.
- `get_highest_priority_live` skips a cooled-down streamer and returns the next.
- Cooldown expiry (monkeypatch `time.time`) re-exposes the streamer.
- Explicit `switch:<streamer>` clears that streamer's cooldown.
- `_handled_current_death` prevents a second recheck on the next tick.

---

## 2. Always-on daemon; Start/Stop toggle playback only

### Problem

The UI doesn't start the daemon on launch — stream info only appears after
pressing `(S)`, which also immediately launches a stream. You can't just look at
the dashboard.

### Approach: one daemon, in-memory `playback_enabled` flag

Chosen over splitting into two processes — the launch/switch logic is too
intertwined with the poll loop to separate cheaply, and a flag gets the same
result.

### Daemon changes (`marquee_daemon.py`)

- **New state:** `self.playback_enabled: bool = False`. The daemon now boots in
  monitor-only mode.
- **`parse_control_command`** learns two tokens:
  - `play` → `(None, "play")`
  - `stop` → `(None, "stop")`
  (Return shape stays `(target, mode)`; `target` is `None` for these.)
- **Loop split** — the loop already nearly separates these halves:
  - **Always runs:** API poll, `live_streams` cache, `last_seen` updates,
    `save_status()`, mpv-title push for a playing stream, `maybe_reload_priority_list`.
  - **Gated on `self.playback_enabled`:** the entire
    `if not self.is_stream_alive(): ... elif self.is_stream_alive(): ...`
    launch / relaunch / grace-period / auto-switch block, **and** the §1
    end-of-stream detection. When the flag is off, `current_stream` stays `None`
    and nothing spawns.
- **Control handling:**
  - `play` → `self.playback_enabled = True`. Next tick launches the
    highest-priority live stream via existing logic.
  - `stop` → `self.playback_enabled = False`; terminate `self.current_process`,
    `pkill -x chatterino`, clear `current_stream` / `switching_soon` /
    `grace_period_start` / `manual_override`.
  - An explicit `switch:<streamer>` (manual launch / ad-hoc override/temporary)
    implies `play` — it sets `playback_enabled = True` before launching.
- **`save_status()`** gains `'playback_enabled': self.playback_enabled`.
- **Shutdown cleanup** (end of `run()`) also `pkill -x chatterino` — today it
  only terminates the stream process.

### `marquee.sh` changes

- `start` — unchanged mechanically, but the daemon it starts is now monitor-only.
- `stop` — unchanged: SIGTERM the daemon; its cleanup tears down stream +
  Chatterino.
- **New** `play` — `echo play > "$CONTROL_FILE"`.
- **New** `stop-playback` — `echo stop > "$CONTROL_FILE"`.
  (Thin wrappers for parity / debugging; the UI writes the control file directly,
  as it does today for `switch:`.)
- Help text updated.

### UI changes (`marquee_ui.py`)

- **`on_mount`:** before `refresh_data(force=True)`, ensure the daemon is running
  — if `not self.daemon_running()`, run `marquee.sh start` (blocking ~1s, same as
  today's `start_service`; also makes the subsequent `refresh_data` cheaper since
  it takes the status-file path instead of a direct API poll).
- **`action_start_service` (`S`):** write `play` to `.control`. Strict toggle —
  no re-pick, no-op if already playing. Keep the footer-key highlight feedback.
- **`action_stop_service` (`X`):** write `stop` to `.control`. Clear
  `self.current_stream` / `self.stream_alive` locally for immediate feedback.
  **No longer** blanket-`pkill`s mpv (that would also kill ad-hoc one-shots) —
  the daemon tears down its own stream.
- **`_load_status_file`:** read `playback_enabled` into `self.playback_enabled`.
- **`_header_data()`:** with the daemon essentially always up, the header states
  become:
  - `not self._daemon_was_running` → `HeaderData(active=False, inactive_message="Daemon Offline")` (genuine error state — daemon failed to start).
  - daemon up, `not self.playback_enabled` → `HeaderData(active=False, inactive_message="Playback stopped — press (S) to start")`.
  - daemon up, playback on, `not self.current_stream` → `HeaderData(active=False)` (waiting for a live stream).
  - otherwise → active, as today.
- **`QuitConfirmModal`:** still two options, wording nudged since "keep" now also
  means "keep monitoring":
  - `("keep", "Quit — leave daemon running")`
  - `("stop", "Quit and stop the daemon")`
- **`action_request_quit`:** unchanged logic; the `not daemon_running()`
  early-exit branch stays as a safety net but will rarely fire now.

### Notes / out of scope

- `playback_enabled` is **in-memory only**. If the daemon process itself
  restarts (crash + systemd `Restart=on-failure`, manual restart), it comes back
  monitor-only. This is the safe default; persisting it is out of scope.
- `marquee.service` users get the same behaviour change: enabling the unit
  monitors but does not auto-play until `play` is sent. README note only; no unit
  file change.

### Tests

- `tests/test_marquee_daemon.py`: `play` / `stop` control tokens flip
  `playback_enabled`; monitor-only loop still writes status + last_seen but never
  calls `launch_stream`; `stop` terminates the current process and clears state;
  `switch:<streamer>` implies `play`.
- `tests/test_marquee_ui.py`: `on_mount` starts the daemon when absent; `S`
  writes `play`; `X` writes `stop` and does not `pkill mpv`; header reflects
  `playback_enabled`; quit modal wording.

---

## 3. Time-based priority rules

### File syntax (`streamers.txt`)

```
northernlion|Northernlion

@block
jerma985|Jerma
cosmonaut_variety_hour|Cosmonaut
@when 20:00-08:00: cosmonaut_variety_hour, jerma985
@end

shaun_vids|Shaun
```

- `@block` … `@end` delimit a block. Member lines between them use the existing
  line syntax (`username` or `username|Nickname`), and their file order is the
  **default** priority order.
- `@when HH:MM-HH:MM: name, name, …` — during that wall-clock window, order the
  block's members this way instead. 24-hour clock. **Start inclusive, end
  exclusive.** Windows may wrap midnight (`20:00-08:00`).
- Multiple `@when` lines are allowed. With non-overlap enforced (below) at most
  one ever matches. No window matches the current time → default order.
- A member omitted from the matching `@when` is appended after the listed ones,
  in default order.
- `#` comments and blank lines are allowed inside a block.
- A file with no `@block` parses exactly as today. `---` separators are
  unchanged and may not appear inside a block.
- The block occupies the contiguous run of priority slots where its first member
  would have sat; the rest of the list is unaffected.

### Validation

A `@block` is **valid** only if all of:

1. Every `@when` time is `HH:MM` with `00 <= HH <= 23` and `00 <= MM <= 59`.
2. `start != end` for every window (no zero-length / implicit all-day windows).
3. **No two `@when` windows overlap.** Checked by marking a 1440-entry
   minute-of-day coverage array (each window, midnight-wrap aware); any minute
   covered twice → overlap. Touching endpoints (`…-20:00` and `20:00-…`) do not
   overlap because end is exclusive.
4. Every name in every `@when` is one of the block's members (case-insensitive);
   no duplicate name within a single `@when`.
5. Well-formed: `@end` present, not nested, no `---` inside, `>= 1` member,
   `>= 1` `@when` line.

**On any violation:** the block's timing is discarded and its members are used in
**plain default file order** — no streamer is ever dropped over a typo — and a
warning is emitted:

- **UI:** rendered inline where the block sits, Catppuccin Mocha yellow
  (`#f9e2af`), e.g.
  `⚠ block (jerma985, cosmonaut_variety_hour): overlapping windows 08:00-21:00 / 20:00-08:00`.
  Full detail also goes to the log/stderr.
- **Daemon:** logged to `.log`.

The UI and the daemon each parse `streamers.txt` independently (already true
today), so each validates on its own.

### Data model (`priority_list.py`)

```python
@dataclass
class TimeRule:
    start: datetime.time
    end: datetime.time
    order: List[str]           # usernames, priority order for this window

@dataclass
class TimeBlock:
    members: List[StreamerEntry]   # default order
    rules: List[TimeRule]
    warning: Optional[str] = None  # set => rules invalid, treat as plain group

    def resolve(self, now: datetime.time) -> List[StreamerEntry]:
        if self.warning is None:
            for rule in self.rules:
                if _in_window(now, rule.start, rule.end):
                    return _apply_order(self.members, rule.order)
        return list(self.members)
```

- `parse_streamers_file(path) -> List[Union[StreamerEntry, TimeBlock]]`.
- **New** `resolve_entries(entries, now=None) -> List[StreamerEntry]` — flattens
  any `TimeBlock` in effective order (`now` defaults to `datetime.now().time()`).
- `usernames(entries)` is unchanged — it already filters non-`StreamerEntry` /
  separator items; callers that need the time-resolved order call
  `usernames(resolve_entries(entries, now))`.
- `_in_window(t, start, end)` handles `start < end` (normal) and `start > end`
  (wraps midnight).
- Parser collects warnings; on a malformed block it still emits a `TimeBlock`
  with `.warning` set and `.members` intact (so downstream code is uniform).
  A structurally broken block (missing `@end` at EOF, `---` inside, nested
  `@block`) is closed at the offending point, its members kept, `.warning` set.

### Daemon changes (`marquee_daemon.py`)

- `load_priority_list()` / `maybe_reload_priority_list()` store the parsed
  `List[StreamerEntry | TimeBlock]` (call it `self.priority_entries`) **and** log
  any block warnings.
- Each loop tick:
  `resolved = usernames(resolve_entries(self.priority_entries, datetime.now().time()))`
  and use that as `self.priority_list` for the rest of the tick.
- **Reorder event:** keep `self._prev_resolved_order: List[str]`. If `resolved`
  differs from it (and it isn't the first tick) a boundary was crossed. On that
  tick only, if the new `highest_priority` is live and isn't `current_stream`
  and `not self.manual_override`, start the **normal grace period**
  (`switching_soon` / `grace_period_start`) and fire a notification —
  `show_notification` variant text: `priority shifted — <streamer> now takes
  precedence, switching in 5 minutes`.
- Steady state (no boundary crossed) never starts a grace period from a time
  rule — matching the existing "settled in" semantics of the `newly_live` gate,
  so you're not yanked around mid-session.

### UI changes (`marquee_ui.py`, `marquee_render.py`, `marquee_model.py`)

- `load_entries()` stores `self.priority_entries`; a resolved
  `self.entries: List[StreamerEntry]` (with a lightweight marker on block members)
  is recomputed on each `tick()` from the current time so a boundary crossing
  updates the display.
- Block members render in effective order, bracketed by faint rule lines reusing
  the existing `---` separator rendering. The top rule carries a label: the
  active window (`⏱ 20:00–08:00`) or, when no window matches, `⏱ default order`.
  A block with `.warning` renders the yellow warning line in place of the label
  and its members plain.
- `ListNavigator` skip-indices already exclude separators; the block rule lines
  are added to that set so navigation / `i` info skip them.
- `e`-edit: re-parse on return (already happens via `load_entries`); malformed
  blocks just surface their warning line. No blocking modal.

### Tests

- `tests/test_priority_list.py`: parse a valid block; `resolve_entries` picks the
  right order inside / outside a window; midnight-wrap window; omitted member
  appended; no-`@block` file unchanged; each validation rule (`25:00`, `08:60`,
  `start == end`, overlap, unknown member name, dup name, missing `@end`, `---`
  inside, nested) → `.warning` set and members preserved in default order.
- `tests/test_marquee_daemon.py`: monkeypatch the clock; crossing a boundary with
  both streams live and the lower one playing → grace period + notification;
  steady state → no grace period; `manual_override` suppresses the boundary
  switch.
- `tests/test_marquee_render.py` / `test_marquee_ui.py`: block renders in
  resolved order with the window label; warning block renders the yellow line;
  navigation skips rule lines.

---

## Implementation order

1. §1 (self-contained daemon change) → review → commit.
2. §2 (daemon flag + UI wiring) → review → commit.
3. §3 (parser + data model, then daemon, then UI) → review → commit.

Each lands as its own commit on a shared feature branch.
