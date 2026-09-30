# -*- coding: utf-8 -*-
"""كشفُ الرواتب الشهريّ بشكل الشيت الذي تعمل به الشركة (Monthly Payroll).

الأعمدةُ كما في الشيت: الراتبُ بالعقد، ثم المستحقُّ عن أيّام العمل، ثم
الإضافات (الإضافيّ بساعاته ومبالغه، الإجازة، العمولة، أخرى)، ثم الإجماليّ،
ثم الخصومات (الغياب، الجزاءات، السلف، أخرى)، ثم الصافي.

## المصدر

كلُّ مبلغٍ بندٌ من `compute_monthly_payroll` — المحرّكِ الذي يعرضه الكشف
ويحفظه ويصرفه. هنا توزيعٌ على الأعمدة لا حسابٌ ثانٍ، وكلُّ صفٍّ يتحقّق:
**الإجماليّ − الخصومات = صافي المحرّك** (`check`).

## ملفُّ Excel

قالبُ الشركة نفسُه (`templates/xlsx/monthly_payroll.xlsx`، بلا بيانات):
تنسيقُه وأعمدتُه المخفيّة ومعادلاتُه. القيمُ كلُّها من النظام، والأيّامُ
(غياب، جزاء) تُكتب بحيث تُرجع معادلةُ الشيت مبلغَ المحرّك نفسَه. وما لا
يحمله النظام (اسمٌ عربيّ، مركزُ تكلفة، حسابٌ بنكيّ، راتب، بصمات الفترة…)
يُلوَّن بالأحمر (`missing_cells`).

## أيّامُ العمل

قاسمُ اليوم (26 أو 30 أو أيّام الجدول) ناقصًا ما قبل التعيين وما بعد
انتهاء الخدمة. فالأساسيُّ المستحقّ = الأساسيّ − خصمُهما، والبدلاتُ الثابتة
يناسبها المحرّكُ بالأيّام نفسِها (`full_amount` للعقد، و`amount` للمستحقّ).

## الجزاءات (Fine)

التأخيرُ والانصرافُ المبكّر والبصمةُ الناقصة وجزاءُ التواجد والجزاءُ
الإداريّ. و«الأيّام» بجوارها أيّامُ أجرٍ مكافئة (المبلغ ÷ أجر اليوم)،
لأنّ التأخير دقائقُ لا أيّام.
"""
import io
import os
from datetime import date

FIXED_COLS = ('transport', 'housing', 'phone')
FINE_CODES = {'lateness_deduction', 'early_leave_deduction', 'missing_punch_penalty',
              'presence_penalty', 'admin_penalty'}
PRORATION_CODES = {'pre_hire_days', 'post_service_days'}
MONEY_EPS = 0.0005

CONTRACT_TYPES = {'open_ended': 'Open-ended', 'full_time': 'Full time', 'part_time': 'Part time',
                  'fixed_term': 'Fixed term', 'freelance': 'Freelance'}


def _sum(items, pred):
    return round(sum(i['amount'] for i in items if pred(i)), 3)


