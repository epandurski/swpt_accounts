import pytest
from datetime import datetime, timezone
from swpt_accounts import procedures as p
from swpt_accounts.models import RejectedTransferSignal

D_ID = -1
C_ID = 1


@pytest.fixture(scope='function')
def actors():
    from swpt_accounts import actors
    return actors


def test_prepare_transfer(db_session, actors):
    actors.prepare_transfer(
        coordinator_type='test',
        coordinator_id=1,
        coordinator_request_id=2,
        min_locked_amount=1,
        max_locked_amount=200,
        debtor_id=D_ID,
        creditor_id=C_ID,
        recipient='1234',
        min_interest_rate=-100.0,
        max_commit_delay=1000000,
        ts=datetime.now(tz=timezone.utc).isoformat(),
    )
    actors.prepare_transfer(
        coordinator_type='test',
        coordinator_id=1,
        coordinator_request_id=2,
        min_locked_amount=1,
        max_locked_amount=200,
        debtor_id=D_ID,
        creditor_id=C_ID,
        recipient='invalid',
        min_interest_rate=-100.0,
        max_commit_delay=1000000,
        ts=datetime.now(tz=timezone.utc).isoformat(),
    )

    p.process_transfer_requests(D_ID, C_ID)
    signals = RejectedTransferSignal.query.all()
    assert len(signals) == 2

    for rts in signals:
        assert rts.debtor_id == D_ID
        assert rts.coordinator_type == 'test'
        assert rts.coordinator_id == 1
        assert rts.coordinator_request_id == 2


def test_finalize_transfer(db_session, actors):
    actors.finalize_transfer(
        debtor_id=D_ID,
        creditor_id=C_ID,
        transfer_id=666,
        coordinator_type='test',
        coordinator_id=1,
        coordinator_request_id=2,
        committed_amount=100,
        transfer_note_format='',
        transfer_note='',
        ts=datetime.now(tz=timezone.utc).isoformat(),
    )


def test_configure_account(db_session, actors):
    from swpt_accounts.fetch_api_client import _clear_root_config_data

    actors.configure_account(
        debtor_id=D_ID,
        creditor_id=C_ID,
        ts='2099-12-31T00:00:00+00:00',
        seqnum=0,
        negligible_amount=500.0,
        config_flags=0,
        config_data='',
    )
    _clear_root_config_data()


def test_on_pending_balance_change_signal(db_session, actors):
    actors.on_pending_balance_change_signal(
        debtor_id=D_ID,
        creditor_id=C_ID,
        change_id=1,
        coordinator_type='direct',
        transfer_note_format='',
        transfer_note='',
        committed_at='2099-12-31T00:00:00+00:00',
        principal_delta=1000,
        other_creditor_id=123,
    )
