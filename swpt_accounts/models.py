import math
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.sql.expression import func, null, or_, and_
from swpt_pythonlib.utils import date_to_int24
from swpt_accounts.extensions import db
from swpt_accounts.events import SECONDS_IN_DAY, INTEREST_RATE_FLOOR, INTEREST_RATE_CEIL, \
    TRANSFER_NOTE_MAX_BYTES, ROOT_CREDITOR_ID  # noqa
from swpt_accounts.events import *  # noqa

MIN_INT16 = -1 << 15
MAX_INT16 = (1 << 15) - 1
MIN_INT32 = -1 << 31
MAX_INT32 = (1 << 31) - 1
MIN_INT64 = -1 << 63
MAX_INT64 = (1 << 63) - 1
T0 = datetime(1970, 1, 1, tzinfo=timezone.utc)
SECONDS_IN_YEAR = 365.25 * SECONDS_IN_DAY
CONFIG_DATA_MAX_BYTES = 2000
IRI_MAX_LENGTH = 200
CONTENT_TYPE_MAX_BYTES = 100
DEBTOR_INFO_SHA256_REGEX = r'^([0-9A-F]{64}|[0-9a-f]{64})?$'


# Reserved coordinator types:
CT_INTEREST = 'interest'
CT_DELETE = 'delete'
CT_DIRECT = 'direct'

# Transfer status codes:
SC_OK = 'OK'
SC_TIMEOUT = 'TERMINATED'
SC_TOO_LOW_INTEREST_RATE = 'TERMINATED'
SC_SENDER_IS_UNREACHABLE = 'SENDER_IS_UNREACHABLE'
SC_RECIPIENT_IS_UNREACHABLE = 'RECIPIENT_IS_UNREACHABLE'
SC_INSUFFICIENT_AVAILABLE_AMOUNT = 'INSUFFICIENT_AVAILABLE_AMOUNT'
SC_RECIPIENT_SAME_AS_SENDER = 'RECIPIENT_SAME_AS_SENDER'
SC_TOO_MANY_TRANSFERS = 'TOO_MANY_TRANSFERS'


def get_now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def calc_k(interest_rate: float) -> float:
    return math.log(1.0 + interest_rate / 100.0) / SECONDS_IN_YEAR


def contain_principal_overflow(value: int) -> int:
    if value <= MIN_INT64:
        return -MAX_INT64
    if value > MAX_INT64:
        return MAX_INT64
    return value


def calc_current_balance(
        *,
        creditor_id: int,
        principal: int,
        interest: float,
        interest_rate: float,
        last_change_ts: datetime,
        current_ts: datetime) -> Decimal:

    current_balance = Decimal(principal)

    # Any interest accumulated on the debtor's account will not be
    # included in the current balance. Thus, accumulating interest on
    # the debtor's account has no effect.
    if creditor_id != ROOT_CREDITOR_ID:
        current_balance += Decimal.from_float(interest)
        if current_balance > 0:
            k = calc_k(interest_rate)
            passed_seconds = max(0.0, (current_ts - last_change_ts).total_seconds())
            current_balance *= Decimal.from_float(math.exp(k * passed_seconds))

    return current_balance


def is_negligible_balance(balance, negligible_amount):
    return balance <= negligible_amount or balance <= 2.0