def build_sheet(conn, month, year):
    """يعيد {'period', 'rows', 'totals'}؛ كلُّ صفٍّ قاموسٌ بمفاتيح الأعمدة."""
    from utils.leave_balance import compute_leave_balance
    from utils.payroll_engine import compute_monthly_payroll
    data = compute_monthly_payroll(conn, month, year)
    p_end = data['period']['end']
    emp_info = {r['id']: r for r in conn.execute('SELECT * FROM employees').fetchall()}

    rows = []
    for r in data['rows']:
        e = emp_info.get(r['employee_id'])
        ek = e.keys() if e else []
        alw, ded = r['allowances'], r['deductions']
        fixed = [a for a in alw if a.get('source') == 'fixed']
        # بالعقد: البدلُ كاملًا؛ والمستحقّ: ما يصرفه المحرّك بعد تناسب الأيّام.
        full = [dict(a, amount=a.get('full_amount', a['amount'])) for a in fixed]
        c_tr = _sum(full, lambda a: a['code'] == 'transport')
        c_ho = _sum(full, lambda a: a['code'] == 'housing')
        c_ph = _sum(full, lambda a: a['code'] == 'phone')
        c_ot = _sum(full, lambda a: a['code'] not in FIXED_COLS)
        c_allow = round(c_tr + c_ho + c_ph + c_ot, 3)
        e_tr = _sum(fixed, lambda a: a['code'] == 'transport')
        e_ho = _sum(fixed, lambda a: a['code'] == 'housing')
        e_ph = _sum(fixed, lambda a: a['code'] == 'phone')
        e_ot = _sum(fixed, lambda a: a['code'] not in FIXED_COLS)
        e_allow = round(e_tr + e_ho + e_ph + e_ot, 3)

        daily = float(r.get('daily_rate') or 0)
        base_days = round(r['basic'] / daily) if daily > MONEY_EPS else 0
        pro_days = sum(int(d.get('days') or 0) for d in ded if d['code'] in PRORATION_CODES)
        pro_amt = _sum(ded, lambda d: d['code'] in PRORATION_CODES)
        e_basic = round(r['basic'] - pro_amt, 3)

        otd = r.get('ot_detail') or {}
        ot_amt = _sum(alw, lambda a: a['code'] == 'overtime')
        leave_amt = _sum(alw, lambda a: a['code'] == 'leave_encashment')
        comm = _sum(alw, lambda a: a['code'] == 'commission')
        others = _sum(alw, lambda a: a.get('source') != 'fixed'
                      and a['code'] not in ('overtime', 'leave_encashment', 'commission'))
        gross = round(e_basic + e_allow + ot_amt + leave_amt + comm + others, 3)

        absent = [d for d in ded if d['code'] == 'absence_deduction']
        abs_days = sum(float(d.get('days') or 0) for d in absent)
        abs_amt = _sum(absent, lambda d: True)
        fine_amt = _sum(ded, lambda d: d['code'] in FINE_CODES)
        fine_days = round(fine_amt / daily, 2) if daily > MONEY_EPS else 0
        loan = _sum(ded, lambda d: d['code'] == 'loan_installment')
        other_ded = _sum(ded, lambda d: d['code'] not in FINE_CODES | PRORATION_CODES
                         | {'absence_deduction', 'loan_installment'})
        total_ded = round(abs_amt + fine_amt + loan + other_ded, 3)
        net = round(gross - total_ded, 3)

        try:
            bal = compute_leave_balance(conn, r['employee_id'], as_of=p_end)
            leave_bal = bal['balance'] if bal else None
        except Exception:
            leave_bal = None
        ct = (e['contract_type'] if e is not None and 'contract_type' in ek else '') or ''
        from utils.employee_gaps import payment_label
        pay_label, pay_known = payment_label(e) if e is not None else ('Cash', False)
        rows.append({
            'employee_id': r['employee_id'],
            'emp_no': r['employee_number'], 'name_e': r['name'] or '',
            'name_a': r['arabic_name'] or '',
            'doj': (e['hire_date'] if e is not None else '') or '',
            'cost_center': (e['cost_center'] if e is not None and 'cost_center' in ek else '') or '',
            'dept': (e['department'] if e is not None else '') or '',
            'location': (e['branch_location'] if e is not None and 'branch_location' in ek else '') or '',
            'job': (e['position'] if e is not None else '') or '',
            'c_basic': r['basic'], 'c_trans': c_tr, 'c_house': c_ho, 'c_phone': c_ph,
            'c_other': c_ot, 'c_allow': c_allow, 'c_total': round(r['basic'] + c_allow, 3),
            'work_days': max(0, base_days - pro_days),
            'e_basic': e_basic, 'e_trans': e_tr, 'e_house': e_ho, 'e_phone': e_ph,
            'e_other': e_ot, 'e_allow': e_allow, 'e_total': round(e_basic + e_allow, 3),
            'ot_h_daily': otd.get('weekday_hours', 0), 'ot_h_fri': otd.get('weekend_hours', 0),
            'ot_h_hol': otd.get('holiday_hours', 0),
            'ot_a_daily': otd.get('weekday_amount', 0), 'ot_a_fri': otd.get('weekend_amount', 0),
            'ot_a_hol': otd.get('holiday_amount', 0), 'ot_total': ot_amt,
            'leave': leave_amt, 'commission': comm, 'others': others, 'gross': gross,
            'abs_days': abs_days, 'abs_amt': abs_amt, 'fine_days': fine_days, 'fine_amt': fine_amt,
            'loan': loan, 'other_ded': other_ded, 'total_ded': total_ded, 'net': net,
            'engine_net': r['net'],
            'pay_type': pay_label,
            'term': CONTRACT_TYPES.get(ct, ct),
            'leave_balance': leave_bal,
            'has_bank': pay_known,
            'nothing_earned': any((w if isinstance(w, str) else w.get('code')) == 'nothing_earned'
                                  for w in (r.get('law_warnings') or [])),
        })

    num_keys = [k for k in (rows[0].keys() if rows else [])
                if isinstance(rows[0][k], (int, float)) and not isinstance(rows[0][k], bool)
                and k not in ('employee_id', 'leave_balance')]
    totals = {k: round(sum(float(x[k] or 0) for x in rows), 3) for k in num_keys}
    return {'period': data['period'], 'month': month, 'year': year, 'rows': rows,
            'totals': totals}


