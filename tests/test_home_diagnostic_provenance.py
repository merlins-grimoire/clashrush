"""Atomic native HOME rejection, using only synthetic transient frames."""
import json
from dataclasses import asdict

import pytest

from clash_rush_rebuild import mvp_local_runtime as runtime
from test_home_diagnostics import ACCOUNT, BINDING, frame, recognizer_for
from test_home_diagnostic_seal import assert_no_pixels


@pytest.mark.parametrize('count', [0, 960, 961])
def test_required_home_has_one_reduction_and_no_negative_publication(monkeypatch, count):
    calls = []
    original = runtime.BgraGameplayRecognizer._hsv
    def hsv(*channels):
        calls.append(None)
        # A second scan would disagree; it must never happen.
        return original(*channels) if len(calls) <= 3200 else (10, 255, 255)
    monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_hsv', staticmethod(hsv))
    recognizer, captures = recognizer_for(frame(count=count))
    if count > 960:
        assert runtime.BgraGameplayRecognizer.recognize(
            recognizer, BINDING, ACCOUNT, require_home=True,
        ).home is True
    else:
        with pytest.raises(runtime.RuntimeSafetyError) as caught:
            runtime.BgraGameplayRecognizer.recognize(
                recognizer, BINDING, ACCOUNT, require_home=True,
            )
        payload = json.loads(str(caught.value).split(' ', 1)[1])
        assert payload['orange_pixels'] == count
        assert payload['reason'] == ('FRACTION_LOW' if count else 'COLOR_ABSENT')
        assert recognizer.home_diagnostic is None
        assert_no_pixels(caught.value)
    assert len(calls) == 3200
    assert len(captures) == 1


@pytest.mark.parametrize('mode', ['mutate', 'replace', 'delete'])
def test_later_valid_scalar_changes_cannot_rewrite_emitted_snapshot(mode):
    recognizer, _ = recognizer_for(frame(count=960))
    recognizer.recognize(BINDING, ACCOUNT)
    old = recognizer.home_diagnostic
    with pytest.raises(runtime.RuntimeSafetyError) as caught:
        runtime.BgraGameplayRecognizer.recognize(
            recognizer, BINDING, ACCOUNT, require_home=True,
        )
    sealed = str(caught.value)
    values = asdict(old)
    fields = ('hue_pixels', 'saturation_pixels', 'value_pixels', 'orange_pixels')
    if mode == 'mutate':
        for name in fields:
            object.__setattr__(old, name, 959)
        recognizer._home_diagnostic = old
    elif mode == 'replace':
        values.update(dict.fromkeys(fields, 1))
        recognizer._home_diagnostic = runtime.HomeDiagnostic(**values)
    else:
        object.__delattr__(old, 'orange_pixels')
        recognizer._home_diagnostic = old
    assert str(caught.value) == sealed
    assert json.loads(sealed.split(' ', 1)[1])['orange_pixels'] == 960


@pytest.mark.parametrize('bad', [None, True, object(), 'raise'])
def test_atomic_serialization_failure_is_closed_and_pixel_free(monkeypatch, bad):
    def dumps(*_args, **_kwargs):
        if bad == 'raise':
            raise ValueError('synthetic-private')
        return bad
    monkeypatch.setattr(runtime.json, 'dumps', dumps)
    recognizer, captures = recognizer_for(frame(count=960))
    with pytest.raises(runtime.RuntimeSafetyError, match='^HOME_REDUCTION_FAILED$') as caught:
        runtime.BgraGameplayRecognizer.recognize(
            recognizer, BINDING, ACCOUNT, require_home=True,
        )
    assert caught.value.__context__ is None
    assert recognizer.home_diagnostic is None
    assert len(captures) == 1
    assert_no_pixels(caught.value)


@pytest.mark.parametrize('bad', [None, 1, 'true'])
def test_requirement_is_exact_before_capture(bad):
    recognizer, captures = recognizer_for(frame())
    with pytest.raises(runtime.RuntimeSafetyError, match='^HOME_REQUIREMENT_INVALID$'):
        runtime.BgraGameplayRecognizer.recognize(
            recognizer, BINDING, ACCOUNT, require_home=bad,
        )
    assert captures == []