class Account(db.Model):
    CONFIG_SCHEDULED_FOR_DELETION_FLAG = 1 << 0

    STATUS_DELETED_FLAG = 1 << 0
    STATUS_OVERFLOWN_FLAG = 1 << 1

    debtor_id = db.Column(db.BigInteger, primary_key=True)
    creditor_id = db.Column(db.BigInteger, primary_key=True)
    creation_date = db.Column(db.DATE, nullable=False)
    last_change_seqnum = db.Column(db.Integer, nullable=False, default=0)
    last_change_ts = db.Column(db.TIMESTAMP(timezone=True), nullable=False, default=get_now_utc)
    principal = db.Column(db.BigInteger, nullable=False, default=0)
    interest_rate = db.Column(db.REAL, nullable=False, default=0.0)
    interest = db.Column(db.FLOAT, nullable=False, default=0.0)
    last_interest_rate_change_ts = db.Column(db.TIMESTAMP(timezone=True), nullable=False, default=T0)
    last_config_ts = db.Column(db.TIMESTAMP(timezone=True), nullable=False, default=T0)
    last_config_seqnum = db.Column(db.Integer, nullable=False, default=0)
    last_transfer_number = db.Column(db.BigInteger, nullable=False, default=0)
    last_transfer_committed_at = db.Column(db.TIMESTAMP(timezone=True), nullable=False, default=T0)
    negligible_amount = db.Column(db.REAL, nullable=False, default=0.0)
    config_flags = db.Column(db.Integer, nullable=False, default=0)
    config_data = db.Column(db.String, nullable=False, default='')
    debtor_info_iri = db.Column(db.String)
    debtor_info_content_type = db.Column(db.String)
    debtor_info_sha256 = db.Column(db.LargeBinary)
    status_flags = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        comment="Contain account status bits: "
                f"{STATUS_DELETED_FLAG} - deleted,"
                f"{STATUS_OVERFLOWN_FLAG} - overflown."
    )
    total_locked_amount = db.Column(
        db.BigInteger,
        nullable=False,
        default=0,
        comment='The total sum of all pending transfer locks (the total sum of the values of '
                'the `pending_transfer.locked_amount` column) for this account. This value '
                'has been reserved and must be subtracted from the available amount, to '
                'avoid double-spending.',
    )
    pending_transfers_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        comment='The number of `pending_transfer` records for this account.',
    )
    last_transfer_id = db.Column(
        db.BigInteger,
        nullable=False,
        default=(lambda context: date_to_int24(context.get_current_parameters()['creation_date']) << 40),
        comment='Incremented when a new `prepared_transfer` record is inserted. It is used '
                'to generate sequential numbers for the `prepared_transfer.transfer_id` column. '
                'When the account is created, `last_transfer_id` has its lower 40 bits set '
                'to zero, and its higher 24 bits calculated from the value of `creation_date` '
                '(the number of days since Jan 1st, 1970).',
    )
    previous_interest_rate = db.Column(
        db.REAL,
        nullable=False,
        default=0.0,
        comment='The annual interest rate (in percents) as it was before the last change of '
                'the interest rate happened (see `last_interest_rate_change_ts`).',
    )
    last_heartbeat_ts = db.Column(
        db.TIMESTAMP(timezone=True),
        nullable=False,
        default=get_now_utc,
        comment='The moment at which the last `AccountUpdateSignal` was sent.',
    )
    last_interest_capitalization_ts = db.Column(
        db.TIMESTAMP(timezone=True),
        nullable=False,
        default=T0,
        comment='The moment at which the last interest capitalization was preformed. It is '
                'used to avoid capitalizing interest too often.',
    )
    last_deletion_attempt_ts = db.Column(
        db.TIMESTAMP(timezone=True),
        nullable=False,
        default=T0,
        comment='The moment at which the last deletion attempt was made. It is used to '
                'avoid trying to delete the account too often.',
    )
    pending_account_update = db.Column(
        db.BOOLEAN,
        nullable=False,
        default=False,
        comment='Whether there has been a change in the record that requires an `AccountUpdate` message '
                'to be send.',
    )
    __table_args__ = (
        db.CheckConstraint(and_(
            interest_rate >= INTEREST_RATE_FLOOR,
            interest_rate <= INTEREST_RATE_CEIL,
        )),
        db.CheckConstraint(and_(
            previous_interest_rate >= INTEREST_RATE_FLOOR,
            previous_interest_rate <= INTEREST_RATE_CEIL,
        )),
        db.CheckConstraint(total_locked_amount >= 0),
        db.CheckConstraint(pending_transfers_count >= 0),
        db.CheckConstraint(principal > MIN_INT64),
        db.CheckConstraint(last_transfer_id >= 0),
        db.CheckConstraint(last_transfer_number >= 0),
        db.CheckConstraint(negligible_amount >= 0.0),
        db.CheckConstraint(or_(debtor_info_sha256 == null(), func.octet_length(debtor_info_sha256) == 32)),
        {
            'comment': 'Tells who owes what to whom.',
        }
    )

    def calc_current_balance(self, current_ts: datetime) -> Decimal:
        return calc_current_balance(
            creditor_id=self.creditor_id,
            principal=self.principal,
            interest=self.interest,
            interest_rate=self.interest_rate,
            last_change_ts=self.last_change_ts,
            current_ts=current_ts,
        )

    def calc_due_interest(self, amount: int, due_ts: datetime, current_ts: datetime) -> float:
        """Return the accumulated interest between `due_ts` and `current_ts`.

        When `amount` is a positive number, returns the amount of
        interest that would have been accumulated for the given
        `amount`, between `due_ts` and `current_ts`. When `amount` is
        a negative number, returns `-self.calc_due_interest(-amount,
        due_ts, current_ts)`.

        To calculate the accumulated interest, this function assumes
        that: 1) `current_ts` is the current time; 2) The interest
        rate has not changed more than once between `due_ts` and
        `current_ts`.

        """

        start_ts, end_ts = due_ts, max(due_ts, current_ts)
        interest_rate_change_ts = min(self.last_interest_rate_change_ts, end_ts)
        t = (end_ts - start_ts).total_seconds()
        t1 = max((interest_rate_change_ts - start_ts).total_seconds(), 0)
        t2 = min((end_ts - interest_rate_change_ts).total_seconds(), t)
        k1 = calc_k(self.previous_interest_rate)
        k2 = calc_k(self.interest_rate)

        assert t >= 0
        assert 0 <= t1 <= t
        assert 0 <= t2 <= t
        assert abs(t1 + t2 - t) <= t / 1000

        return amount * (math.exp(k1 * t1 + k2 * t2) - 1.0)


