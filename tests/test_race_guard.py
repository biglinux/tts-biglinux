"""Alt+V race-guard semantics: generation counter guards stale audio.

Model:
  - stop() increments the generation (invalidates in-flight synths).
  - speak() captures self._dispatch_gen = self._generation.
  - a background synth plays only if its captured gen is still current.
"""
import importlib
ts = importlib.import_module("services.tts_service")


def _svc():
    s = ts.TTSService.__new__(ts.TTSService)
    s._generation = 0
    s._dispatch_gen = 0
    return s


def test_interrupt_invalidates_previous():
    s = _svc()
    # First request dispatches at gen 0
    s._dispatch_gen = s._generation
    first = s._dispatch_gen
    assert s._is_current(first)

    # Interrupt: stop() bumps generation, then new speak captures it
    s._generation += 1              # stop()
    s._dispatch_gen = s._generation  # speak()
    second = s._dispatch_gen

    assert not s._is_current(first), "old request must be superseded"
    assert s._is_current(second)


def test_simultaneous_mode_coexists():
    s = _svc()
    # Two simultaneous dispatches WITHOUT a stop() between them
    s._dispatch_gen = s._generation
    a = s._dispatch_gen
    # second simultaneous speak (stop_previous=False → no bump)
    s._dispatch_gen = s._generation
    b = s._dispatch_gen
    assert s._is_current(a) and s._is_current(b), "simultaneous speeches coexist"

    # explicit stop() invalidates both
    s._generation += 1
    assert not s._is_current(a) and not s._is_current(b)
