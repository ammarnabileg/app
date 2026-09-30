# -*- coding: utf-8 -*-
"""كشفُ الرواتب الشهريّ بشكل الشيت الذي تعمل به الشركة (Monthly Payroll).

الأعمدةُ كما في الشيت: الراتبُ بالعقد، ثم المستحقُّ عن أيّام العمل، ثم
الإضافات (الإضافيّ بساعاته ومبالغه، الإجازة، العمولة، أخرى)، ثم الإجماليّ،
ثم الخصومات (الغياب، الجزاءات، السلف، أخرى)، ثم الصافي.

## المصدر

كلُّ مبلغٍ بندٌ من `compute_monthly_payroll` — المحرّكِ الذي يعرضه الكشف
ويحفظه ويصرفه. هنا توزيعٌ على الأعمدة لا حسابٌ ثانٍ، وكلُّ صفٍّ يتحقّق:
**الإجماليّ − الخصومات = صافي المحرّك** (`check`). والشيتُ الأصليّ كان
يحسب بمعادلاتٍ في الخلايا؛ هنا قيمٌ ثابتة، فلا يفترق الملفُّ عن النظام
إن فُتح على جهازٍ بإعداداتٍ أخرى.

## أيّامُ العمل

قاسمُ اليوم (26 أو 30 أو أيّام الجدول) ناقصًا ما قبل التعيين وما بعد
انتهاء الخدمة. فالأساسيُّ المستحقّ = الأساسيّ − خصمُهما، والشيتُ يُظهره
تناسبًا بالأيّام كما كان. والبدلاتُ الثابتة تُدفع كاملةً كما في المحرّك.

## الجزاءات (Fine)

التأخيرُ والانصرافُ المبكّر والبصمةُ الناقصة وجزاءُ التواجد والجزاءُ
الإداريّ. و«الأيّام» بجوارها أيّامُ أجرٍ مكافئة (المبلغ ÷ أجر اليوم)،
لأنّ التأخير دقائقُ لا أيّام.
"""
import io
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
        c_tr = _sum(fixed, lambda a: a['code'] == 'transport')
        c_ho = _sum(fixed, lambda a: a['code'] == 'housing')
        c_ph = _sum(fixed, lambda a: a['code'] == 'phone')
        c_ot = _sum(fixed, lambda a: a['code'] not in FIXED_COLS)
        c_allow = round(c_tr + c_ho + c_ph + c_ot, 3)

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
        gross = round(e_basic + c_allow + ot_amt + leave_amt + comm + others, 3)

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
        bank = (e['bank_account_number'] if e is not None and 'bank_account_number' in ek else '') or ''
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
            'e_basic': e_basic, 'e_trans': c_tr, 'e_house': c_ho, 'e_phone': c_ph,
            'e_other': c_ot, 'e_allow': c_allow, 'e_total': round(e_basic + c_allow, 3),
            'ot_h_daily': otd.get('weekday_hours', 0), 'ot_h_fri': otd.get('weekend_hours', 0),
            'ot_h_hol': otd.get('holiday_hours', 0),
            'ot_a_daily': otd.get('weekday_amount', 0), 'ot_a_fri': otd.get('weekend_amount', 0),
            'ot_a_hol': otd.get('holiday_amount', 0), 'ot_total': ot_amt,
            'leave': leave_amt, 'commission': comm, 'others': others, 'gross': gross,
            'abs_days': abs_days, 'abs_amt': abs_amt, 'fine_days': fine_days, 'fine_amt': fine_amt,
            'loan': loan, 'other_ded': other_ded, 'total_ded': total_ded, 'net': net,
            'engine_net': r['net'],
            'pay_type': 'Bank Transfer' if bank.strip() else 'Cash',
            'term': CONTRACT_TYPES.get(ct, ct),
            'leave_balance': leave_bal,
        })

    num_keys = [k for k in (rows[0].keys() if rows else [])
                if isinstance(rows[0][k], (int, float)) and k not in ('employee_id', 'leave_balance')]
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