# ------------------------------------------------------------------ الأعمدة

# (المفتاح، العنوان، النوع) — النوع: text | money | days | hours
COLUMNS = [
    ('emp_no', 'Emp#.', 'text'), ('name_e', 'Name_E', 'text'), ('name_a', 'Name_A', 'text'),
    ('doj', 'DOJ.', 'text'), ('cost_center', 'Cost Center', 'text'), ('dept', 'Dept.', 'text'),
    ('location', 'Location', 'text'), ('job', 'Job', 'text'),
    ('c_basic', 'Basic salary', 'money'), ('c_trans', 'Trans.', 'money'),
    ('c_house', 'House', 'money'), ('c_phone', 'Phone', 'money'),
    ('c_other', 'Other allow.', 'money'), ('c_allow', 'Total\nAllowances', 'money'),
    ('c_total', 'Total Salary', 'money'),
    ('work_days', 'Working Days', 'days'),
    ('e_basic', 'Basic salary', 'money'), ('e_trans', 'Trans.', 'money'),
    ('e_house', 'House', 'money'), ('e_phone', 'Phone', 'money'),
    ('e_other', 'Other allow.', 'money'), ('e_allow', 'Total\nAllowances', 'money'),
    ('e_total', 'Total Salary', 'money'),
    ('ot_h_daily', 'Daily', 'hours'), ('ot_h_fri', 'Fridays', 'hours'), ('ot_h_hol', 'Holidays', 'hours'),
    ('ot_a_daily', 'Daily', 'money'), ('ot_a_fri', 'Fridays', 'money'), ('ot_a_hol', 'Holidays', 'money'),
    ('ot_total', 'Total', 'money'),
    ('leave', 'Leave', 'money'), ('commission', 'Commission', 'money'), ('others', 'Others', 'money'),
    ('gross', 'Gross salary', 'money'),
    ('abs_days', 'Days', 'days'), ('abs_amt', 'Amt', 'money'),
    ('fine_days', 'Days', 'days'), ('fine_amt', 'Amt', 'money'),
    ('loan', 'Loan', 'money'), ('other_ded', 'Other', 'money'), ('total_ded', 'Total\nDeduction', 'money'),
    ('net', 'Net Salary', 'money'), ('pay_type', 'Payment Type', 'text'),
    ('term', 'Term', 'text'), ('leave_balance', 'Leave Bal.', 'days'),
]
KEY_INDEX = {k: i + 1 for i, (k, _h, _t) in enumerate(COLUMNS)}

# مجموعاتُ العناوين فوق الأعمدة: (أوّل مفتاح، آخر مفتاح، العنوان، الصف الأوّل للدمج)
GROUPS = [
    ('ot_h_daily', 'ot_total', 'additions', 2),
    ('ot_h_daily', 'ot_h_hol', 'Hours', 3),
    ('ot_a_daily', 'ot_a_hol', 'Amt.', 3),
    ('abs_days', 'total_ded', 'Deduction', 2),
    ('abs_days', 'abs_amt', 'Absent', 3),
    ('fine_days', 'fine_amt', 'Fine', 3),
    ('c_basic', 'c_total', 'Contract salary', 3),
    ('e_basic', 'e_total', 'Earned for working days', 3),
]
# أعمدةٌ عنوانُها في الصفّ 2 مدموجٌ حتى 4 (لا مجموعة فوقها)
TALL = ('leave', 'commission', 'others', 'gross', 'net', 'pay_type')
SIGNATURES = (('name_e', 'Prepared By'), ('dept', 'HR'), ('work_days', 'Finance'),
              ('commission', 'Financial manager'), ('total_ded', 'GM.'))