class TransferRequest(db.Model):
    debtor_id = db.Column(db.BigInteger, primary_key=True)
    sender_creditor_id = db.Column(db.BigInteger, primary_key=True)
    transfer_request_id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    coordinator_type = db.Column(db.String(30), nullable=False)
    coordinator_id = db.Column(db.BigInteger, nullable=False)
    coordinator_request_id = db.Column(db.BigInteger, nullable=False)
    min_locked_amount = db.Column(db.BigInteger, nullable=False)
    max_locked_amount = db.Column(db.BigInteger, nullable=False)
    deadline = db.Column(db.TIMESTAMP(timezone=True), nullable=False)
    min_interest_rate = db.Column(db.REAL, nullable=False)
    recipient_creditor_id = db.Column(db.BigInteger, nullable=False)
    __table_args__ = (
        db.CheckConstraint(min_locked_amount >= 0),
        db.CheckConstraint(min_locked_amount <= max_locked_amount),
        db.CheckConstraint(min_interest_rate >= -100.0),
        {
            'comment': 'Represents a request to secure (prepare) some amount for transfer, if '
                       'it is available on a given account. If the request is fulfilled, a new '
                       'row will be inserted in the `prepared_transfer` table. Requests are '
                       'queued to the `transfer_request` table, before being processed, because '
                       'this allows many requests from one sender to be processed at once, '
                       'reducing the lock contention on `account` table rows.',
        }
    )


class FinalizationRequest(db.Model):
    debtor_id = db.Column(db.BigInteger, primary_key=True)
    sender_creditor_id = db.Column(db.BigInteger, primary_key=True)
    transfer_id = db.Column(db.BigInteger, primary_key=True)
    coordinator_type = db.Column(db.String(30), nullable=False)
    coordinator_id = db.Column(db.BigInteger, nullable=False)
    coordinator_request_id = db.Column(db.BigInteger, nullable=False)
    committed_amount = db.Column(db.BigInteger, nullable=False)
    transfer_note_format = db.Column(pg.TEXT, nullable=False)
    transfer_note = db.Column(pg.TEXT, nullable=False)
    ts = db.Column(db.TIMESTAMP(timezone=True), nullable=False)
    __table_args__ = (
        db.CheckConstraint(committed_amount >= 0),
        {
            'comment': 'Represents a request to finalize a prepared transfer. Requests are '
                       'queued to the `finalization_request` table, before being processed, '
                       'because this allows many requests from one sender to be processed at '
                       'once, reducing the lock contention on `account` table rows.',
        }
    )