def write_workbook(sheet, company=''):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from utils.timefmt import EXCEL_HOURS_FORMAT, excel_hours

    wb = Workbook()
    ws = wb.active
    ws.title = f"{sheet['month']:02d}-{sheet['year']}"
    thin = Side(style='thin', color='999999')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    head_fill = PatternFill('solid', fgColor='DDEBF7')
    group_fill = PatternFill('solid', fgColor='BDD7EE')
    bold = Font(bold=True, size=10)
    n = len(COLUMNS)

    # الصفّ 1: أرقامُ الأعمدة كما في الشيت
    for i in range(1, n + 1):
        c = ws.cell(row=1, column=i, value=i)
        c.font, c.alignment = Font(size=8, color='888888'), center

    ws.cell(row=2, column=1, value='Monthly Payroll').font = Font(bold=True, size=14)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=2)
    c = ws.cell(row=2, column=3, value=date(sheet['year'], sheet['month'], 1))
    c.number_format, c.font = 'mmm-yyyy', Font(bold=True, size=14)
    ws.cell(row=3, column=1, value=f"{sheet['period']['start']} → {sheet['period']['end']}"
            + (f'   {company}' if company else '')).font = Font(size=9, color='555555')
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=8)

    for key, title, typ in COLUMNS:
        col = KEY_INDEX[key]
        if key in TALL:
            ws.merge_cells(start_row=2, start_column=col, end_row=4, end_column=col)
            c = ws.cell(row=2, column=col, value=title)
        else:
            c = ws.cell(row=4, column=col, value=title)
        c.font, c.alignment, c.fill, c.border = bold, center, head_fill, border
    for first, last, title, row in GROUPS:
        a, b = KEY_INDEX[first], KEY_INDEX[last]
        ws.merge_cells(start_row=row, start_column=a, end_row=row, end_column=b)
        c = ws.cell(row=row, column=a, value=title)
        c.font, c.alignment, c.fill = bold, center, group_fill
        for i in range(a, b + 1):
            ws.cell(row=row, column=i).border = border

    r = 4
    for row in sheet['rows']:
        r += 1
        for key, _title, typ in COLUMNS:
            v = row[key]
            if typ == 'hours':
                v = excel_hours(v)
            c = ws.cell(row=r, column=KEY_INDEX[key], value=v)
            c.border = border
            c.alignment = Alignment(vertical='center',
                                    horizontal='left' if typ == 'text' else 'center')
            if typ == 'money':
                c.number_format = '#,##0.000'
            elif typ == 'hours':
                c.number_format = EXCEL_HOURS_FORMAT
            elif typ == 'days':
                c.number_format = '0.##'
        if row['net'] < 0:
            ws.cell(row=r, column=KEY_INDEX['net']).font = Font(color='C00000', bold=True)

    r += 1
    ws.cell(row=r, column=KEY_INDEX['name_e'], value='Total').font = bold
    for key, _title, typ in COLUMNS:
        if typ == 'text' or key == 'leave_balance':
            continue
        v = sheet['totals'].get(key, 0)
        c = ws.cell(row=r, column=KEY_INDEX[key], value=excel_hours(v) if typ == 'hours' else v)
        c.font, c.border, c.alignment = bold, border, center
        c.fill = PatternFill('solid', fgColor='FFF2CC')
        c.number_format = {'money': '#,##0.000', 'hours': EXCEL_HOURS_FORMAT}.get(typ, '0.##')

    r += 4
    for key, label in SIGNATURES:
        ws.cell(row=r, column=KEY_INDEX[key], value=label).font = bold

    widths = {'emp_no': 7, 'name_e': 24, 'name_a': 24, 'doj': 11, 'cost_center': 10, 'dept': 12,
              'location': 11, 'job': 14, 'pay_type': 14, 'term': 11}
    for key, _t, typ in COLUMNS:
        ws.column_dimensions[get_column_letter(KEY_INDEX[key])].width = widths.get(
            key, 9 if typ in ('days', 'hours') else 11)
    ws.row_dimensions[4].height = 30
    ws.freeze_panes = ws.cell(row=5, column=KEY_INDEX['c_basic'])
    ws.print_title_rows = '2:4'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