def header_grid():
    """العناوين ثلاثةَ صفوف (2–4) بدمجها، للطباعة: [[{text, colspan, rowspan}]]."""
    n = len(COLUMNS)
    merges = []                                   # (صف, عمود, صفوف, أعمدة, نص) — الصفوف 0..2
    for key, title, _t in COLUMNS:
        col = KEY_INDEX[key] - 1
        if key in TALL:
            merges.append((0, col, 3, 1, title))
        else:
            merges.append((2, col, 1, 1, title))
    for first, last, title, row in GROUPS:
        a, b = KEY_INDEX[first] - 1, KEY_INDEX[last] - 1
        merges.append((row - 2, a, 1, b - a + 1, title))
    covered = {}
    for r0, c0, rs, cs, text in merges:
        for r in range(r0, r0 + rs):
            for c in range(c0, c0 + cs):
                covered[(r, c)] = (r0, c0)
        covered[(r0, c0, 'cell')] = {'text': text, 'rowspan': rs, 'colspan': cs}
    grid = []
    for r in range(3):
        cells = []
        for c in range(n):
            owner = covered.get((r, c))
            if owner is None:
                cells.append({'text': '', 'rowspan': 1, 'colspan': 1})
            elif owner == (r, c):
                cells.append(covered[(r, c, 'cell')])
        grid.append(cells)
    return grid


TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'templates', 'xlsx', 'monthly_payroll.xlsx')
PROTO_DATA, PROTO_TOTAL, PROTO_SIGN = 5, 6, 7        # صفوفُ النموذج في القالب
MISSING_FILL = 'FFFF8B8B'
LEGEND = 'الخلايا الحمراء: بياناتٌ غير مسجّلة في النظام (أو لا بصمات في الفترة) — راجعها قبل الاعتماد.'


def missing_cells(row):
    """أعمدةُ الصفّ التي لا يحمل النظامُ قيمتَها — تُلوَّن بالأحمر."""
    out = []
    for col, key in (('B', 'name_e'), ('C', 'name_a'), ('D', 'doj'), ('E', 'cost_center'),
                     ('F', 'dept'), ('G', 'location'), ('H', 'job'), ('AR', 'term')):
        if not str(row.get(key) or '').strip():
            out.append(col)
    if not (row.get('c_basic') or 0) > MONEY_EPS:
        out.append('I')
    if not row.get('has_bank'):
        out.append('AQ')
    if row.get('leave_balance') is None:
        out.append('AS')
    if row.get('nothing_earned'):
        out.append('AI')
    return out


def _days_of(amount, total_salary):
    """أيّامٌ تُرجع معادلةُ الشيت منها المبلغَ نفسَه: (الإجماليّ ÷ 26) × الأيّام."""
    return round(amount / (total_salary / 26.0), 6) if total_salary > MONEY_EPS else 0


