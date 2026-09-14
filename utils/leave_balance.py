# -*- coding: utf-8 -*-
"""Live annual-leave balance.

Doctrine: the balance is derived from documents only —
  opening  : the certified balance carried in on the migration cutoff date
             (employees.previous_leave_balance). Applies ONLY to staff hired
             before the cutoff; for anyone hired after it, accrual simply
             starts at their hire date and the opening balance is 0.
  accrued  : monthly accrual (settings.leave_earned_per_month) pro-rata from
             the effective start (max of hire date and cutoff), clamped at
             end_of_service_date when present;
  taken    : approved, non-deleted, daily-duration, PAID, non-sick leaves
             recorded ON OR AFTER the effective start — leave consumed before
             go-live is already reflected inside the opening balance, so
             counting it again would deduct it twice;
  adjusted : signed rows in leave_balance_adjustments, each with a reason
             and a recorder (encashments, corrections, arbitration).

Migration rationale: a company going live in 2026 with staff hired in 2019
cannot reconstruct seven years of leave history it never recorded. Accruing
from the hire date while knowing only recently-recorded leave produces a
large fictitious balance, and that figure turns into real money at end of
service. Instead the company certifies each employee's balance as of a
cutoff date and the system runs forward from there — the same way an opening
trial balance works in accounting.

With no cutoff configured, behaviour is unchanged: accrual from hire date.
"""
from datetime import datetime, date


def _d(v):
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def compute_leave_balance(conn, employee_id, as_of=None):
    emp = conn.execute('SELECT id, hire_date, end_of_service_date, '
                       'previous_leave_balance '
                       'FROM employees WHERE id = ?',
                       (employee_id,)).fetchone()
    if not emp:
        return None
    hire = _d(emp['hire_date'])
    if not hire:
        return None
    if as_of is None:
        as_of = date.today()
    elif isinstance(as_of, str):
        as_of = _d(as_of) or date.today()
    eos = _d(emp['end_of_service_date'])
    if eos and eos < as_of:
        as_of = eos

    accrual = 2.5
    cutoff = None
    try:
        from utils.settings_utils import get_salary_settings_v2
        sal = get_salary_settings_v2(conn)
        accrual = float(sal.get('leave_earned_per_month', 2.5) or 2.5)
        cutoff = _d(sal.get('leave_migration_cutoff_date') or '')
    except Exception:
        pass

    # Pre-existing staff carry an opening balance from the cutoff date;
    # anyone hired later starts clean at their own hire date.
    keys = emp.keys()
    if cutoff and hire < cutoff:
        effective_start = cutoff
        opening = round(float(
            (emp['previous_leave_balance'] if 'previous_leave_balance' in keys
             else 0) or 0), 2)
    else:
        effective_start = hire
        opening = 0.0

    days = max(0, (as_of - effective_start).days)
    months = days / 30.4375
    accrued = round(months * accrual, 2)

    taken = conn.execute("""
        SELECT COALESCE(SUM(lr.days_count), 0) AS n
        FROM leave_requests lr
        JOIN leave_types lt ON lt.id = lr.leave_type_id
        WHERE lr.employee_id = ? AND lr.status = 'approved'
          AND COALESCE(lr.is_deleted, 0) = 0
          AND COALESCE(lr.leave_duration_type, 'full_day') != 'hourly'
          AND COALESCE(lr.is_paid_leave, 1) = 1
          AND DATE(lr.start_date) >= ?
          AND DATE(lr.start_date) <= ?
          AND LOWER(COALESCE(lt.name, '')) NOT LIKE '%مرض%'
          AND LOWER(COALESCE(lt.name, '')) NOT LIKE '%sick%'""",
        (employee_id, effective_start.isoformat(),
         as_of.isoformat())).fetchone()['n']
    taken = round(float(taken or 0), 2)

    adjusted = conn.execute("""
        SELECT COALESCE(SUM(days), 0) FROM leave_balance_adjustments
        WHERE employee_id = ? AND DATE(created_at) <= ?""",
        (employee_id, as_of.isoformat())).fetchone()[0]
    adjusted = round(float(adjusted or 0), 2)

    return {'employee_id': employee_id,
            'as_of': as_of.isoformat(),
            'cutoff_date': cutoff.isoformat() if cutoff else None,
            'effective_start': effective_start.isoformat(),
            'pre_existing': bool(cutoff and hire < cutoff),
            'opening': opening,
            'service_months': round(months, 2),
            'accrual_per_month': accrual,
            'accrued': accrued,
            'taken': taken,
            'adjusted': adjusted,
            'balance': round(opening + accrued - taken + adjusted, 2)}
