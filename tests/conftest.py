"""Keep pre-canonical Windows ownership regressions disposable and explicit."""
import pytest


@pytest.fixture(autouse=True)
def historical_surrogate_creation(request, monkeypatch):
    modules = {'test_policy_nested_job', 'test_policy_parent_handoff',
               'test_policy_process_topology', 'test_fixed_package_observation',
               'test_fixed_package_corrections', 'test_owner_corrective_boundaries', 'test_win32_runtime'}
    if request.module.__name__ in modules:
        from surrogate_creation import permit_disposable_interpreter
        from clash_rush_rebuild import mvp_canonical_runtime
        monkeypatch.setattr(mvp_canonical_runtime, 'consume_native_creation', permit_disposable_interpreter)
