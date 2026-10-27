import pytest
from voice_test_harness import CASES, evaluate_report, suite_lock


def test_one_registry_includes_all_workflows():
    scripts={row[1] for row in CASES}
    assert {'test_voice_decline_alternative.py','test_voice_decline_then_confirm.py',
            'test_voice_baseline_full_slot.py','test_baseline_last_table_race.py',
            'test_voice_natural_dates.py','test_handsfree_voice_flow.py','test_voice_retry_reset.py','test_voice_customer_feedback.py'}<=scripts
    assert len({row[0] for row in CASES})==len(CASES)


@pytest.mark.parametrize('report,code',[
    ({'passed':True,'checks':{'x':True},'cleanup':[]},0),
    ({'passed':True,'checks':{'x':True},'cleanup':{'cancelled':False}},0),
    ({'passed':True,'checks':{'x':False},'cleanup':{'cancelled':True}},0),
    ({'passed':True,'checks':{'x':True},'cleanup':{'cancelled':True}},1),
    ({},0),
])
def test_no_false_pass_when_checks_cleanup_or_process_fail(report,code):
    assert evaluate_report(report,code)['status']=='failed'


def test_success_needs_process_checks_and_cleanup():
    result=evaluate_report({'passed':True,'checks':{'x':True},'cleanup':[{'cancelled':True}]},0)
    assert result['status']=='passed' and result['cleanup_verified']


def test_lock_prevents_overlapping_cli_and_ui_suites(tmp_path,monkeypatch):
    import voice_test_harness as harness
    monkeypatch.setattr(harness,'OUTPUT',tmp_path)
    with suite_lock():
        with pytest.raises(RuntimeError,match='already running'):
            with suite_lock():pass
