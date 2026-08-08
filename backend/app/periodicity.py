"""When the next examination falls due.

"Periodic" is the whole point of a PME programme, and it was the one thing the
API could not answer: examinations could be recorded but nothing said who was
due or overdue. The rule lives here rather than inline in the route so the
intervals are one edit away and the arithmetic is tested once.

How the next due date is chosen, in order:

1. The doctor's explicit recall date, if they gave one. A clinician overrides
   the schedule — that is the point of having one examine the person.
2. Otherwise the outcome's validity period, counted from the examination date:
   a FIT result is good for a year, a provisional one needs a much earlier
   recheck.

An UNFIT outcome sets no due date. The employee is not on a routine cycle any
more; what happens next is a case decision, not a calendar entry, and inventing
a date would quietly turn a serious finding into a routine recall.
"""

import calendar
from datetime import date

from app.config import settings
from app.models import FitnessStatus


def validity_months(outcome: FitnessStatus) -> int | None:
    """Months an outcome stays valid, or None if it starts no new cycle."""
    return {
        FitnessStatus.FIT: settings.PME_VALIDITY_MONTHS_FIT,
        FitnessStatus.TEMPORARILY_UNFIT: settings.PME_VALIDITY_MONTHS_TEMPORARY,
        FitnessStatus.UNFIT: None,
    }[outcome]


def add_months(start: date, months: int) -> date:
    """Return ``start`` advanced by whole calendar months.

    Clamps to the end of the target month, so 31 January + 1 month is 28 or 29
    February rather than an invalid date, and 29 February + 12 months lands on
    28 February in a common year. ``dateutil`` does this too, but not for one
    function's worth of arithmetic.
    """
    zero_based = start.month - 1 + months
    year = start.year + zero_based // 12
    month = zero_based % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


def next_due_date(outcome: FitnessStatus, exam_date: date,
                  doctor_recall: date | None = None) -> date | None:
    """Work out when the next PME is due after this one.

    Args:
        outcome: The fitness decision just recorded.
        exam_date: When the examination happened; the period runs from here, not
            from the scheduled date, so a late examination does not compound.
        doctor_recall: An explicit recall date, which always wins.

    Returns:
        The due date, or None when the outcome starts no new cycle (UNFIT).

    """
    if doctor_recall is not None:
        return doctor_recall
    months = validity_months(outcome)
    return None if months is None else add_months(exam_date, months)
