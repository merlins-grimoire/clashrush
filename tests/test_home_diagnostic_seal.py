"""Synthetic adversarial matrix for the scalar HOME seam."""
import json
from dataclasses import asdict

import pytest

from clash_rush_rebuild import mvp_local_runtime as runtime
from test_home_diagnostics import ACCOUNT, BINDING, frame, recognizer_for


def diagnostic():
    recognizer, _ = recognizer_for(frame(count=960))
    recognizer.recognize(BINDING, ACCOUNT)
    return recognizer.home_diagnostic


def assert_no_pixels(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
        trace = current.__traceback__
        while trace:
            if trace.tb_frame.f_code.co_name not in {'assert_no_pixels'} and trace.tb_frame.f_globals.get('__name__') != __name__:
                for item in trace.tb_frame.f_locals.values():
                    assert not (type(item) in (bytes, bytearray) and len(item) > 1024)
                    assert not (type(item) is tuple and any(type(part) is bytes and len(part) > 1024 for part in item))
            trace = trace.tb_next


def test_one_reduction_cannot_contradict_mutating_hsv(monkeypatch):
    calls = []
    def hsv(*_channels):
        calls.append(None)
        return (10, 255, 255) if len(calls) <= 3200 else (0, 0, 0)
    monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_hsv', hsv)
    recognizer, captures = recognizer_for(frame())
    result = recognizer.recognize(BINDING, ACCOUNT)
    assert result.home is (recognizer.home_diagnostic.reason == 'HOME_POSITIVE')
    assert len(calls) == 3200
    assert len(captures) == 1


def test_home_does_not_invoke_second_orange_pass(monkeypatch):
    def fail(*_channels):
        raise RuntimeError('synthetic second pass')
    monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_orange', fail)
    recognizer, _ = recognizer_for(frame())
    assert recognizer.recognize(BINDING, ACCOUNT).home is True


@pytest.mark.parametrize('method', ['_diagnose_home', '_fraction'])
def test_direct_reducers_clear_pixels_on_exception(monkeypatch, method):
    def fail(*_channels):
        raise RuntimeError('synthetic predicate')
    monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_hsv', fail)
    with pytest.raises(RuntimeError) as caught:
        if method == '_fraction':
            runtime.BgraGameplayRecognizer._fraction(frame(), (.035, .90, .085, .97), fail)
        else:
            runtime.BgraGameplayRecognizer._diagnose_home(frame())
    assert_no_pixels(caught.value)


class IntSubclass(int):
    pass


@pytest.mark.parametrize('field', list(runtime.HomeDiagnostic.__dataclass_fields__))
@pytest.mark.parametrize('bad', [True, IntSubclass(1), -1, 10**100, 'synthetic-private', None])
def test_constructor_rejects_nonexact_or_unbounded_fields(field, bad):
    values = asdict(diagnostic())
    values[field] = bad
    with pytest.raises(runtime.RuntimeSafetyError):
        runtime.HomeDiagnostic(**values)


@pytest.mark.parametrize('changes', [
    {'left': 45}, {'top': 647}, {'right': 109}, {'bottom': 699},
    {'roi_pixels': 3199}, {'orange_pixels': 961},
    {'hue_pixels': 3200, 'saturation_pixels': 3200, 'value_pixels': 3200},
    {'width': 639}, {'height': 359},
])
def test_constructor_rejects_cross_field_inconsistency(changes):
    values = asdict(diagnostic())
    values.update(changes)
    with pytest.raises(runtime.RuntimeSafetyError):
        runtime.HomeDiagnostic(**values)


@pytest.mark.parametrize('mutation', ['bool', 'delete', 'overflow'])
def test_serialization_revalidates_postconstruction_mutation(mutation):
    value = diagnostic()
    if mutation == 'delete':
        object.__delattr__(value, 'orange_pixels')
    else:
        object.__setattr__(value, 'orange_pixels', True if mutation == 'bool' else 99999)
    with pytest.raises(runtime.RuntimeSafetyError):
        runtime.HomeDiagnostic.to_json(value)


def test_exact_type_rejects_subclass_even_when_base_method_called():
    class Subclass(runtime.HomeDiagnostic):
        @property
        def reason(self):
            return 'synthetic-private'
        def to_json(self):
            return 'synthetic-private'
    with pytest.raises(runtime.RuntimeSafetyError):
        value = Subclass(**asdict(diagnostic()))
        runtime.HomeDiagnostic.to_json(value)


@pytest.mark.parametrize('shadow', ['_diagnose_home', '_fraction', '_frame'])
def test_instance_helpers_cannot_replace_home_reduction(shadow):
    recognizer, captures = recognizer_for(frame(count=0))
    def fail(*_args):
        raise AssertionError('instance helper invoked')
    setattr(recognizer, shadow, fail)
    assert recognizer.recognize(BINDING, ACCOUNT).home is False
    assert len(captures) == 1


@pytest.mark.parametrize('bad', [(True, 100, 120), (10, 100, 256), (10, 'private', 120), (10, 100)])
def test_malformed_hsv_result_fails_closed_without_retained_pixels(monkeypatch, bad):
    monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_hsv', lambda *_args: bad)
    recognizer, _ = recognizer_for(frame())
    with pytest.raises(runtime.RuntimeSafetyError) as caught:
        recognizer.recognize(BINDING, ACCOUNT)
    assert recognizer.home_diagnostic is None
    assert_no_pixels(caught.value)


def test_capture_exception_chain_is_sanitized_and_scrubbed():
    retained = []
    def capture(_binding):
        pixels = frame()[2]
        try:
            raise ValueError('synthetic-private')
        except ValueError as cause:
            error = RuntimeError('synthetic-private')
            retained.append(error)
            raise error from cause
    recognizer = runtime.BgraGameplayRecognizer(BINDING, capture, account_verified=False)
    with pytest.raises(runtime.RuntimeSafetyError) as caught:
        recognizer.recognize(BINDING, ACCOUNT)
    assert 'synthetic-private' not in str(caught.value)
    assert caught.value.__context__ is None
    assert_no_pixels(caught.value)
    for error in retained:
        trace = error.__traceback__
        while trace:
            if trace.tb_frame.f_code.co_name == 'capture':
                assert not trace.tb_frame.f_locals
            trace = trace.tb_next


def test_exact_serialization_has_closed_canonical_grammar():
    value = diagnostic()
    result = runtime.HomeDiagnostic.to_json(value)
    assert type(result) is str
    payload = json.loads(result)
    assert result == json.dumps(payload, sort_keys=True, separators=(',', ':'))
    assert set(payload) == set(asdict(value)) | {'schema', 'reason'}
    assert payload['schema'] == 1
    assert payload['reason'] == 'FRACTION_LOW'


def test_postcapture_validation_exception_clears_frame():
    from dataclasses import replace
    binding = replace(BINDING)
    def capture(_binding):
        value = frame()
        object.__delattr__(binding, 'width')
        return value
    recognizer = runtime.BgraGameplayRecognizer(binding, capture, account_verified=False)
    with pytest.raises(runtime.RuntimeSafetyError, match='FRAME_VALIDATION_FAILED') as caught:
        recognizer.recognize(binding, ACCOUNT)
    assert caught.value.__context__ is None
    assert_no_pixels(caught.value)


@pytest.mark.parametrize('method', ['army_ready', 'return_home_visible', 'scout_ready'])
def test_sibling_fraction_failure_clears_service_frame_locals(monkeypatch, method):
    recognizer, _ = recognizer_for(frame())
    recognizer.capture_scout_source()
    original = runtime.BgraGameplayRecognizer._fraction
    def fail(*_channels):
        raise RuntimeError('synthetic sibling failure')
    def fraction(value, region, _predicate):
        try:
            return original(value, region, fail)
        finally:
            value = None
    monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_fraction', staticmethod(fraction))
    with pytest.raises(RuntimeError) as caught:
        getattr(recognizer, method)()
    assert_no_pixels(caught.value)


@pytest.mark.parametrize('stage', ['reduce', 'construct'])
def test_retained_reduction_exception_tracebacks_are_scrubbed(monkeypatch, stage):
    retained = []
    def fail(*_args):
        try:
            raise ValueError('synthetic-private')
        except ValueError as cause:
            error = RuntimeError('synthetic-private')
            retained.append(error)
            raise error from cause
    if stage == 'reduce':
        monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_diagnose_home', staticmethod(fail))
    else:
        monkeypatch.setattr(runtime.HomeDiagnostic, '__post_init__', fail)
    recognizer, _ = recognizer_for(frame())
    with pytest.raises(runtime.RuntimeSafetyError, match='HOME_REDUCTION_FAILED') as caught:
        recognizer.recognize(BINDING, ACCOUNT)
    assert caught.value.__context__ is None
    assert recognizer.home_diagnostic is None
    for error in retained:
        trace = error.__traceback__
        while trace:
            if trace.tb_frame.f_code.co_name == 'fail':
                assert not trace.tb_frame.f_locals
            trace = trace.tb_next
        assert_no_pixels(error)


@pytest.mark.parametrize('bad', [None, True, object()])
def test_emission_rejects_nonexact_serializer_result(monkeypatch, bad):
    value = diagnostic()
    monkeypatch.setattr(runtime.json, 'dumps', lambda *_args, **_kw: bad)
    with pytest.raises(runtime.RuntimeSafetyError, match='HOME_DIAGNOSTIC_INVALID'):
        runtime.HomeDiagnostic.to_json(value)
    assert runtime.home_not_verified_message(value) == 'HOME_NOT_VERIFIED'


def test_emission_rejects_forged_subclass_without_invoking_helpers():
    class Forged(runtime.HomeDiagnostic):
        def __post_init__(self):
            pass
        def to_json(self):
            raise AssertionError('untrusted helper invoked')
        @property
        def reason(self):
            raise AssertionError('untrusted helper invoked')
    value = Forged(**asdict(diagnostic()))
    assert runtime.home_not_verified_message(value) == 'HOME_NOT_VERIFIED'


def test_malformed_reduction_return_cannot_retain_full_frame(monkeypatch):
    monkeypatch.setattr(runtime.BgraGameplayRecognizer, '_diagnose_home', staticmethod(lambda value: value))
    recognizer, _ = recognizer_for(frame())
    with pytest.raises(runtime.RuntimeSafetyError) as caught:
        recognizer.recognize(BINDING, ACCOUNT)
    assert_no_pixels(caught.value)
    assert recognizer.home_diagnostic is None


def test_capture_exception_group_scrubs_nested_tracebacks():
    retained = []
    def inner():
        pixels = frame()[2]
        raise ValueError('synthetic-private')
    def capture(_binding):
        try:
            inner()
        except ValueError as error:
            retained.append(error)
            raise ExceptionGroup('synthetic-private', [error])
    recognizer = runtime.BgraGameplayRecognizer(BINDING, capture, account_verified=False)
    with pytest.raises(runtime.RuntimeSafetyError, match='FRAME_CAPTURE_FAILED') as caught:
        recognizer.recognize(BINDING, ACCOUNT)
    assert caught.value.__context__ is None
    trace = retained[0].__traceback__
    while trace:
        if trace.tb_frame.f_code.co_name in {'capture', 'inner'}:
            assert not trace.tb_frame.f_locals
        trace = trace.tb_next
