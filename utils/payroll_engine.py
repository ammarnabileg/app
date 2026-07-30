# -*- coding: utf-8 -*-
"""
Monthly Payroll Engine — instrument-based, attestation model.

Security principle: no figure is ever hand-entered.
  DOCUMENTS  -> punches, approved leaves, official holidays, weekly-off
                schedule, attendance excuses, manual punch corrections.
  COMPUTE    -> deterministic metrics derived only from those documents.
  ATTEST     -> approval snapshots the computed metrics (a signature).
                Any later document change makes the signature STALE.
  DISBURSE   -> payroll save is blocked unless every employee's signature
                is present and fresh.
"""

import calendar
import json
from datetime import date, datetime, timedelta

from utils.settings_utils import get_salary_settings_v2

MONEY_EPS = 0.0005
DAILY_RATE_DIVISOR = 30.0  # matches the existing comprehensive report's basis

# Fields that constitute an attested signature. Any drift = stale approval.
METRIC_FIELDS = ('required_days', 'actual_days', 'required_hours', 'actual_hours',
                 'partial_days', 'absent_days', 'sick_days', 'unpaid_days', 'excuse_days',
                 'weekly_off_days', 'holiday_days', 'late_mins', 'early_mins',
                 'presence_missing', 'hourly_perm_hours',
                 'ot_weekday_mins', 'ot_weekend_mins', 'ot_holiday_mins',
                 'unentitled_rest_days')

SICK_NAME_HINTS = ('مرض', 'sick')


