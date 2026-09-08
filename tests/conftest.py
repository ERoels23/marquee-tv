import pytest

from marquee_ui import MarqueeApp


@pytest.fixture(autouse=True)
def _no_real_daemon_spawn(monkeypatch):
    """`MarqueeApp.on_mount` now auto-starts the daemon when one isn't already
    running, so every `app.run_test()` would otherwise shell out to
    `marquee.sh start` and spawn a real background daemon. Neutralise it by
    default; tests that specifically exercise the start path re-`setattr` it
    (a test-body monkeypatch applied after this fixture wins)."""
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: None, raising=False)
