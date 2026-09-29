import sqlite3
from flask import session, Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_babel import gettext
from datetime import datetime
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission

eos_bp = Blueprint('eos', __name__)

EOS_FULL_REASONS = ('termination', 'retirement', 'contract_end',
                    'death', 'disability', 'resignation_marriage',
                    'resignation_art48')

# سببُ الإنهاء ← مفتاحُ ترجمته. الطباعةُ والسجلّ يقرآن من هنا.
EOS_REASONS = {
    'resignation': 'x.resignation',
    'resignation_marriage': 'x.eos_resign_marriage',
    'resignation_art48': 'x.eos_resign_art48',
    'termination': 'x.dismissal',
    'art41': 'x.eos_art41',
    'retirement': 'x.retirement',
    'contract_end': 'x.contract_end',
    'death': 'x.eos_death',
    'disability': 'x.eos_disability',
    'other': 'x.other',
}

# بنودُ المادة 41 — الفصلُ بلا مكافأة لا يُقبل بغير بندٍ منها ودليل.
ART41_CLAUSES = {
    'a': 'x.eos_art41_a', 'b': 'x.eos_art41_b', 'c': 'x.eos_art41_c',
    'd': 'x.eos_art41_d', 'e': 'x.eos_art41_e',
}

KUWAITI = {'kuwait', 'kuwaiti', 'kw', 'kwt', 'كويت', 'كويتي', 'كويتية',
           'الكويت'}


def is_kuwaiti(nationality):
    return (nationality or '').strip().lower() in KUWAITI


@eos_bp.app_template_filter('eos_reason')
def eos_reason_label(code):
    return gettext(EOS_REASONS.get(code or '', 'x.other'))


@eos_bp.app_template_filter('art41_clause')
def art41_clause_label(code):
    key = ART41_CLAUSES.get(code or '')
    return gettext(key) if key else ''


def _eos_fraction(reason, years):
    """نسبةُ الاستحقاق (المواد 41، 48، 51، 52، 53).

    - المادة 41: فصلٌ لخطأ جسيم → لا مكافأة.
    - الاستقالة: أقلُّ من 3 سنوات لا شيء، ومن 3 **إلى 5 شاملةً** النصف،
      وما زاد على 5 وقلّ عن 10 الثلثان، و10 فأكثر كاملة.
    - الاستقالةُ خلال سنةٍ من الزواج (للعاملة) أو لإخلال صاحب العمل
      (المادة 48) → كاملة، كسائر الأسباب.
    """
    if reason == 'art41':
        return 0.0
    if reason != 'resignation':
        return 1.0
    if years < 3:
        return 0.0
    if years <= 5:
        return 0.5
    if years < 10:
        return 2.0 / 3.0
    return 1.0