def _parse_dt(s):
    if not s:
        return None
    s = str(s).replace('T', ' ')[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_d(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _weekly_off_set(emp):
    """App convention day numbers (0=Sunday .. 6=Saturday = isoweekday % 7)."""
    raw = emp['weekly_leave_selected_days'] if 'weekly_leave_selected_days' in emp.keys() else None
    days = set()
    if raw:
        for part in str(raw).split(','):
            part = part.strip()
            if part.isdigit():
                days.add(int(part) % 7)
    if days:
        return days
    try:
        start = int(emp['weekly_leave_start']) if emp['weekly_leave_start'] is not None else 5
    except (ValueError, TypeError):
        start = 5
    try:
        count = int(emp['weekly_leave_days']) if emp['weekly_leave_days'] is not None else 2
    except (ValueError, TypeError):
        count = 2
    count = max(0, min(count, 7))
    return {(start + i) % 7 for i in range(count)}


def _is_sick_type(name):
    n = (name or '').lower()
    return any(h in n for h in SICK_NAME_HINTS)


def _t2m(t, default):
    try:
        h, mm = str(t or default)[:5].split(':')
        return int(h) * 60 + int(mm)
    except Exception:
        h, mm = default.split(':')
        return int(h) * 60 + int(mm)


def _charged_late_early(first_t, last_t, emp, sal):
    """
    Charged late/early minutes for one punched day, mirroring v3 semantics:
      - grace-gated trigger, but ACTUAL charge counted from shift start/end
      - policy converts the charge: warning->0, actual_time->minutes,
        quarter/half/full_day -> that fraction of the shift in minutes.
    Returns (late_charged, early_charged) ints.
    """
    keys = emp.keys()
    sh_start = _t2m(emp['shift_start'] if 'shift_start' in keys else None,
                    (emp['default_start_time'] if 'default_start_time' in keys
                     and emp['default_start_time'] else '08:00'))
    sh_end = _t2m(emp['shift_end'] if 'shift_end' in keys else None,
                  (emp['default_end_time'] if 'default_end_time' in keys
                   and emp['default_end_time'] else '16:00'))
    shift_mins = int(float(emp['hours_per_day'] or 8) * 60)
    grace_late = int(float(sal.get('grace_late_minutes', 10) or 10))
    grace_early = int(float(sal.get('early_departure_grace_minutes',
                                    sal.get('grace_early_minutes', 5)) or 5))
    pol_late = sal.get('late_arrival_policy', 'actual_time')
    pol_early = sal.get('early_departure_policy', 'actual_time')

    def convert(actual, policy):
        if policy == 'warning':
            return 0
        if policy == 'quarter_day':
            return int(shift_mins * 0.25)
        if policy == 'half_day':
            return int(shift_mins * 0.50)
        if policy == 'full_day':
            return shift_mins
        return int(actual)  # actual_time

    late = early = 0
    if first_t:
        fin = _t2m(first_t, '00:00')
        if fin > sh_start + grace_late:
            late = convert(fin - sh_start, pol_late)
    if last_t:
        lout = _t2m(last_t, '00:00')
        if lout < sh_end - grace_early:
            early = convert(sh_end - lout, pol_early)
    return late, early


def _emp_flex_mode(emp):
    """The explicit shift_types.flex_mode column is the ONLY source of
    flexibility. Names carry no behavior: legacy flexibly-named shifts were
    frozen into an explicit mode by a one-shot startup migration."""
    keys = emp.keys()
    fm = (emp['flex_mode'] if 'flex_mode' in keys else None) or 'none'
    return fm if fm in ('any_time', 'fixed_hours') else 'none'


def _ot_round(mins, step):
    """Overtime rounding: floor to multiples of `step` minutes (0 = exact)."""
    m = max(0, int(mins))
    if step and step > 0:
        m = (m // int(step)) * int(step)
    return m


def _presence_punches(times, ps, pe):
    """All punch times that fell inside the presence window [ps, pe]."""
    return [t for t in times if ps <= t <= pe]


def _presence_missing(times, ps, pe):
    """True when a presence window is set and NO punch fell inside it.
    Mirrors the original presence report: any punch with ps <= t <= pe is OK."""
    return not _presence_punches(times, ps, pe)


def metrics_signature(m):
    return tuple(round(float(m.get(f) or 0), 2) for f in METRIC_FIELDS)


PAYROLL_EMP_COLUMNS = """e.id, e.employee_number, e.name, e.arabic_name,
       e.department, e.position, e.salary, e.hire_date, e.end_of_service_date, e.is_active,
       e.weekly_leave_selected_days, e.weekly_leave_start, e.weekly_leave_days,
       e.default_start_time, e.default_end_time,
       COALESCE(st.hours_per_day, 8) AS hours_per_day,
       st.start_time AS shift_start, st.end_time AS shift_end,
       st.presence_start_time, st.presence_end_time,
       st.flex_mode, st.name AS shift_name, st.description AS shift_notes,
       e.shift_type"""


def fetch_payroll_employees(conn, dept=None, employee_id=None, active_only=True, period=None):
    """THE single employees+shift source for the whole payroll engine and its
    routes. Every consumer (metrics, payroll, approval API, print, day
    drill-down) MUST use this instead of writing its own SELECT — duplicated
    column lists caused two real bugs (pages disagreeing on the same truth)."""
    q = f"""SELECT {PAYROLL_EMP_COLUMNS}
            FROM employees e
            LEFT JOIN shift_types st ON st.name = e.shift_type
            WHERE 1 = 1"""
    params = []
    if period is not None:
        # Service-window rule: the DATE decides, not the flag.
        ps, pe = period
        q += (' AND DATE(e.hire_date) <= ?'
              ' AND (e.end_of_service_date IS NULL'
              '      OR DATE(e.end_of_service_date) >= ?)')
        params.extend([pe.isoformat(), ps.isoformat()])
    elif active_only:
        q += ' AND e.is_active = 1'
    if employee_id is not None:
        q += ' AND e.id = ?'
        params.append(int(employee_id))
    if dept:
        q += ' AND e.department = ?'
        params.append(dept)
    q += ' ORDER BY CAST(e.employee_number AS INTEGER), e.employee_number'
    return conn.execute(q, params).fetchall()


def resolve_period(conn, month, year):
    """
    Resolves the company payroll cycle for a labeled (month, year).
    system_settings.payroll_period_start_day (D, 1-28):
      D == 1 -> calendar month [1st .. last day of month]
      D  > 1 -> [day D of previous month .. day D-1 of labeled month]
    Read directly from DB (bypasses the settings cache) so a changed setting
    takes effect immediately and deterministically.
    """
    month = int(month)
    year = int(year)
    d = 1
    try:
        r = conn.execute("SELECT payroll_period_start_day FROM system_settings WHERE id = 1").fetchone()
        if r and r['payroll_period_start_day'] is not None:
            d = int(r['payroll_period_start_day'])
    except Exception:
        d = 1
    d = max(1, min(d, 28))
    if d == 1:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    else:
        pm, py = (12, year - 1) if month == 1 else (month - 1, year)
        start = date(py, pm, d)
        end = date(year, month, d) - timedelta(days=1)
    return start, end


def compute_month_metrics(conn, month, year, employees=None):
    """
    Deterministic monthly metrics per active employee, computed ONLY from
    documented sources. Returns {employee_id: metrics dict} with keys:
      required_days, required_hours  (scheduled minus weekly off / holidays
                                      / approved leaves / excuses)
      actual_days, actual_hours      (days with a valid first->last span;
                                      spans on off/holiday days still count)
      partial_days                   (scheduled days punched but with no
                                      valid span -> missing punch)
      absent_days                    (scheduled days with no punch at all)
      sick_days, unpaid_days         (working days inside approved leaves,
                                      classified by leave type / paid flag)
      excuse_days                    (working days covered by an excuse)
    """
    month = int(month)
    year = int(year)
    p_start, p_end = resolve_period(conn, month, year)
    start_iso, end_iso = p_start.isoformat(), p_end.isoformat()

    if employees is None:
        employees = fetch_payroll_employees(conn, period=(p_start, p_end))
    sal = get_salary_settings_v2(conn)

    holidays = {
        r['date'][:10] for r in conn.execute(
            "SELECT date FROM official_holidays WHERE DATE(date) BETWEEN ? AND ?",
            (start_iso, end_iso)).fetchall()
    }

    # ALL punches per employee per day (times list feeds the presence window)
    punch_rows = conn.execute('''
        SELECT employee_id, DATE(check_time) AS d, check_time
        FROM attendance_records
        WHERE DATE(check_time) BETWEEN ? AND ?
        ORDER BY employee_id, check_time
    ''', (start_iso, end_iso)).fetchall()
    _times = {}
    for r in punch_rows:
        _times.setdefault((r['employee_id'], r['d']), []).append(str(r['check_time'])[11:16])
    span_map = {}   # (emp_id, ds) -> (span_hours, first, last-or-None, [times])
    for key, ts in _times.items():
        f_t = ts[0]
        l_t = ts[-1] if len(ts) >= 2 and ts[-1] > ts[0] else None
        h = 0.0
        if l_t:
            fh, fm = map(int, f_t.split(':'))
            lh, lm = map(int, l_t.split(':'))
            h = ((lh * 60 + lm) - (fh * 60 + fm)) / 60.0
        span_map[key] = (h, f_t, l_t, ts)

    # Approved leaves overlapping the month, with classification
    leave_rows = conn.execute('''
        SELECT lr.employee_id, lr.start_date, lr.end_date,
               COALESCE(lr.is_paid_leave, 0) AS is_paid,
               lt.name AS type_name,
               lr.leave_duration_type, lr.start_time, lr.end_time
        FROM leave_requests lr
        JOIN leave_types lt ON lt.id = lr.leave_type_id
        WHERE lr.status = 'approved'
          AND COALESCE(lr.is_deleted, 0) = 0
          AND NOT (DATE(lr.end_date) < ? OR DATE(lr.start_date) > ?)
    ''', (start_iso, end_iso)).fetchall()
    leaves_map = {}
    hperm_map = {}   # emp_id -> {date_iso: (hours, from, to)}
    for r in leave_rows:
        s, e = _parse_d(r['start_date']), _parse_d(r['end_date'])
        if not (s and e):
            continue
        if (r['leave_duration_type'] or '') == 'hourly':
            if r['start_time'] and r['end_time']:
                _h = max(0.0, (_t2m(r['end_time'], '00:00') - _t2m(r['start_time'], '00:00')) / 60.0)
                if _h > 0:
                    hperm_map.setdefault(r['employee_id'], {})[s.isoformat()] = \
                        (round(_h, 2), str(r['start_time'])[:5], str(r['end_time'])[:5])
            continue
        leaves_map.setdefault(r['employee_id'], []).append(
            (s, e, bool(r['is_paid']), _is_sick_type(r['type_name']), r['type_name']))

    # Excuses in the month
    exc_rows = conn.execute('''
        SELECT employee_id, date, side FROM attendance_excuses
        WHERE DATE(date) BETWEEN ? AND ?
    ''', (start_iso, end_iso)).fetchall()
    excuses_map = {}
    for r in exc_rows:
        excuses_map.setdefault(r['employee_id'], {})[str(r['date'])[:10]] = r['side']

    _period_days = []
    _d = p_start
    while _d <= p_end:
        _period_days.append(_d)
        _d += timedelta(days=1)

    out = {}
    for emp in employees:
        emp_id = emp['id']
        hpd = float(emp['hours_per_day'] or 8)
        off_days = _weekly_off_set(emp)
        emp_leaves = leaves_map.get(emp_id, [])
        emp_excuses = excuses_map.get(emp_id, {})
        hire_dt = _parse_d(emp['hire_date']) if 'hire_date' in emp.keys() else None
        eos_dt = (_parse_d(emp['end_of_service_date'])
                  if 'end_of_service_date' in emp.keys() else None)

        m = {f: 0 for f in METRIC_FIELDS}
        m['required_hours'] = 0.0
        m['actual_hours'] = 0.0
        _day_log = []
        ek = emp.keys()
        pres_ps = emp['presence_start_time'] if 'presence_start_time' in ek else None
        pres_pe = emp['presence_end_time'] if 'presence_end_time' in ek else None
        pres_on = bool(pres_ps and pres_pe)
        flex = _emp_flex_mode(emp)
        emp_hperm = hperm_map.get(emp_id, {})
        _sh_s = _t2m(emp['shift_start'] if 'shift_start' in ek else None,
                     (emp['default_start_time'] if 'default_start_time' in ek
                      and emp['default_start_time'] else '08:00'))
        _sh_e = _t2m(emp['shift_end'] if 'shift_end' in ek else None,
                     (emp['default_end_time'] if 'default_end_time' in ek
                      and emp['default_end_time'] else '16:00'))
        _gl = int(float(sal.get('grace_late_minutes', 10) or 10))
        _ge = int(float(sal.get('early_departure_grace_minutes',
                                sal.get('grace_early_minutes', 5)) or 5))
        _ot_step = int(float(sal.get('overtime_round_to_minutes', 15) or 0))

        for d in _period_days:
            ds = d.isoformat()
            rec = span_map.get((emp_id, ds))
            span = rec[0] if rec is not None else None

            if hire_dt and d < hire_dt:
                continue
            if eos_dt and d > eos_dt:
                continue
            _wk = (d - timedelta(days=(d.isoweekday() % 7))).isoformat()
            if (d.isoweekday() % 7) in off_days:
                m['weekly_off_days'] += 1
                _worked = span is not None and span > 0
                if _worked:
                    m['actual_days'] += 1
                    m['actual_hours'] += span
                    m['ot_weekend_mins'] += _ot_round(span * 60, _ot_step)
                _day_log.append((_wk, 'rest', _worked))
                continue
            if ds in holidays:
                m['holiday_days'] += 1
                _worked = span is not None and span > 0
                if _worked:
                    m['actual_days'] += 1
                    m['actual_hours'] += span
                    m['ot_holiday_mins'] += _ot_round(span * 60, _ot_step)
                _day_log.append((_wk, 'rest', _worked))
                continue

            leave_hit = next((lv for lv in emp_leaves if lv[0] <= d <= lv[1]), None)
            if leave_hit:
                _, _, is_paid, is_sick, _ = leave_hit
                _day_log.append((_wk, 'work', bool(is_paid or is_sick)))
                if is_sick:
                    m['sick_days'] += 1
                elif not is_paid:
                    m['unpaid_days'] += 1
                if span is not None and span > 0:
                    m['actual_days'] += 1
                    m['actual_hours'] += span
                continue  # not required either way

            punched = rec is not None
            exc_side = emp_excuses.get(ds, '__none__')
            excused = exc_side != '__none__'

            if excused and not punched:
                _day_log.append((_wk, 'work', True))
                # Whole-day excuse on an absent day -> not required at all
                m['excuse_days'] += 1
                continue
            # (side value used below for waivers)

            # A scheduled, required working day
            _day_log.append((_wk, 'work', punched))
            m['required_days'] += 1
            m['required_hours'] += hpd

            hp = emp_hperm.get(ds)

            if flex == 'any_time':
                # Any-Time contract: ANY punch = a full credited day.
                # No lateness, no early-leave, no missing-punch concept.
                if hp:
                    m['hourly_perm_hours'] += hp[0]
                if excused:
                    m['excuse_days'] += 1
                if punched:
                    m['actual_days'] += 1
                    m['actual_hours'] += hpd
                    if pres_on and not excused and _presence_missing(rec[3], pres_ps, pres_pe):
                        m['presence_missing'] += 1
                elif not excused:
                    m['absent_days'] += 1
                continue

            hp_touch_start = hp_touch_end = hp_cover_pres = False
            if hp:
                m['hourly_perm_hours'] += hp[0]
                m['required_hours'] -= min(hp[0], hpd)
                _pf, _pt = _t2m(hp[1], '00:00'), _t2m(hp[2], '00:00')
                hp_touch_start = _pf <= _sh_s + _gl
                hp_touch_end = _pt >= _sh_e - _ge
                if pres_on:
                    hp_cover_pres = not (_pt <= _t2m(pres_ps, '00:00')
                                         or _pf >= _t2m(pres_pe, '00:00'))
            if span is not None and span > 0:
                m['actual_days'] += 1
                m['actual_hours'] += span
                if flex == 'none' and rec[2]:
                    _ot_raw = _t2m(rec[2], '00:00') - _sh_e
                    if _ot_raw > 0:
                        m['ot_weekday_mins'] += _ot_round(_ot_raw, _ot_step)
                elif flex == 'fixed_hours' and span > hpd:
                    m['ot_weekday_mins'] += _ot_round((span - hpd) * 60, _ot_step)
            if punched and flex == 'none':
                lm, em = _charged_late_early(rec[1], rec[2], emp, sal)
                if exc_side == 'in' or hp_touch_start:
                    lm = 0   # entry excuse / start-side permission waives lateness
                if exc_side == 'out' or hp_touch_end:
                    em = 0   # exit excuse / end-side permission waives early leave
                m['late_mins'] += lm
                m['early_mins'] += em
            if punched and pres_on and not excused and not hp_cover_pres \
                    and _presence_missing(rec[3], pres_ps, pres_pe):
                # A documented excuse for the day waives its presence
                # violation as well (fairness: one justification per day).
                m['presence_missing'] += 1
            if excused:
                # Punch-side excuse: the day IS worked/required; the excuse
                # only waives the punch deficiency. Hours stay as recorded,
                # and the day COUNTS as an actual worked day.
                m['excuse_days'] += 1
                if span is not None and span <= 0:
                    m['actual_days'] += 1
            elif span is None:
                m['absent_days'] += 1
            elif span <= 0:
                m['partial_days'] += 1  # punched, but missing in/out

        m['required_hours'] = round(max(m['required_hours'], 0.0), 2)
        m['actual_hours'] = round(m['actual_hours'], 2)
        m['hourly_perm_hours'] = round(m['hourly_perm_hours'], 2)
        _credited_weeks = {w for (w, k, c) in _day_log if c}
        m['unentitled_rest_days'] = sum(
            1 for (w, k, c) in _day_log
            if k == 'rest' and not c and w not in _credited_weeks)
        out[emp_id] = m
    return out


def get_hours_approvals(conn, month, year):
    rows = conn.execute('''
        SELECT a.*, u.username AS approved_by_name
        FROM payroll_hours_approvals a
        LEFT JOIN users u ON u.id = a.approved_by
        WHERE a.month = ? AND a.year = ?
    ''', (int(month), int(year))).fetchall()
    return {r['employee_id']: dict(r) for r in rows}


def approval_state(appr_row, live_metrics, period_start_iso=None, period_end_iso=None):
    """Returns 'approved' | 'stale' | 'pending' for one employee.
    A signature is fresh only if BOTH the metric tuple and the attested
    period window match the current computation."""
    if not appr_row:
        return 'pending'
    keys = appr_row.keys()
    stored_ps = appr_row['period_start'] if 'period_start' in keys else None
    stored_pe = appr_row['period_end'] if 'period_end' in keys else None
    if period_start_iso and stored_ps and (
            str(stored_ps)[:10] != period_start_iso or str(stored_pe)[:10] != period_end_iso):
        return 'stale'
    stored = {
        'required_days': appr_row['required_days'], 'actual_days': appr_row['actual_days'],
        'required_hours': appr_row['required_hours'], 'actual_hours': appr_row['computed_hours'],
        'partial_days': appr_row['partial_days'], 'absent_days': appr_row['absent_days'],
        'sick_days': appr_row['sick_days'], 'unpaid_days': appr_row['unpaid_days'],
        'excuse_days': appr_row['excuse_days'],
        'weekly_off_days': appr_row['weekly_off_days'] if 'weekly_off_days' in keys else 0,
        'holiday_days': appr_row['holiday_days'] if 'holiday_days' in keys else 0,
        'late_mins': appr_row['late_mins'] if 'late_mins' in keys else 0,
        'early_mins': appr_row['early_mins'] if 'early_mins' in keys else 0,
        'presence_missing': appr_row['presence_missing'] if 'presence_missing' in keys else 0,
        'hourly_perm_hours': appr_row['hourly_perm_hours'] if 'hourly_perm_hours' in keys else 0,
        'ot_weekday_mins': appr_row['ot_weekday_mins'] if 'ot_weekday_mins' in keys else 0,
        'ot_weekend_mins': appr_row['ot_weekend_mins'] if 'ot_weekend_mins' in keys else 0,
        'ot_holiday_mins': appr_row['ot_holiday_mins'] if 'ot_holiday_mins' in keys else 0,
        'unentitled_rest_days': (appr_row['unentitled_rest_days']
                                 if 'unentitled_rest_days' in keys else 0),
    }
    return 'approved' if metrics_signature(stored) == metrics_signature(live_metrics) else 'stale'


def attest_hours(conn, month, year, items, user_id):
    """
    Attestation: snapshots SERVER-COMPUTED metrics for the given employees.
    `items` = [{'employee_id': int, 'notes': str?}, ...] — no numbers accepted.
    Returns count saved.
    """
    month = int(month)
    year = int(year)
    p_start, p_end = resolve_period(conn, month, year)
    metrics = compute_month_metrics(conn, month, year)
    saved = 0
    for it in items:
        emp_id = int(it['employee_id'])
        m = metrics.get(emp_id)
        if m is None:
            continue
        notes = (it.get('notes') or '').strip()
        conn.execute('''
            INSERT INTO payroll_hours_approvals
                (employee_id, month, year,
                 required_hours, computed_hours, approved_hours,
                 required_days, actual_days, partial_days, absent_days,
                 sick_days, unpaid_days, excuse_days,
                 weekly_off_days, holiday_days, late_mins, early_mins,
                 presence_missing, hourly_perm_hours,
                 ot_weekday_mins, ot_weekend_mins, ot_holiday_mins,
                 unentitled_rest_days,
                 period_start, period_end,
                 notes, approved_by, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(employee_id, month, year) DO UPDATE SET
                required_hours = excluded.required_hours,
                computed_hours = excluded.computed_hours,
                approved_hours = excluded.approved_hours,
                required_days = excluded.required_days,
                actual_days = excluded.actual_days,
                partial_days = excluded.partial_days,
                absent_days = excluded.absent_days,
                sick_days = excluded.sick_days,
                unpaid_days = excluded.unpaid_days,
                excuse_days = excluded.excuse_days,
                weekly_off_days = excluded.weekly_off_days,
                holiday_days = excluded.holiday_days,
                late_mins = excluded.late_mins,
                early_mins = excluded.early_mins,
                presence_missing = excluded.presence_missing,
                hourly_perm_hours = excluded.hourly_perm_hours,
                ot_weekday_mins = excluded.ot_weekday_mins,
                ot_weekend_mins = excluded.ot_weekend_mins,
                ot_holiday_mins = excluded.ot_holiday_mins,
                unentitled_rest_days = excluded.unentitled_rest_days,
                period_start = excluded.period_start,
                period_end = excluded.period_end,
                notes = excluded.notes,
                approved_by = excluded.approved_by,
                approved_at = CURRENT_TIMESTAMP
        ''', (emp_id, month, year,
              m['required_hours'], m['actual_hours'], m['actual_hours'],
              m['required_days'], m['actual_days'], m['partial_days'],
              m['absent_days'], m['sick_days'], m['unpaid_days'], m['excuse_days'],
              m['weekly_off_days'], m['holiday_days'],
              m['late_mins'], m['early_mins'], m['presence_missing'],
              round(m['hourly_perm_hours'], 2),
              m['ot_weekday_mins'], m['ot_weekend_mins'], m['ot_holiday_mins'],
              m['unentitled_rest_days'],
              p_start.isoformat(), p_end.isoformat(),
              notes, user_id))
        saved += 1
    return saved


def _sick_tiers(conn):
    """Kuwait sick-pay tiers for the sick leave type: [(start_day, end_day, pct)]."""
    rows = conn.execute("""
        SELECT t.start_day, t.end_day, t.payment_percentage, lt.name
        FROM leave_tiers t JOIN leave_types lt ON lt.id = t.leave_type_id
        ORDER BY t.start_day""").fetchall()
    return [(r['start_day'], r['end_day'], float(r['payment_percentage']))
            for r in rows if _is_sick_type(r['name'])]


def _tier_pct(tiers, position):
    for s, e, pct in tiers:
        if s <= position <= e:
            return pct
    return 0.0  # beyond the last tier -> unpaid


def _prior_sick_map(conn, employees, p_start, basis='calendar'):
    """Approved sick WORKING days per employee within the tier year window,
    up to (but excluding) the period start — the cumulative base for tiers.
    basis='calendar' -> Jan 1 of the period year.
    basis='hire'     -> each employee's latest hire anniversary <= p_start."""
    def _anniv(hire):
        h = _parse_d(hire)
        if not h:
            return date(p_start.year, 1, 1)
        try:
            a = h.replace(year=p_start.year)
        except ValueError:  # Feb 29
            a = date(p_start.year, 2, 28)
        return a if a <= p_start else a.replace(year=p_start.year - 1)

    ys_map = {}
    for e in employees:
        hire = e['hire_date'] if 'hire_date' in e.keys() else None
        ys_map[e['id']] = (_anniv(hire) if basis == 'hire'
                           else date(p_start.year, 1, 1))
    min_ys = min(ys_map.values())
    if p_start <= min_ys:
        return {}
    cutoff = (p_start - timedelta(days=1)).isoformat()
    hol = {r['date'][:10] for r in conn.execute(
        "SELECT date FROM official_holidays WHERE DATE(date) BETWEEN ? AND ?",
        (min_ys.isoformat(), cutoff)).fetchall()}
    rows = conn.execute("""
        SELECT lr.employee_id, lr.start_date, lr.end_date, lt.name AS type_name
        FROM leave_requests lr JOIN leave_types lt ON lt.id = lr.leave_type_id
        WHERE lr.status = 'approved'
          AND DATE(lr.start_date) <= ? AND DATE(lr.end_date) >= ?""",
        (cutoff, min_ys.isoformat())).fetchall()
    off_by_emp = {e['id']: _weekly_off_set(e) for e in employees}
    out = {}
    for r in rows:
        if not _is_sick_type(r['type_name']):
            continue
        emp_id = r['employee_id']
        off = off_by_emp.get(emp_id)
        if off is None:
            continue
        d, e = _parse_d(r['start_date']), _parse_d(r['end_date'])
        if not d or not e:
            continue
        d = max(d, ys_map.get(emp_id, min_ys))
        e = min(e, p_start - timedelta(days=1))
        while d <= e:
            if (d.isoweekday() % 7) not in off and d.isoformat() not in hol:
                out[emp_id] = out.get(emp_id, 0) + 1
            d += timedelta(days=1)
    return out


def compute_monthly_payroll(conn, month, year):
    """
    Returns {month, year, rows, totals, hours_approval:{approved, stale, total}}.
    Hours shown for an APPROVED employee come from the attested snapshot;
    stale/pending employees show live figures with the appropriate flag.
    Loan remaining EXCLUDES this month's payroll-source payments (idempotent).
    """
    month = int(month)
    year = int(year)

    p_start, p_end = resolve_period(conn, month, year)
    employees = fetch_payroll_employees(conn, period=(p_start, p_end))
    sal = get_salary_settings_v2(conn)
    _basis = str(sal.get('daily_rate_basis', '30') or '30').strip()
    if _basis not in ('30', '26', 'working'):
        _basis = '30'
    _holidays_set = {str(r['date'])[:10] for r in conn.execute(
        'SELECT date FROM official_holidays WHERE DATE(date) BETWEEN ? AND ?',
        (p_start.isoformat(), p_end.isoformat()))}
    _all_days = [p_start + timedelta(days=i)
                 for i in range((p_end - p_start).days + 1)]

    def _scheduled_days(emp_row, win_s=None, win_e=None):
        offs = _weekly_off_set(emp_row)
        n = 0
        for _d in _all_days:
            if win_s and _d < win_s:
                continue
            if win_e and _d > win_e:
                continue
            if (_d.isoweekday() % 7) in offs:
                continue
            if _d.isoformat() in _holidays_set:
                continue
            n += 1
        return n

    def _daily_rate_of(emp_row, basic_v):
        if basic_v <= MONEY_EPS:
            return 0.0
        if _basis == '26':
            return basic_v / 26.0
        if _basis == 'working':
            sched = _scheduled_days(emp_row)
            return (basic_v / sched) if sched else 0.0
        return basic_v / DAILY_RATE_DIVISOR

    try:
        _dec = int(float(sal.get('rounding_display_decimals', 2) or 2))
    except (TypeError, ValueError):
        _dec = 2
    _dec = min(max(_dec, 0), 3)

    def _rnd(v):
        return round(float(v or 0), _dec)
    start_iso, end_iso = p_start.isoformat(), p_end.isoformat()
    metrics = compute_month_metrics(conn, month, year, employees)
    approvals = get_hours_approvals(conn, month, year)

    # Date-scoped: a fixed item applies only to periods its [start_date,
    # end_date] window overlaps. NULL start_date = in effect since before
    # any recorded history (grandfathers pre-existing rows); NULL end_date
    # = still in effect. This is the same overlap rule used for employee
    # hire/end-of-service windows — a benefit has a service life too.
    fixed_rows = conn.execute('''
        SELECT s.employee_id, s.calc_method, s.amount, s.percent_value,
               it.code, it.name_ar, it.name_en, it.kind, it.sort_order
        FROM employee_salary_items s
        JOIN payroll_item_types it ON it.id = s.item_type_id
        WHERE s.is_active = 1 AND it.is_active = 1
          AND COALESCE(s.start_date, '0001-01-01') <= ?
          AND (s.end_date IS NULL OR s.end_date >= ?)
        ORDER BY it.sort_order
    ''', (end_iso, start_iso)).fetchall()
    fixed_map = {}
    for r in fixed_rows:
        fixed_map.setdefault(r['employee_id'], []).append(r)

    tx_rows = conn.execute('''
        SELECT t.employee_id, t.amount, t.reason,
               it.code, it.name_ar, it.name_en, it.kind, it.sort_order
        FROM payroll_transactions t
        JOIN payroll_item_types it ON it.id = t.item_type_id
        WHERE t.month = ? AND t.year = ? AND t.status = 'active'
        ORDER BY it.sort_order, t.id
    ''', (month, year)).fetchall()
    tx_map = {}
    for r in tx_rows:
        tx_map.setdefault(r['employee_id'], []).append(r)

    loan_rows = conn.execute('''
        SELECT l.id AS loan_id, l.employee_id, l.principal, l.monthly_installment,
               COALESCE(p.paid, 0) AS paid
        FROM employee_loans l
        LEFT JOIN (
            SELECT loan_id, SUM(amount) AS paid
            FROM loan_payments
            WHERE NOT (source = 'payroll' AND month = ? AND year = ?)
            GROUP BY loan_id
        ) p ON p.loan_id = l.id
        WHERE l.status IN ('active', 'settled')
          AND (l.start_year < ? OR (l.start_year = ? AND l.start_month <= ?))
    ''', (month, year, year, year, month)).fetchall()
    loans_map = {}
    for r in loan_rows:
        remaining = (r['principal'] or 0) - (r['paid'] or 0)
        if remaining > MONEY_EPS:
            inst = min(r['monthly_installment'] or 0, remaining)
            if inst > MONEY_EPS:
                loans_map.setdefault(r['employee_id'], []).append(
                    {'loan_id': r['loan_id'], 'amount': round(inst, 3)})

    sick_tiers = _sick_tiers(conn)
    prior_sick = _prior_sick_map(conn, employees, p_start,
                                 sal.get('sick_tier_year_basis', 'calendar'))

    hpd_of = {e['id']: float(e['hours_per_day'] or 8) for e in employees}
    rows = []
    totals = {'basic': 0.0, 'allowances': 0.0, 'deductions': 0.0,
              'net': 0.0, 'required_hours': 0.0, 'actual_hours': 0.0}
    n_approved = n_stale = 0

    for emp in employees:
        emp_id = emp['id']
        basic = float(emp['salary'] or 0)
        live = metrics.get(emp_id, {f: 0 for f in METRIC_FIELDS})
        appr = approvals.get(emp_id)
        state = approval_state(appr, live, start_iso, end_iso)
        if state == 'approved':
            n_approved += 1
            required_hours = round(float(appr['required_hours'] or 0), 2)
            actual_hours = round(float(appr['computed_hours'] or 0), 2)
        else:
            if state == 'stale':
                n_stale += 1
            required_hours = live['required_hours']
            actual_hours = live['actual_hours']

        allowances, deductions = [], []
        for it in fixed_map.get(emp_id, []):
            if it['calc_method'] == 'percent_of_basic':
                amount = round(basic * float(it['percent_value'] or 0) / 100.0, 3)
            else:
                amount = round(float(it['amount'] or 0), 3)
            if amount <= MONEY_EPS:
                continue
            entry = {'code': it['code'], 'name_ar': it['name_ar'],
                     'name_en': it['name_en'], 'amount': amount, 'source': 'fixed'}
            (allowances if it['kind'] == 'earning' else deductions).append(entry)

        for t in tx_map.get(emp_id, []):
            entry = {'code': t['code'], 'name_ar': t['name_ar'],
                     'name_en': t['name_en'],
                     'amount': round(float(t['amount'] or 0), 3), 'source': 'tx'}
            (allowances if t['kind'] == 'earning' else deductions).append(entry)

        # --- Attendance-based deductions (from the attested figures) ---
        if state == 'approved':
            ak = appr.keys()
            att = {'absent_days': appr['absent_days'] or 0,
                   'unpaid_days': appr['unpaid_days'] or 0,
                   'sick_days': appr['sick_days'] or 0,
                   'partial_days': appr['partial_days'] or 0,
                   'late_mins': (appr['late_mins'] if 'late_mins' in ak else 0) or 0,
                   'early_mins': (appr['early_mins'] if 'early_mins' in ak else 0) or 0,
                   'presence_missing': (appr['presence_missing'] if 'presence_missing' in ak else 0) or 0}
        else:
            att = {'absent_days': live.get('absent_days', 0),
                   'unpaid_days': live.get('unpaid_days', 0),
                   'sick_days': live.get('sick_days', 0),
                   'partial_days': live.get('partial_days', 0),
                   'late_mins': live.get('late_mins', 0),
                   'early_mins': live.get('early_mins', 0),
                   'presence_missing': live.get('presence_missing', 0)}
        daily_rate = _daily_rate_of(emp, basic)
        if daily_rate > MONEY_EPS:
            if att['absent_days'] > 0:
                deductions.append({'code': 'absence_deduction',
                                   'name_ar': f"خصم غياب ({att['absent_days']} يوم)",
                                   'name_en': f"Absence Deduction ({att['absent_days']} d)",
                                   'amount': round(att['absent_days'] * daily_rate, 3),
                                   'source': 'computed'})
            if att['unpaid_days'] > 0:
                deductions.append({'code': 'unpaid_leave_deduction',
                                   'name_ar': f"خصم إجازة بدون راتب ({att['unpaid_days']} يوم)",
                                   'name_en': f"Unpaid Leave ({att['unpaid_days']} d)",
                                   'amount': round(att['unpaid_days'] * daily_rate, 3),
                                   'source': 'computed'})
            hourly_rate = round(daily_rate / float(emp['hours_per_day'] or 8), 4)
            if att['late_mins'] > 0:
                deductions.append({'code': 'lateness_deduction',
                                   'name_ar': f"خصم تأخير ({att['late_mins']} د)",
                                   'name_en': f"Lateness ({att['late_mins']} min)",
                                   'amount': round(att['late_mins'] / 60.0 * hourly_rate, 3),
                                   'source': 'computed'})
            if att['early_mins'] > 0:
                deductions.append({'code': 'early_leave_deduction',
                                   'name_ar': f"خصم انصراف مبكر ({att['early_mins']} د)",
                                   'name_en': f"Early Leave ({att['early_mins']} min)",
                                   'amount': round(att['early_mins'] / 60.0 * hourly_rate, 3),
                                   'source': 'computed'})
            if att['partial_days'] > 0:
                mp = sal.get('missing_punch_policy', 'invalid')
                n = int(att['partial_days'])
                if mp == 'penalty_tiered':
                    p1 = float(sal.get('missing_punch_penalty_1', 0) or 0)
                    p2 = float(sal.get('missing_punch_penalty_2', 0.25) or 0)
                    p3 = float(sal.get('missing_punch_penalty_3', 1.0) or 0)
                    charged_days = (p1 if n >= 1 else 0) + (p2 if n >= 2 else 0) \
                                   + (p3 * max(0, n - 2))
                else:  # 'invalid' or 'zero_hours': the day is not a valid workday
                    charged_days = float(n)
                mp_amount = round(charged_days * daily_rate, 3)
                if mp_amount > MONEY_EPS:
                    deductions.append({'code': 'missing_punch_penalty',
                                       'name_ar': f"عقوبة بصمة ناقصة ({n} يوم)",
                                       'name_en': f"Missing Punch Penalty ({n} d)",
                                       'amount': mp_amount, 'source': 'computed'})
            pmp = sal.get('presence_missing_policy', 'warning')
            if att['presence_missing'] > 0 and pmp != 'warning':
                n = int(att['presence_missing'])
                if pmp == 'penalty_tiered':
                    q1 = float(sal.get('presence_penalty_1', 0) or 0)
                    q2 = float(sal.get('presence_penalty_2', 0.25) or 0)
                    q3 = float(sal.get('presence_penalty_3', 0.5) or 0)
                    charged_days = (q1 if n >= 1 else 0) + (q2 if n >= 2 else 0) \
                                   + (q3 * max(0, n - 2))
                else:  # deduct_fraction
                    frac = float(sal.get('presence_penalty_day_fraction', 0.25) or 0)
                    charged_days = frac * n
                cap = float(sal.get('presence_penalty_monthly_cap_days', 5) or 0)
                if cap > 0:
                    charged_days = min(charged_days, cap)
                pp_amount = round(charged_days * daily_rate, 3)
                if pp_amount > MONEY_EPS:
                    deductions.append({'code': 'presence_penalty',
                                       'name_ar': f"جزاء بصمة تواجد ({n} يوم)",
                                       'name_en': f"Presence Penalty ({n} d)",
                                       'amount': pp_amount, 'source': 'computed'})
            ot_src = appr if state == 'approved' else live
            akx = ot_src.keys() if hasattr(ot_src, 'keys') else ot_src
            def _g(k):
                try:
                    return int((ot_src[k] if k in akx else 0) or 0)
                except Exception:
                    return 0
            wk_h = _g('ot_weekday_mins') / 60.0
            we_h = _g('ot_weekend_mins') / 60.0
            ho_h = _g('ot_holiday_mins') / 60.0
            ot_total = wk_h + we_h + ho_h
            if ot_total > MONEY_EPS and daily_rate > MONEY_EPS:
                cap = float(sal.get('overtime_cap_monthly_hours', 0) or 0)
                if cap > 0 and ot_total > cap:
                    cut = ot_total - cap
                    # trim cheapest multiplier first: weekday -> weekend -> holiday
                    t = min(cut, wk_h); wk_h -= t; cut -= t
                    t = min(cut, we_h); we_h -= t; cut -= t
                    t = min(cut, ho_h); ho_h -= t; cut -= t
                    ot_total = wk_h + we_h + ho_h
                hourly_rate = daily_rate / hpd_of.get(emp_id, 8.0)
                m_wk = float(sal.get('weekday_ot_multiplier', 1.25) or 0)
                m_we = float(sal.get('weekend_ot_multiplier', 1.5) or 0)
                m_ho = float(sal.get('holiday_ot_multiplier', 2.0) or 0)
                ot_amount = round((wk_h * m_wk + we_h * m_we + ho_h * m_ho)
                                  * hourly_rate, 3)
                if ot_amount > MONEY_EPS:
                    allowances.append({'code': 'overtime',
                                       'name_ar': f"العمل الإضافي ({round(ot_total, 2)} س)",
                                       'name_en': f"Overtime ({round(ot_total, 2)} h)",
                                       'amount': ot_amount, 'source': 'computed'})
            if att['sick_days'] > 0 and sick_tiers:
                prior = prior_sick.get(emp_id, 0)
                unpaid_fraction = 0.0
                for i in range(1, int(att['sick_days']) + 1):
                    unpaid_fraction += (1.0 - _tier_pct(sick_tiers, prior + i))
                sick_ded = round(unpaid_fraction * daily_rate, 3)
                if sick_ded > MONEY_EPS:
                    deductions.append({'code': 'sick_tier_deduction',
                                       'name_ar': f"خصم شرائح الإجازة المرضية ({att['sick_days']} يوم)",
                                       'name_en': f"Sick Leave Tiers ({att['sick_days']} d)",
                                       'amount': sick_ded, 'source': 'computed'})

        _rest_src = appr if state == 'approved' else live
        _rk = _rest_src.keys() if hasattr(_rest_src, 'keys') else _rest_src
        _rest_n = int((_rest_src['unentitled_rest_days']
                       if 'unentitled_rest_days' in _rk else 0) or 0)
        if _basis != '30':
            _rest_n = 0   # rest days carry no price outside the calendar basis
        if _rest_n > 0 and daily_rate > MONEY_EPS:
            deductions.append({'code': 'rest_days_unentitled',
                               'name_ar': f'خصم أيام راحة غير مستحقة ({_rest_n} يوم)',
                               'name_en': f'Unearned rest days ({_rest_n} d)',
                               'amount': round(_rest_n * daily_rate, 3),
                               'source': 'computed'})

        if daily_rate > MONEY_EPS:
            _h = _parse_d(emp['hire_date']) if 'hire_date' in emp.keys() else None
            _e = (_parse_d(emp['end_of_service_date'])
                  if 'end_of_service_date' in emp.keys() else None)
            if _basis == '30':
                pre_days = (_h - p_start).days if _h and _h > p_start else 0
                post_days = (p_end - _e).days if _e and _e < p_end else 0
            else:
                pre_days = (_scheduled_days(emp, win_e=_h - timedelta(days=1))
                            if _h and _h > p_start else 0)
                post_days = (_scheduled_days(emp, win_s=_e + timedelta(days=1))
                             if _e and _e < p_end else 0)
            if pre_days > 0:
                deductions.append({'code': 'pre_hire_days',
                                   'name_ar': f'خصم ما قبل التعيين ({pre_days} يوم)',
                                   'name_en': f'Pre-hire days ({pre_days} d)',
                                   'amount': round(pre_days * daily_rate, 3),
                                   'source': 'computed'})
            if post_days > 0:
                deductions.append({'code': 'post_service_days',
                                   'name_ar': f'خصم ما بعد انتهاء الخدمة ({post_days} يوم)',
                                   'name_en': f'Post-service days ({post_days} d)',
                                   'amount': round(post_days * daily_rate, 3),
                                   'source': 'computed'})

        for ln in loans_map.get(emp_id, []):
            deductions.append({'code': 'loan_installment',
                               'name_ar': 'قسط سلفة', 'name_en': 'Loan Installment',
                               'amount': ln['amount'], 'source': 'loan',
                               'loan_id': ln['loan_id']})

        for _it in allowances:
            _it['amount'] = _rnd(_it['amount'])
        for _it in deductions:
            _it['amount'] = _rnd(_it['amount'])
        basic = _rnd(basic)
        total_allow = _rnd(sum(a['amount'] for a in allowances))
        total_ded = _rnd(sum(d['amount'] for d in deductions))
        net = _rnd(basic + total_allow - total_ded)

        rows.append({'employee_id': emp_id,
                     'employee_number': emp['employee_number'],
                     'name': emp['name'], 'arabic_name': emp['arabic_name'],
                     'required_hours': required_hours,
                     'actual_hours': actual_hours,
                     'hours_diff': round(actual_hours - required_hours, 2),
                     'hours_state': state,
                     'hours_approved': state == 'approved',
                     'basic': basic,
                     'allowances': allowances, 'deductions': deductions,
                     'total_allowances': total_allow,
                     'total_deductions': total_ded, 'net': net})

        totals['basic'] += basic
        totals['allowances'] += total_allow
        totals['deductions'] += total_ded
        totals['net'] += net
        totals['required_hours'] += required_hours
        totals['actual_hours'] += actual_hours

    for k in totals:
        if k in ('basic', 'allowances', 'deductions', 'net'):
            totals[k] = _rnd(totals[k])
        else:
            totals[k] = round(totals[k], 2)

    return {'month': month, 'year': year, 'rows': rows, 'totals': totals,
            'period': {'start': start_iso, 'end': end_iso},
            'hours_approval': {'approved': n_approved, 'stale': n_stale,
                               'total': len(rows)}}


class RunLockedError(Exception):
    """Raised when modifying a finally-approved (locked) payroll run."""
    def __init__(self, run):
        self.run = dict(run)
        super().__init__('payroll run is locked')


class HoursNotApprovedError(Exception):
    """Raised when saving payroll with missing or stale hour attestations."""
    def __init__(self, missing):
        self.missing = missing  # [{'employee_id','employee_number','name','arabic_name','reason'}]
        super().__init__(f"{len(missing)} employees without fresh approved hours")


def save_monthly_payroll(conn, month, year, user_id):
    """
    Snapshots payroll into payroll_runs / payroll_run_lines and records loan
    installments (source='payroll'). Idempotent per month. GATED: every active
    employee must hold a FRESH hours attestation.
    """
    month = int(month)
    year = int(year)
    cur = conn.cursor()

    existing = cur.execute('SELECT * FROM payroll_runs WHERE month = ? AND year = ?',
                           (month, year)).fetchone()
    if existing and existing['status'] == 'approved':
        raise RunLockedError(existing)

    data_check = compute_monthly_payroll(conn, month, year)
    missing = [{'employee_id': r['employee_id'],
                'employee_number': r['employee_number'],
                'name': r['name'], 'arabic_name': r['arabic_name'],
                'reason': r['hours_state']}
               for r in data_check['rows'] if r['hours_state'] != 'approved']
    if missing:
        raise HoursNotApprovedError(missing)

    if existing:
        cur.execute('DELETE FROM payroll_run_lines WHERE run_id = ?', (existing['id'],))
    cur.execute("DELETE FROM loan_payments WHERE source = 'payroll' AND month = ? AND year = ?",
                (month, year))
    cur.execute('''UPDATE employee_loans SET status = 'active'
                   WHERE status = 'settled'
                     AND principal > COALESCE((SELECT SUM(amount) FROM loan_payments
                                               WHERE loan_id = employee_loans.id), 0) + ?''',
                (MONEY_EPS,))

    data = data_check
    t = data['totals']

    if existing:
        # Re-save keeps the SAME run row so the unlock audit trail
        # (unlocked_by / unlocked_at / unlock_reason) survives; the final
        # approval is cleared because this is a new, unapproved version.
        cur.execute('''UPDATE payroll_runs
                       SET status = 'saved', employees_count = ?, total_basic = ?,
                           total_allowances = ?, total_deductions = ?, total_net = ?,
                           period_start = ?, period_end = ?, created_by = ?,
                           created_at = CURRENT_TIMESTAMP,
                           approved_by = NULL, approved_at = NULL
                       WHERE id = ?''',
                    (len(data['rows']), t['basic'], t['allowances'],
                     t['deductions'], t['net'],
                     data['period']['start'], data['period']['end'], user_id,
                     existing['id']))
        run_id = existing['id']
    else:
        cur.execute('''INSERT INTO payroll_runs
                       (month, year, status, employees_count, total_basic,
                        total_allowances, total_deductions, total_net,
                        period_start, period_end, created_by)
                       VALUES (?, ?, 'saved', ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (month, year, len(data['rows']), t['basic'], t['allowances'],
                     t['deductions'], t['net'],
                     data['period']['start'], data['period']['end'], user_id))
        run_id = cur.lastrowid

    for row in data['rows']:
        cur.execute('''INSERT INTO payroll_run_lines
                       (run_id, employee_id, basic, required_hours, actual_hours,
                        total_allowances, total_deductions, net, details_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (run_id, row['employee_id'], row['basic'],
                     row['required_hours'], row['actual_hours'],
                     row['total_allowances'], row['total_deductions'], row['net'],
                     json.dumps({'allowances': row['allowances'],
                                 'deductions': row['deductions']},
                                ensure_ascii=False)))
        for d in row['deductions']:
            if d.get('source') == 'loan':
                cur.execute('''INSERT INTO loan_payments
                               (loan_id, month, year, amount, source, created_by)
                               VALUES (?, ?, ?, ?, 'payroll', ?)''',
                            (d['loan_id'], month, year, d['amount'], user_id))

    cur.execute('''UPDATE employee_loans SET status = 'settled'
                   WHERE status = 'active'
                     AND principal <= COALESCE((SELECT SUM(amount) FROM loan_payments
                                                WHERE loan_id = employee_loans.id), 0) + ?''',
                (MONEY_EPS,))

    return {'run_id': run_id, 'employees_count': len(data['rows']), 'totals': t}


def is_period_locked(conn, month, year):
    """CLOSED = the payroll run of that labeled month is finally approved."""
    r = conn.execute('SELECT status FROM payroll_runs WHERE month = ? AND year = ?',
                     (int(month), int(year))).fetchone()
    return bool(r and r['status'] == 'approved')


def date_period_locked(conn, d):
    """True when calendar date `d` falls inside a CLOSED payroll period."""
    if isinstance(d, str):
        d = _parse_d(d)
    if not d:
        return False
    probe = d.replace(day=1)
    for cand in ((probe.month, probe.year),
                 (1 if probe.month == 12 else probe.month + 1,
                  probe.year + 1 if probe.month == 12 else probe.year)):
        ps, pe = resolve_period(conn, cand[0], cand[1])
        if ps <= d <= pe:
            return is_period_locked(conn, cand[0], cand[1])
    return False


def _sha(payload):
    import hashlib
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _canon(obj):
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def post_to_ledger(conn, month, year, user_id, note=None):
    """Immutable ledger posting of a finally-approved run; globally hash-chained
    so any later edit to any stored byte breaks verification."""
    run = conn.execute('SELECT * FROM payroll_runs WHERE month = ? AND year = ?',
                       (int(month), int(year))).fetchone()
    if not run or run['status'] != 'approved':
        raise ValueError('post_requires_locked_run')
    lines = conn.execute(
        """SELECT l.*, e.employee_number, e.name, e.arabic_name
           FROM payroll_run_lines l
           JOIN employees e ON e.id = l.employee_id
           WHERE l.run_id = ?
           ORDER BY CAST(e.employee_number AS INTEGER), e.employee_number""",
        (run['id'],)).fetchall()
    prev = conn.execute('SELECT chain_hash FROM payroll_ledger_postings '
                        'ORDER BY id DESC LIMIT 1').fetchone()
    prev_hash = prev['chain_hash'] if prev else ''
    line_rows, line_hashes = [], []
    for l in lines:
        payload = _canon({'emp': l['employee_id'], 'no': l['employee_number'],
                          'basic': l['basic'], 'allow': l['total_allowances'],
                          'ded': l['total_deductions'], 'net': l['net'],
                          'details': l['details_json']})
        h = _sha(payload)
        line_hashes.append(h)
        line_rows.append((l['employee_id'], l['employee_number'], l['name'],
                          l['arabic_name'], l['basic'], l['total_allowances'],
                          l['total_deductions'], l['net'], l['details_json'], h))
    entry = _canon({'m': int(month), 'y': int(year),
                    'ps': run['period_start'], 'pe': run['period_end'],
                    'n': len(lines), 'basic': run['total_basic'],
                    'allow': run['total_allowances'], 'ded': run['total_deductions'],
                    'net': run['total_net'], 'lines': line_hashes,
                    'note': note or ''})
    entry_hash = _sha(entry)
    chain_hash = _sha(prev_hash + entry_hash)
    cur = conn.execute(
        """INSERT INTO payroll_ledger_postings
           (month, year, period_start, period_end, run_id, note, employees_count,
            total_basic, total_allowances, total_deductions, total_net,
            entry_hash, prev_hash, chain_hash, posted_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(month), int(year), run['period_start'], run['period_end'], run['id'],
         note, len(lines), run['total_basic'], run['total_allowances'],
         run['total_deductions'], run['total_net'],
         entry_hash, prev_hash, chain_hash, user_id))
    pid = cur.lastrowid
    for r in line_rows:
        conn.execute(
            """INSERT INTO payroll_ledger_lines
               (posting_id, employee_id, employee_number, name, arabic_name,
                basic, total_allowances, total_deductions, net, details_json, line_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (pid,) + r)
    conn.commit()
    return pid


def verify_ledger(conn):
    """Recompute every hash and the whole chain; (ok, first_broken_id)."""
    prev = ''
    for p in conn.execute('SELECT * FROM payroll_ledger_postings ORDER BY id'):
        hashes = []
        for l in conn.execute('SELECT * FROM payroll_ledger_lines '
                              'WHERE posting_id = ? ORDER BY id', (p['id'],)):
            payload = _canon({'emp': l['employee_id'], 'no': l['employee_number'],
                              'basic': l['basic'], 'allow': l['total_allowances'],
                              'ded': l['total_deductions'], 'net': l['net'],
                              'details': l['details_json']})
            h = _sha(payload)
            if h != l['line_hash']:
                return False, p['id']
            hashes.append(h)
        entry = _canon({'m': p['month'], 'y': p['year'],
                        'ps': p['period_start'], 'pe': p['period_end'],
                        'n': p['employees_count'], 'basic': p['total_basic'],
                        'allow': p['total_allowances'], 'ded': p['total_deductions'],
                        'net': p['total_net'], 'lines': hashes,
                        'note': p['note'] or ''})
        if _sha(entry) != p['entry_hash']:
            return False, p['id']
        if _sha(prev + p['entry_hash']) != p['chain_hash'] or (p['prev_hash'] or '') != prev:
            return False, p['id']
        prev = p['chain_hash']
    return True, None

def lock_run(conn, month, year, user_id):
    """Final approval: locks the saved run against any re-save."""
    run = conn.execute('SELECT * FROM payroll_runs WHERE month = ? AND year = ?',
                       (int(month), int(year))).fetchone()
    if not run:
        raise ValueError('run_not_found')
    if run['status'] == 'approved':
        raise RunLockedError(run)
    conn.execute("""UPDATE payroll_runs
                    SET status = 'approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP
                    WHERE id = ?""", (user_id, run['id']))
    return run['id']


def unlock_run(conn, month, year, user_id, reason):
    """Documented un-approval: reason is mandatory and recorded."""
    reason = (reason or '').strip()
    if len(reason) < 3:
        raise ValueError('reason_required')
    run = conn.execute('SELECT * FROM payroll_runs WHERE month = ? AND year = ?',
                       (int(month), int(year))).fetchone()
    if not run:
        raise ValueError('run_not_found')
    if run['status'] != 'approved':
        raise ValueError('not_locked')
    conn.execute("""UPDATE payroll_runs
                    SET status = 'saved', unlocked_by = ?, unlocked_at = CURRENT_TIMESTAMP,
                        unlock_reason = ?
                    WHERE id = ?""", (user_id, reason, run['id']))
    return run['id']


# Fields worth an audit trail, grouped so the employee file can show
# "what changed about pay" separately from "what changed about their file".
AUDIT_FIELDS = {
    'salary': 'pay', 'department': 'org', 'position': 'org',
    'manager_id': 'org', 'shift_type': 'work', 'employment_status': 'work',
    'contract_type': 'work', 'contract_start_date': 'work',
    'contract_end_date': 'work', 'grade_level': 'work',
    'branch_location': 'work', 'is_active': 'status',
    'end_of_service_date': 'status', 'hire_date': 'status',
    'previous_leave_balance': 'leave', 'weekly_leave_selected_days': 'work',
    'bank_iban': 'bank', 'bank_account_number': 'bank', 'bank_name': 'bank',
    'name': 'identity', 'arabic_name': 'identity', 'employee_number': 'identity',
    'national_id': 'identity', 'phone': 'contact', 'email': 'contact',
}


def log_employee_changes(conn, employee_id, before, after, user_id=None,
                         source='edit', note=None):
    """Record field-level changes for one employee.

    `before` and `after` are dict-like snapshots. Only fields listed in
    AUDIT_FIELDS are tracked, and only when the value actually changed —
    an audit log full of no-op rows is worse than none, because it buries
    the real events.
    """
    def norm(v):
        if v is None:
            return ''
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v).strip()

    rows = []
    for field, category in AUDIT_FIELDS.items():
        try:
            old_v = before[field] if field in before.keys() else None
        except (TypeError, AttributeError):
            old_v = (before or {}).get(field)
        try:
            new_v = after[field] if field in after.keys() else None
        except (TypeError, AttributeError):
            new_v = (after or {}).get(field)
        if norm(old_v) == norm(new_v):
            continue
        rows.append((employee_id, field, norm(old_v), norm(new_v),
                     category, source, note, user_id))
    if rows:
        conn.executemany(
            """INSERT INTO employee_audit_log
               (employee_id, field, old_value, new_value, category,
                source, note, changed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    return len(rows)


def log_employee_event(conn, employee_id, field, old_value, new_value,
                       category, user_id=None, source='system', note=None):
    """Record a single non-column event (allowance added, loan issued, ...)."""
    conn.execute(
        """INSERT INTO employee_audit_log
           (employee_id, field, old_value, new_value, category,
            source, note, changed_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (employee_id, field, old_value, new_value, category, source,
         note, user_id))


def fetch_fixed_earnings(conn, employee_id, basic, as_of=None):
    """Fixed recurring earning items in effect for one employee AS OF a
    date (default: today). Single source reused by EOS wage calc (as of
    the termination date), the employee profile (as of today), and
    anywhere else a 'qualifying wage' breakdown is needed."""
    if as_of is None:
        from datetime import date as _date
        as_of = _date.today().isoformat()
    elif hasattr(as_of, 'isoformat'):
        as_of = as_of.isoformat()
    else:
        as_of = str(as_of)[:10]
    out = []
    for it in conn.execute("""
            SELECT esi.calc_method, esi.amount, esi.percent_value,
                   it.code, it.name_ar, it.name_en
            FROM employee_salary_items esi
            JOIN payroll_item_types it ON it.id = esi.item_type_id
            WHERE esi.employee_id = ? AND COALESCE(esi.is_active, 1) = 1
              AND it.kind = 'earning' AND it.species = 'fixed'
              AND COALESCE(esi.start_date, '0001-01-01') <= ?
              AND (esi.end_date IS NULL OR esi.end_date >= ?)""",
            (employee_id, as_of, as_of)).fetchall():
        amt = (basic * float(it['percent_value'] or 0) / 100.0
               if it['calc_method'] == 'percent_of_basic'
               else float(it['amount'] or 0))
        out.append({'code': it['code'], 'name_ar': it['name_ar'],
                    'name_en': it['name_en'], 'amount': round(amt, 3)})
    return out


def fetch_employee_loans_detail(conn, employee_id):
    """Full loan list with installments and payment history — the one
    query both the financial-history screen and the employee profile use,
    so 'what they owe' never drifts between the two."""
    loans = []
    for ln in conn.execute("""SELECT * FROM employee_loans
                             WHERE employee_id = ? ORDER BY id""",
                          (employee_id,)).fetchall():
        pays = [dict(p) for p in conn.execute(
            """SELECT month, year, amount, source, created_at
               FROM loan_payments WHERE loan_id = ?
               ORDER BY year, month""", (ln['id'],)).fetchall()]
        paid = round(sum(p['amount'] or 0 for p in pays), 3)
        loans.append({'id': ln['id'], 'principal': ln['principal'],
                      'installment': ln['monthly_installment'],
                      'start': f"{ln['start_month']}/{ln['start_year']}",
                      'reason': ln['reason'], 'status': ln['status'],
                      'paid': paid,
                      'remaining': round((ln['principal'] or 0) - paid, 3),
                      'payments': pays})
    return loans


def compute_employee_days(conn, employee_id, month, year):
    """
    Day-by-day breakdown for ONE employee across the payroll period.
    Single source used by the drill-down API and the print report.
    Returns {'employee': {...}, 'period': {...}, 'days': [...]} or None.
    """
    rows = fetch_payroll_employees(conn, employee_id=employee_id, active_only=False)
    emp = rows[0] if rows else None
    if not emp:
        return None
    sal = get_salary_settings_v2(conn)
    pres_ps = emp['presence_start_time']
    pres_pe = emp['presence_end_time']
    pres_on = bool(pres_ps and pres_pe)
    flex = _emp_flex_mode(emp)

    p_start, p_end = resolve_period(conn, month, year)
    start_iso, end_iso = p_start.isoformat(), p_end.isoformat()
    hire_dt = _parse_d(emp['hire_date'])
    eos_dt = (_parse_d(emp['end_of_service_date'])
              if 'end_of_service_date' in emp.keys() else None)
    off_days = _weekly_off_set(emp)

    holidays = {r['date'][:10]: r['name'] for r in conn.execute(
        "SELECT date, name FROM official_holidays WHERE DATE(date) BETWEEN ? AND ?",
        (start_iso, end_iso)).fetchall()}

    leaves = []
    hperms = {}
    for r in conn.execute('''
        SELECT lr.start_date, lr.end_date, COALESCE(lr.is_paid_leave,0) AS is_paid,
               lr.reason AS leave_reason, lt.name AS type_name,
               lr.leave_duration_type, lr.start_time, lr.end_time
        FROM leave_requests lr JOIN leave_types lt ON lt.id = lr.leave_type_id
        WHERE lr.employee_id = ? AND lr.status = 'approved'
          AND COALESCE(lr.is_deleted, 0) = 0
          AND NOT (DATE(lr.end_date) < ? OR DATE(lr.start_date) > ?)''',
        (emp['id'], start_iso, end_iso)).fetchall():
        s, e = _parse_d(r['start_date']), _parse_d(r['end_date'])
        if not (s and e):
            continue
        if (r['leave_duration_type'] or '') == 'hourly':
            if r['start_time'] and r['end_time']:
                _h = max(0.0, (_t2m(r['end_time'], '00:00') - _t2m(r['start_time'], '00:00')) / 60.0)
                if _h > 0:
                    hperms[s.isoformat()] = {'hours': round(_h, 2),
                                             'from': str(r['start_time'])[:5],
                                             'to': str(r['end_time'])[:5],
                                             'reason': r['leave_reason']}
            continue
        leaves.append((s, e, bool(r['is_paid']), r['type_name'], r['leave_reason']))

    excuses = {str(r['date'])[:10]: {'reason': r['reason'], 'type': r['type'],
                                     'side': r['side'], 'by': r['by_name']}
               for r in conn.execute('''SELECT ae.date, ae.reason, ae.type, ae.side,
                                               u.username AS by_name
                                        FROM attendance_excuses ae
                                        LEFT JOIN users u ON u.id = ae.created_by
                                        WHERE ae.employee_id = ? AND DATE(ae.date) BETWEEN ? AND ?''',
                                     (emp['id'], start_iso, end_iso)).fetchall()}

    punches = {}
    for r in conn.execute('''
        SELECT DATE(check_time) AS d, check_time, check_type,
               COALESCE(source,'device') AS source
        FROM attendance_records
        WHERE employee_id = ? AND DATE(check_time) BETWEEN ? AND ?
        ORDER BY check_time''', (emp['id'], start_iso, end_iso)).fetchall():
        punches.setdefault(r['d'], []).append(dict(r))

    days = []
    d = p_start
    while d <= p_end:
        ds = d.isoformat()
        recs = punches.get(ds, [])
        times = [p['check_time'][11:16] for p in recs]
        first_t = times[0] if times else None
        last_t = times[-1] if len(times) >= 2 else None
        span_h = 0.0
        if first_t and last_t and last_t > first_t:
            fh, fm = map(int, first_t.split(':'))
            lh, lm = map(int, last_t.split(':'))
            span_h = round(((lh * 60 + lm) - (fh * 60 + fm)) / 60.0, 2)
        has_manual = any(p['source'] == 'manual' for p in recs)

        leave_hit = next((lv for lv in leaves if lv[0] <= d <= lv[1]), None)
        exc = excuses.get(ds)

        hp = hperms.get(ds)
        if eos_dt and d > eos_dt:
            recs = []
            first_t = last_t = None
            span_h = 0.0
        d_ot = 0
        d_ot_kind = None
        d_late = d_early = 0
        if recs and flex == 'none':
            d_late, d_early = _charged_late_early(first_t, last_t, emp, sal)
            if exc and exc.get('side') == 'in':
                d_late = 0
            if exc and exc.get('side') == 'out':
                d_early = 0
            if hp:
                _pf, _pt = _t2m(hp['from'], '00:00'), _t2m(hp['to'], '00:00')
                _shs = _t2m(emp['shift_start'], (emp['default_start_time'] or '08:00'))
                _she = _t2m(emp['shift_end'], (emp['default_end_time'] or '16:00'))
                _gl = int(float(sal.get('grace_late_minutes', 10) or 10))
                _ge = int(float(sal.get('early_departure_grace_minutes',
                                        sal.get('grace_early_minutes', 5)) or 5))
                if _pf <= _shs + _gl:
                    d_late = 0
                if _pt >= _she - _ge:
                    d_early = 0

        if hire_dt and d < hire_dt:
            status = 'not_hired'
        elif eos_dt and d > eos_dt:
            status = 'ended'
        elif leave_hit:
            status = 'leave_sick' if _is_sick_type(leave_hit[3]) else (
                'leave_paid' if leave_hit[2] else 'leave_unpaid')
        elif (d.isoweekday() % 7) in off_days:
            status = 'weekly_off'
        elif ds in holidays:
            status = 'holiday'
        elif exc and not recs:
            status = 'excused'
        elif not recs:
            status = 'absent'
        elif span_h > 0:
            status = 'present'
        else:
            status = 'partial'

        if flex == 'any_time' and status in ('present', 'partial'):
            # Any-Time contract: the punched day is a full credited day
            status = 'present'
            span_h = float(emp['hours_per_day'] or 8)

        _ot_step_d = int(float(sal.get('overtime_round_to_minutes', 15) or 0))
        if recs and span_h > 0:
            if status == 'weekly_off':
                d_ot = _ot_round(span_h * 60, _ot_step_d); d_ot_kind = 'weekend'
            elif status == 'holiday':
                d_ot = _ot_round(span_h * 60, _ot_step_d); d_ot_kind = 'holiday'
            elif status in ('present', 'partial') and flex != 'any_time':
                if flex == 'none' and last_t:
                    _she2 = _t2m(emp['shift_end'], (emp['default_end_time'] or '16:00'))
                    _raw = _t2m(last_t, '00:00') - _she2
                    if _raw > 0:
                        d_ot = _ot_round(_raw, _ot_step_d); d_ot_kind = 'weekday'
                elif flex == 'fixed_hours':
                    _hpdx = float(emp['hours_per_day'] or 8)
                    if span_h > _hpdx:
                        d_ot = _ot_round((span_h - _hpdx) * 60, _ot_step_d); d_ot_kind = 'weekday'

        _hp_pres = False
        if hp and pres_on:
            _pf, _pt = _t2m(hp['from'], '00:00'), _t2m(hp['to'], '00:00')
            _hp_pres = not (_pt <= _t2m(pres_ps, '00:00') or _pf >= _t2m(pres_pe, '00:00'))
        p_missing = bool(pres_on and recs and not exc and not _hp_pres
                         and status in ('present', 'partial')
                         and _presence_missing(times, pres_ps, pres_pe))
        p_in = p_out = None
        if pres_on and recs and status in ('present', 'partial'):
            _pw = _presence_punches(times, pres_ps, pres_pe)
            if _pw:
                p_in = _pw[0]
                p_out = _pw[-1] if _pw[-1] != _pw[0] else None
        days.append({'date': ds, 'weekday': d.isoweekday() % 7,
                     'status': status, 'presence_missing': p_missing,
                     'presence_in': p_in, 'presence_out': p_out,
                     'hourly_perm': hp,
                     'ot_mins': d_ot, 'ot_kind': d_ot_kind,
                     'holiday_name': holidays.get(ds),
                     'leave_type': leave_hit[3] if leave_hit else None,
                     'leave_reason': leave_hit[4] if leave_hit else None,
                     'excuse': exc,
                     'check_in': first_t or '--',
                     'check_out': last_t or '--',
                     'hours': span_h, 'punch_count': len(recs),
                     'late_mins': d_late, 'early_mins': d_early,
                     'has_manual': has_manual})
        d += timedelta(days=1)

    return {'presence_required': pres_on,
            'employee': {'id': emp['id'], 'name': emp['name'],
                         'arabic_name': emp['arabic_name'],
                         'employee_number': emp['employee_number'],
                         'department': emp['department'] or '',
                         'position': emp['position'] or ''},
            'period': {'start': start_iso, 'end': end_iso},
            'days': _rest_flag_days(days)}


def _rest_flag_days(days):
    """Post-pass: flag weekly_off/holiday rows whose week earned no credit."""
    cred = set()
    for _dd in days:
        _wd = _parse_d(_dd['date'])
        _wkk = (_wd - timedelta(days=(_wd.isoweekday() % 7))).isoformat()
        _dd['_wk'] = _wkk
        if _dd['status'] in ('present', 'partial', 'excused',
                             'leave_sick', 'leave_paid') \
           or (_dd['status'] in ('weekly_off', 'holiday')
               and (_dd.get('hours') or 0) > 0):
            cred.add(_wkk)
    for _dd in days:
        _dd['rest_unpaid'] = (_dd['status'] in ('weekly_off', 'holiday')
                              and (_dd.get('hours') or 0) <= 0
                              and _dd['_wk'] not in cred)
        _dd.pop('_wk', None)
    return days