def write_workbook(sheet, company=''):
    """شيتُ الشركة نفسُه (templates/xlsx/monthly_payroll.xlsx): تنسيقُه وأعمدتُه
    ومعادلاتُه — والقيمُ من النظام وحده. ما لا يحمله النظام يُلوَّن بالأحمر.

    المعادلاتُ كما في شيت الشركة: المستحقّ = العقد ÷ 26 × أيّام العمل، والغياب
    والجزاء = الإجماليّ ÷ 26 × الأيّام. والأيّامُ تُكتب بحيث تُرجع المعادلةُ
    مبلغَ المحرّك نفسَه، فصافي الشيت = صافي المحرّك. والإجماليّ يضمّ الإضافيّ
    والعمولة (كانا خارج معادلته في الشيت الأصليّ)، وتظهر أعمدتُهما إن كان لهما قيمة.
    """
    import copy
    from datetime import datetime
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = load_workbook(TEMPLATE)
    ws = wb.active
    ws.title = f"Payroll {sheet['month']:02d}-{sheet['year']}"
    MAXC = 50
    proto = {}
    for name, r in (('data', PROTO_DATA), ('total', PROTO_TOTAL), ('sign', PROTO_SIGN)):
        proto[name] = ({c: copy.copy(ws.cell(r, c)._style) for c in range(1, MAXC + 1)},
                       ws.row_dimensions[r].height)
    blank = copy.copy(ws.cell(8, 1)._style)
    for r in (PROTO_DATA, PROTO_TOTAL, PROTO_SIGN):
        for c in range(1, MAXC + 1):
            ws.cell(r, c)._style = copy.copy(blank)

    def styled(r, name):
        styles, h = proto[name]
        for c in range(1, MAXC + 1):
            ws.cell(r, c)._style = copy.copy(styles[c])
        ws.row_dimensions[r].height = h

    ws['C2'] = datetime(sheet['year'], sheet['month'], 1)
    p = sheet['period']
    ws['A3'] = f"{p['start']} → {p['end']}" + (f'   {company}' if company else '')
    ws['A3'].font = Font(size=11, italic=True)

    red = PatternFill('solid', fgColor=MISSING_FILL)
    first = 5
    rows = sheet['rows']
    for i, row in enumerate(rows):
        r = first + i
        styled(r, 'data')
        total = float(row['c_total'] or 0)
        num = row['emp_no']
        doj = row['doj']
        try:
            doj = datetime.strptime(str(doj)[:10], '%Y-%m-%d') if doj else None
        except ValueError:
            pass
        vals = {
            'A': int(num) if str(num).isdigit() else num, 'B': row['name_e'] or None,
            'C': row['name_a'] or None, 'D': doj, 'E': row['cost_center'] or None,
            'F': row['dept'] or None, 'G': row['location'] or None, 'H': row['job'] or None,
            'I': row['c_basic'], 'J': row['c_trans'], 'K': row['c_house'], 'L': row['c_phone'],
            'M': row['c_other'], 'N': row['c_allow'], 'O': row['c_total'], 'P': row['work_days'],
            'Q': f'=(I{r}/26)*$P{r}', 'R': f'=(J{r}/26)*$P{r}', 'S': f'=(K{r}/26)*$P{r}',
            'T': f'=(L{r}/26)*$P{r}', 'U': f'=(M{r}/26)*$P{r}', 'V': f'=SUM(R{r}:U{r})',
            'W': f'=V{r}+Q{r}',
            'X': round(row['ot_h_daily'], 2), 'Y': round(row['ot_h_fri'], 2),
            'Z': round(row['ot_h_hol'], 2), 'AA': row['ot_a_daily'], 'AB': row['ot_a_fri'],
            'AC': row['ot_a_hol'], 'AD': f'=SUM(AA{r}:AC{r})',
            'AE': row['leave'], 'AF': row['commission'], 'AG': row['others'],
            'AH': f'=W{r}+AD{r}+AE{r}+AF{r}+AG{r}',
            'AI': _days_of(row['abs_amt'], total) if total > MONEY_EPS else row['abs_days'],
            'AJ': f'=(O{r}/26)*AI{r}',
            'AK': _days_of(row['fine_amt'], total), 'AL': f'=(O{r}/26)*AK{r}',
            'AM': row['loan'], 'AN': row['other_ded'],
            'AO': f'=AN{r}+AM{r}+AL{r}+AJ{r}', 'AP': f'=AH{r}-AO{r}',
            'AQ': row['pay_type'], 'AR': row['term'] or None,
            'AS': round(row['leave_balance'], 2) if row['leave_balance'] is not None else None,
        }
        for col, v in vals.items():
            ws[f'{col}{r}'] = v
        for col in missing_cells(row):
            ws[f'{col}{r}'].fill = red
    last = first + len(rows) - 1
    tot = max(last, first) + 3
    styled(tot, 'total')
    for c in range(9, 43):
        col = get_column_letter(c)
        ws[f'{col}{tot}'] = f'=SUM({col}{first}:{col}{tot - 1})'
    sig = tot + 4
    styled(sig, 'sign')
    for col, v in (('B', 'Preparat By'), ('F', 'HR'), ('P', 'Finance'),
                   ('AF', 'Financial manager'), ('AO', 'GM.')):
        ws[f'{col}{sig}'] = v
    ws.merge_cells(f'AO{sig}:AP{sig}')
    ws[f'B{sig + 2}'] = LEGEND
    ws[f'B{sig + 2}'].fill = red
    ws[f'B{sig + 2}'].font = Font(size=12, bold=True)
    ws[f'B{sig + 2}'].alignment = Alignment(horizontal='left')
    ws.print_area = f'A2:AQ{sig + 2}'
    # الإضافيّ والعمولة: مخفيّةٌ في الشيت الأصليّ — تظهر إن حملت قيمة
    if any((x['ot_total'] or 0) > MONEY_EPS for x in rows):
        for col in ('X', 'Y', 'Z', 'AA', 'AB', 'AC', 'AD'):
            ws.column_dimensions[col].hidden = False
    if any((x['commission'] or 0) > MONEY_EPS for x in rows):
        ws.column_dimensions['AF'].hidden = False

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
