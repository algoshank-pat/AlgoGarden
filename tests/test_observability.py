import json
from unittest.mock import Mock

import pytest
import observability as obs
from routing import Decision


@pytest.fixture
def exports(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.setenv('LANGSMITH_API_KEY', 'test-not-a-real-key')
    root = Mock()
    root.id = 'trace-id'
    child = Mock()
    root.create_child.return_value = child
    constructor = Mock(return_value=root)
    monkeypatch.setattr(obs, 'RunTree', constructor)
    monkeypatch.setattr(obs, 'client', lambda: Mock())
    yield root, child, constructor
    obs.drain_exports()


def test_disabled_never_initializes_client(monkeypatch):
    monkeypatch.delenv('LANGSMITH_TRACING', raising=False)
    client = Mock(side_effect=AssertionError('No network'))
    monkeypatch.setattr(obs, 'client', client)
    work = obs.observed('test')(lambda: 42)
    assert work() == 42
    client.assert_not_called()


def test_missing_credentials_disables_tracing(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.delenv('LANGSMITH_API_KEY', raising=False)
    assert not obs.enabled()


def test_raw_inputs_outputs_and_reasoning_not_exported(exports):
    root, child, constructor = exports
    @obs.observed('intent.classify')
    def work(text):
        return Decision('reservation', 'llm', reasoning='Alex 9900112233 wants a table', confidence=.9)
    result = work('Alex 9900112233')
    obs.drain_exports()
    assert result.reasoning.startswith('Alex')
    exported = str(constructor.call_args)+str(root.mock_calls)
    assert 'Alex' not in exported and '9900112233' not in exported
    assert root.end.call_args.kwargs['outputs']['confidence'] == .9


def test_nested_spans_share_parent(exports):
    root, child, constructor = exports
    @obs.observed('tool', 'tool')
    def tool():
        return {'booked': True, 'name': 'Alex', 'phone': '9900112233'}
    @obs.observed('turn')
    def turn():
        return tool()
    assert turn()['booked']
    obs.drain_exports()
    assert constructor.call_count == 1
    assert root.create_child.call_args.args == ('tool',)
    assert child.end.call_args.kwargs['outputs'] == {'booked': True}
    assert obs._parent.get() is None


@pytest.mark.parametrize('stage', ['post', 'patch', 'end'])
def test_export_failure_never_reexecutes_business_operation(exports, stage):
    root, _, _ = exports
    getattr(root, stage).side_effect = RuntimeError('export failure')
    business = Mock(return_value={'booked': True})
    assert obs.observed('book_table', 'tool')(business)()['booked']
    obs.drain_exports()
    business.assert_called_once()
    assert obs._parent.get() is None


def test_business_errors_propagate_without_exporting_message(exports):
    root, _, _ = exports
    @obs.observed('test')
    def fail():
        raise ValueError('Alex 9900112233')
    with pytest.raises(ValueError):
        fail()
    obs.drain_exports()
    assert 'Alex' not in str(root.mock_calls)
    assert obs._parent.get() is None


def test_tool_summary_omits_contact_and_free_text():
    assert obs.summary({'name': 'Alex', 'phone': '9900112233', 'message': 'secret', 'saved': True}) == {'saved': True}