class PreparedTransfer(db.Model):
    debtor_id = db.Column(db.BigInteger, primary_key=True)
    sender_creditor_id = db.Column(db.BigInteger, primary_key=True)
    transfer_id = db.Column(db.BigInteger, primary_key=True)
    coordinator_type = db.Column(db.String(30), nullable=False)
    coordinator_id = db.Column(db.BigInteger, nullable=False)
    coordinator_request_id = db.Column(db.BigInteger, nullable=False)
    recipient_creditor_id = db.Column(db.BigInteger, nullable=False)
    prepared_at = db.Column(db.TIMESTAMP(timezone=True), nullable=False, default=get_now_utc)
    min_interest_rate = db.Column(db.REAL, nullable=False)
    demurrage_rate = db.Column(db.FLOAT, nullable=False)
    deadline = db.Column(db.TIMESTAMP(timezone=True), nullable=False)
    locked_amount = db.Column(db.BigInteger, nullable=False)
    last_reminder_ts = db.Column(
        db.TIMESTAMP(timezone=True),
        comment='The moment at which the last `PreparedTransferSignal` was sent to remind '
                'that the prepared transfer must be finalized. A `NULL` means that no reminders '
                'have been sent yet. This column helps to prevent sending reminders too often.',
    )
    __table_args__ = (
        db.ForeignKeyConstraint(
            ['debtor_id', 'sender_creditor_id'],
            ['account.debtor_id', 'account.creditor_id'],
            ondelete='CASCADE',
        ),
        db.CheckConstraint(transfer_id > 0),
        db.CheckConstraint(min_interest_rate >= -100.0),
        db.CheckConstraint(locked_amount >= 0),
        db.CheckConstraint((demurrage_rate > -100.0) & (demurrage_rate <= 0.0)),
        {
            'comment': 'A prepared transfer represent a guarantee that a particular transfer of '
                       'funds will be successful if ordered (committed). A record will remain in '
                       'this table until the transfer has been committed or dismissed.',
        }
    )

    def calc_status_code(
            self,
            committed_amount: int,
            expendable_amount: int,
            interest_rate: float,
            current_ts: datetime) -> str:

        assert committed_amount >= 0

        def get_is_expendable():
            return committed_amount <= expendable_amount + self.locked_amount

        def get_is_reserved():
            if committed_amount > self.locked_amount:
                return False

            elif self.sender_creditor_id == ROOT_CREDITOR_ID:
                # We do not need to calculate demurrage for transfers
                # from the debtor's account, because all interest
                # payments come from this account anyway.
                return True

            else:
                demurrage_seconds = max(0.0, (current_ts - self.prepared_at).total_seconds())
                ratio = math.exp(calc_k(self.demurrage_rate) * demurrage_seconds)
                assert ratio <= 1.0

                # To avoid nasty surprises coming from precision loss,
                # we multiply `committed_amount` (a 64-bit integer) to
                # `1.0` before the comparison.
                return committed_amount * 1.0 <= self.locked_amount * ratio

        if committed_amount != 0:
            if current_ts > self.deadline:
                return SC_TIMEOUT

            if interest_rate < self.min_interest_rate:
                return SC_TOO_LOW_INTEREST_RATE

            if not (get_is_expendable() or get_is_reserved()):
                return SC_INSUFFICIENT_AVAILABLE_AMOUNT

        return SC_OK


class RegisteredBalanceChange(db.Model):
    debtor_id = db.Column(db.BigInteger, primary_key=True)
    other_creditor_id = db.Column(db.BigInteger, primary_key=True)
    change_id = db.Column(db.BigInteger, primary_key=True)
    committed_at = db.Column(db.TIMESTAMP(timezone=True), nullable=False)
    is_applied = db.Column(db.BOOLEAN, nullable=False, default=False)
    __table_args__ = (
        {
            'comment': 'Represents the fact that a given pending balance change has been '
                       'registered already. This is necessary in order to avoid applying '
                       'one balance change more than once, when the corresponding '
                       '`PendingBalanceChangeSignal`s is received multiple times.',
        }
    )


class PendingBalanceChange(db.Model):
    debtor_id = db.Column(db.BigInteger, primary_key=True)
    other_creditor_id = db.Column(
        db.BigInteger,
        primary_key=True,
        comment='This is the other party in the transfer. When `principal_delta` is '
                'positive, this is the sender. When `principal_delta` is negative, this '
                'is the recipient.',
    )
    change_id = db.Column(db.BigInteger, primary_key=True)
    creditor_id = db.Column(db.BigInteger, nullable=False)
    coordinator_type = db.Column(db.String(30), nullable=False)
    transfer_note_format = db.Column(pg.TEXT, nullable=False)
    transfer_note = db.Column(pg.TEXT, nullable=False)
    committed_at = db.Column(db.TIMESTAMP(timezone=True), nullable=False)
    principal_delta = db.Column(db.BigInteger, nullable=False)
    __table_args__ = (
        db.ForeignKeyConstraint(
            [
                'debtor_id',
                'other_creditor_id',
                'change_id',
            ],
            [
                'registered_balance_change.debtor_id',
                'registered_balance_change.other_creditor_id',
                'registered_balance_change.change_id',
            ],
        ),
        db.CheckConstraint(principal_delta != 0),
        db.Index('idx_changed_account', debtor_id, creditor_id),
        {
            'comment': 'Represents a pending change in the balance of a given account. Pending '
                       'updates to `account.principal` are queued to this table before being '
                       'processed, thus allowing multiple updates to one account to coalesce, '
                       'reducing the lock contention on `account` table rows.',
        }
    )
