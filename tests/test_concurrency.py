import pytest
from concurrency import TurnScheduler


def test_scheduler_releases_permits_on_business_exception():
    scheduler = TurnScheduler(workers=1, waiting=0)
    with pytest.raises(RuntimeError):
        with scheduler.slot():
            raise RuntimeError('business failure')
    with scheduler.slot():
        assert scheduler.status()['active'] == 1
    assert scheduler.status()['active'] == scheduler.status()['waiting'] == 0


@pytest.mark.parametrize('kwargs', [{'workers':0}, {'workers':5}, {'waiting':17}, {'wait_seconds':float('nan')}, {'wait_seconds':-1}])
def test_invalid_scheduler_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        TurnScheduler(**kwargs)