def _anniversary(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:          # 29 فبراير في سنةٍ غير كبيسة
        return d.replace(year=d.year + n, day=28)


def _service_years(hire, term):
    """السنواتُ بالتقويم لا بقسمة 365: من 2021-01-01 إلى 2026-01-01 خمسُ
    سنواتٍ تمامًا — والقسمةُ تجعلها 5.003 فتنقل المستقيلَ من النصف إلى الثلثين."""
    full = term.year - hire.year - ((term.month, term.day) < (hire.month, hire.day))
    last, nxt = _anniversary(hire, full), _anniversary(hire, full + 1)
    return full + (term - last).days / float((nxt - last).days)


def compute_kuwait_eos(conn, employee_id, termination_date, reason,
                       leave_days=None, pifss=None, notice_served_days=None,
                       notice_waived=False):
    """Kuwaiti end-of-service on the /26 basis: 15 days/year for the first
    five years, one month/year afterwards, 18-month cap, pro-rata
    fractions. Wage = basic + active fixed recurring earnings. Leave
    balance pays at the daily rate outside the resignation fraction.
    Outstanding loans are deducted and settled.

    `pifss`: للكويتيّ — ما دفعه صاحبُ العمل للتأمينات عن المكافأة (من كشف
    المؤسسة). صاحبُ العمل يدفع الفرقَ فقط، فيُطرح من المكافأة ولا ينزلها
    تحت الصفر، ولا يمسّ رصيدَ الإجازات."""
    emp = conn.execute('SELECT * FROM employees WHERE id = ?',
                       (employee_id,)).fetchone()
    if not emp:
        return None
    try:
        hire = datetime.strptime(str(emp['hire_date'])[:10], '%Y-%m-%d')
        term = datetime.strptime(str(termination_date)[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        return None
    days = (term - hire).days
    if days <= 0:
        return None
    years = _service_years(hire, term)

    from utils.payroll_engine import fetch_fixed_earnings
    basic = float(emp['salary'] or 0)
    wage_lines = [{'name_ar': 'الراتب الأساسي', 'name_en': 'Basic salary',
                   'amount': round(basic, 3)}]
    wage = basic
    for it in fetch_fixed_earnings(conn, employee_id, basic, as_of=termination_date):
        wage += it['amount']
        wage_lines.append({'name_ar': it['name_ar'], 'name_en': it['name_en'],
                           'amount': it['amount']})
    daily = wage / 26.0

    # المادة 51: الأجرُ الشهريّ 15 يومًا عن كلّ سنةٍ من الخمس الأولى ثم شهرٌ،
    # بحدّ 18 شهرًا؛ واليوميُّ وما في حكمه (بالساعة أو بالقطعة) 10 أيّام ثم
    # 15، بحدّ أجر سنة.
    from utils.labor_law import DAILY_EOS_DAYS, DAILY_EOS_CAP_DAYS
    pay_type = (emp['pay_type'] if 'pay_type' in emp.keys() else None) or 'monthly'
    if pay_type == 'daily':
        t1_rate, t2_rate = DAILY_EOS_DAYS
        cap_days = float(DAILY_EOS_CAP_DAYS)
    else:
        t1_rate, t2_rate = 15.0, 26.0
        cap_days = 18 * 26.0
    tier1_days = min(years, 5.0) * t1_rate
    tier2_days = max(0.0, years - 5.0) * t2_rate
    raw_days = tier1_days + tier2_days
    capped = raw_days > cap_days
    pay_days = min(raw_days, cap_days)
    gross = pay_days * daily
    fraction = _eos_fraction(reason, years)
    gratuity = gross * fraction
    gratuity_before_pifss = gratuity
    try:
        pifss_amt = max(0.0, float(pifss or 0))
    except (TypeError, ValueError):
        pifss_amt = 0.0
    pifss_amt = min(pifss_amt, gratuity)
    gratuity -= pifss_amt

    from utils.leave_balance import compute_leave_balance
    _bal = compute_leave_balance(conn, employee_id, as_of=termination_date)
    suggested_leave = max(0.0, _bal['balance']) if _bal else 0.0
    use_leave = suggested_leave if leave_days is None else max(0.0,
                                                              float(leave_days))
    leave_amount = use_leave * daily

    loans = []
    loans_total = 0.0
    for ln in conn.execute("""SELECT id, principal FROM employee_loans
                              WHERE employee_id = ? AND status = 'active'""",
                           (employee_id,)).fetchall():
        paid = conn.execute('SELECT COALESCE(SUM(amount),0) FROM loan_payments '
                            'WHERE loan_id = ?', (ln['id'],)).fetchone()[0]
        rem = round(float(ln['principal'] or 0) - float(paid or 0), 3)
        if rem > 0:
            loans.append({'loan_id': ln['id'], 'remaining': rem})
            loans_total += rem

    # بدلُ الإنذار (المادة 44): موجبٌ على صاحب العمل إن فصل، وسالبٌ على
    # المستقيل إن لم يُتمّ المهلة ولم يتنازل صاحبُ العمل. ولا إنذارَ في التجربة.
    from utils.labor_law import notice_amount, in_probation
    probation = in_probation(emp, termination_date)
    notice_amt, notice_short = notice_amount(reason, wage, notice_served_days,
                                             waived=notice_waived,
                                             in_probation=probation)

    net = gratuity + leave_amount - loans_total + notice_amt
    return {'employee_id': employee_id,
            'wage_lines': wage_lines,
            'monthly_wage': round(wage, 3),
            'daily_rate': round(daily, 3),
            'basis': '26',
            'pay_type': pay_type,
            'service_days': days,
            'years': round(years, 4),
            'tier1_days': round(tier1_days, 2),
            'tier2_days': round(tier2_days, 2),
            'capped': capped,
            'pay_days': round(pay_days, 2),
            'gross_gratuity': round(gross, 3),
            'fraction': round(fraction, 4),
            'reason': reason,
            'is_kuwaiti': is_kuwaiti(emp['nationality'] if 'nationality' in emp.keys() else None),
            'gratuity_before_pifss': round(gratuity_before_pifss, 3),
            'pifss_deduction': round(pifss_amt, 3),
            'gratuity': round(gratuity, 3),
            'suggested_leave_days': suggested_leave,
            'leave_days': round(use_leave, 2),
            'leave_amount': round(leave_amount, 3),
            'loans': loans,
            'loans_total': round(loans_total, 3),
            'in_probation': probation,
            'notice_served_days': notice_served_days,
            'notice_short_days': notice_short,
            'notice_waived': bool(notice_waived),
            'notice_amount': notice_amt,
            'net': round(net, 3)}

def _notice_days(src):
    v = (src.get('notice_served_days') or '').strip()
    return None if v == '' else v


def _validate_reason(emp, reason, clause, notes):
    """يعيد مفتاحَ رسالة الخطأ، أو None."""
    if reason not in EOS_REASONS:
        return 'x.f_eos_bad_reason'
    if reason == 'art41':
        if clause not in ART41_CLAUSES:
            return 'x.f_eos_art41_clause'
        if not (notes or '').strip():
            return 'x.f_eos_art41_evidence'
    if reason == 'resignation_marriage':
        g = (emp['gender'] if 'gender' in emp.keys() else '') or ''
        if g.strip().lower() in ('male', 'm', 'ذكر'):
            return 'x.f_eos_marriage_female'
    return None


@eos_bp.route('/eos/terminate', methods=['GET', 'POST'])
@login_required
@require_permission('page.eos')
def terminate_employee():
    conn = get_db_connection()
    if request.method == 'POST':
        employee_id = request.form.get('employee_id')
        termination_date = request.form.get('termination_date')
        reason = request.form.get('reason')
        notes = request.form.get('notes')
        
        emp = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if not emp:
            flash(gettext('x.f_employee_not_found'), "danger")
            return redirect(url_for('eos.terminate_employee'))
            
        err = _validate_reason(emp, reason, request.form.get('art41_clause'),
                               notes)
        if err:
            flash(gettext(err), "danger")
            return redirect(url_for('eos.terminate_employee'))

        ld = request.form.get('leave_days')
        calc = compute_kuwait_eos(conn, int(employee_id), termination_date,
                                  reason,
                                  leave_days=(None if ld in (None, '')
                                              else ld),
                                  pifss=request.form.get('pifss_deduction'),
                                  notice_served_days=_notice_days(request.form),
                                  notice_waived=bool(request.form.get('notice_waived')))
        if not calc:
            flash(gettext('x.f_eos_bad_dates'), "danger")
            return redirect(url_for('eos.terminate_employee'))
        if reason == 'art41':
            calc['art41_clause'] = request.form.get('art41_clause')

        import json as _j
        cur = conn.execute('''
            INSERT INTO end_of_service_records
            (employee_id, termination_date, reason, years_of_service,
             reward_amount, net_payout, notes, created_by,
             monthly_wage, daily_rate, basis, tier1_days, tier2_days,
             capped, fraction, gratuity_amount, leave_days, leave_amount,
             loans_deducted, breakdown_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (employee_id, termination_date, reason, calc['years'],
              calc['gratuity'], calc['net'], notes, session.get('user_id'),
              calc['monthly_wage'], calc['daily_rate'], calc['basis'],
              calc['tier1_days'], calc['tier2_days'],
              1 if calc['capped'] else 0, calc['fraction'],
              calc['gratuity'], calc['leave_days'], calc['leave_amount'],
              calc['loans_total'], _j.dumps(calc, ensure_ascii=False)))
        for ln in calc['loans']:
            conn.execute('''INSERT INTO loan_payments
                            (loan_id, month, year, amount, source, created_by)
                            VALUES (?, ?, ?, ?, 'eos', ?)''',
                         (ln['loan_id'], int(str(termination_date)[5:7]),
                          int(str(termination_date)[:4]), ln['remaining'],
                          session.get('user_id')))
            conn.execute("UPDATE employee_loans SET status = 'settled' "
                         "WHERE id = ?", (ln['loan_id'],))
        conn.execute("UPDATE employees SET is_active = 0, "
                     "end_of_service_date = ? WHERE id = ?",
                     (termination_date, employee_id))
        try:
            from utils.payroll_engine import log_employee_event
            log_employee_event(conn, int(employee_id), 'end_of_service_date',
                               '', str(termination_date), 'status',
                               user_id=session.get('user_id'),
                               source='eos_settlement',
                               note=eos_reason_label(reason) if reason else None)
        except Exception as _e:
            print(f'audit log failed on EOS: {_e}')
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        flash(gettext('x.f_eos_saved'), "success")
        return redirect(url_for('eos.list_eos'))
        
    # GET Request
    employees = conn.execute("SELECT * FROM employees WHERE is_active = 1").fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('eos/terminate.html', employees=employees)

@eos_bp.route('/api/eos/calculate')
@login_required
@require_permission('salary.calculate')
def api_calculate_eos():
    emp_id = request.args.get('employee_id')
    term_date = request.args.get('termination_date')
    reason = request.args.get('reason')
    
    if not emp_id or not term_date or not reason:
        return jsonify({"error": "Missing parameters"}), 400
        
    conn = get_db_connection()
    ld = request.args.get('leave_days')
    calc = compute_kuwait_eos(conn, int(emp_id), term_date, reason,
                              leave_days=(None if ld in (None, '') else ld),
                              pifss=request.args.get('pifss_deduction'),
                              notice_served_days=_notice_days(request.args),
                              notice_waived=bool(request.args.get('notice_waived')))
    if not calc:
        return jsonify({"error": "Employee not found or bad dates"}), 404
    return jsonify({"years_worked": calc['years'],
                    "reward_amount": calc['gratuity'],
                    "calc": calc})

@eos_bp.route('/eos/list')
@login_required
@require_permission('page.eos')
def list_eos():
    conn = get_db_connection()
    records = conn.execute('''
        SELECT eos.*, e.name, e.employee_number, e.department 
        FROM end_of_service_records eos
        JOIN employees e ON eos.employee_id = e.id
        ORDER BY eos.termination_date DESC
    ''').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('eos/list.html', records=records)


@eos_bp.route('/eos/print/<int:rec_id>')
@login_required
@require_permission('salary.view')
def print_eos(rec_id):
    import json as _j
    conn = get_db_connection()
    rec = conn.execute("""SELECT eos.*, e.name, e.arabic_name,
                                 e.employee_number, e.department, e.position,
                                 e.hire_date
                          FROM end_of_service_records eos
                          JOIN employees e ON e.id = eos.employee_id
                          WHERE eos.id = ?""", (rec_id,)).fetchone()
    if not rec:
        return 'record not found', 404
    keys = rec.keys()
    raw = rec['breakdown_json'] if 'breakdown_json' in keys else None
    calc = _j.loads(raw or '{}')
    # A record saved before the detailed-breakdown schema existed carries only
    # its headline figures. Printing NULLs as 0.000 next to a real net payout
    # produces a self-contradicting document, so such records are flagged and
    # the empty detail sections are suppressed instead.
    legacy = not calc or (('monthly_wage' not in keys) or rec['monthly_wage'] is None)
    from utils.settings_utils import get_system_settings
    sysx = get_system_settings() or {}
    return render_template('eos/print.html', r=rec, c=calc, legacy=legacy,
                           company=sysx.get('company_name', ''),
                           currency=sysx.get('currency_symbol', ''))
