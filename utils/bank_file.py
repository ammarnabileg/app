# -*- coding: utf-8 -*-
"""ملف تحويل الرواتب بالصيغة اللي البنك بيطلبها (WPS / تحويل جماعي).

كل بنك ليه صيغة (ترتيب أعمدة، فاصل، سطر عنوان، المبلغ بالدينار ولا بالفلس…). بدل ما نثبّت صيغة
بنك بعينه من غير مواصفاته الرسمية، الصيغة **بتتظبط من الشاشة** على عينة البنك:

    {"format": "csv" | "xlsx", "delimiter": ",", "header": true, "columns": ["iban", "name_en", "amount", …],
     "debit_account": "…", "company_code": "…"}

وبيتحفظ في الإعداد `bank_file_layout`. والملف القديم (Excel بـ 3 شيتات) زي ما هو.

## الأعمدة المتاحة
FIELDS تحت. `amount_fils` = المبلغ × 1000 رقم صحيح (بنوك كتير بتطلبه كده).

## الفحص قبل التصدير
الـ IBAN الكويتي (KW + 28 حرف، ورقم التحقق mod-97) بيتفحص، وأي موظف بيتحوّل بنك من غير IBAN سليم
بيطلع في `problems` — الملف مابيطلعش لحد ما يتصلّح أو تختار «صدّر برضه».
"""
import csv
import io
import json
from datetime import date

SETTING = 'bank_file_layout'
FIELDS = {
    'employee_number': 'رقم الموظف',
    'name_en': 'الاسم (إنجليزي)',
    'name_ar': 'الاسم (عربي)',
    'civil_id': 'الرقم المدني',
    'iban': 'IBAN',
    'account': 'رقم الحساب',
    'bank': 'البنك',
    'swift': 'SWIFT',
    'amount': 'المبلغ (دينار، 3 عشري)',
    'amount_fils': 'المبلغ بالفلس (رقم صحيح)',
    'currency': 'العملة (KWD)',
    'reference': 'البيان (Salary MM/YYYY)',
    'debit_account': 'حساب الشركة المدين',
    'company_code': 'كود الشركة عند البنك',
    'value_date': 'تاريخ التنفيذ',
    'basic': 'الأساسي',
    'allowances': 'البدلات',
    'deductions': 'الخصومات',
}
DEFAULT_LAYOUT = {'format': 'csv', 'delimiter': ',', 'header': True,
                  'columns': ['employee_number', 'name_en', 'civil_id', 'iban', 'amount', 'currency', 'reference'],
                  'debit_account': '', 'company_code': ''}


def load_layout(conn=None):
    from utils.db import get_setting
    try:
        lay = json.loads(get_setting(SETTING, '') or '{}')
    except ValueError:
        lay = {}
    out = dict(DEFAULT_LAYOUT)
    out.update({k: v for k, v in lay.items() if k in DEFAULT_LAYOUT})
    out['columns'] = [c for c in out['columns'] if c in FIELDS] or list(DEFAULT_LAYOUT['columns'])
    if out['format'] not in ('csv', 'xlsx'):
        out['format'] = 'csv'
    return out


def save_layout(form):
    from utils.db import set_setting
    cols = [c.strip() for c in str(form.get('columns', '')).split(',') if c.strip() in FIELDS]
    lay = {'format': 'xlsx' if form.get('format') == 'xlsx' else 'csv',
           'delimiter': {'semicolon': ';', 'tab': '\t', 'pipe': '|'}.get(form.get('delimiter'), ','),
           'header': form.get('header') == '1',
           'columns': cols or list(DEFAULT_LAYOUT['columns']),
           'debit_account': str(form.get('debit_account') or '').strip()[:40],
           'company_code': str(form.get('company_code') or '').strip()[:40]}
    set_setting(SETTING, json.dumps(lay))
    return lay


def iban_ok(iban):
    """IBAN كويتي سليم: KW + 28 خانة، ورقم التحقق (ISO 13616 mod-97)."""
    s = str(iban or '').replace(' ', '').upper()
    if len(s) != 30 or not s.startswith('KW') or not s[2:4].isdigit() or not s.isalnum():
        return False
    r = s[4:] + s[:4]
    n = ''.join(str(int(ch, 36)) for ch in r)
    return int(n) % 97 == 1


def rows(conn, month, year, value_date=None):
    """(صفوف التحويل البنكي، المشاكل، المصدر 'saved'|'live')."""
    from utils.payslip import saved_run, slips_saved, slips_live, _emp_rows
    lay = load_layout(conn)
    run = saved_run(conn, month, year)
    slips = slips_saved(conn, month, year) if run else slips_live(conn, month, year)
    emps = _emp_rows(conn, [s['employee_id'] for s in slips])
    ref = f'Salary {month:02d}/{year}'
    vd = value_date or date.today().isoformat()
    out, problems = [], []
    for s in slips:
        net = round(float(s['net'] or 0), 3)
        if net <= 0.0005 or s.get('payment') != 'Bank Transfer':      # النقدي والشيك برا ملف البنك
            continue
        e = emps.get(s['employee_id'])
        g = (lambda k: (e[k] if e is not None and k in e.keys() else '') or '')
        iban = str(g('bank_iban')).replace(' ', '').upper()
        if not iban_ok(iban):
            problems.append({'employee_number': s['employee_number'], 'name': s['name'],
                             'iban': iban, 'why': 'مفيش IBAN' if not iban else 'IBAN غلط'})
        vals = {'employee_number': s['employee_number'], 'name_en': s.get('name_en') or s['name'],
                'name_ar': g('arabic_name') or s['name'], 'civil_id': g('national_id'), 'iban': iban,
                'account': g('bank_account_number'), 'bank': g('bank_name'), 'swift': g('bank_swift'),
                'amount': f'{net:.3f}', 'amount_fils': str(int(round(net * 1000))), 'currency': 'KWD',
                'reference': ref, 'debit_account': lay['debit_account'], 'company_code': lay['company_code'],
                'value_date': vd, 'basic': f"{float(s.get('basic') or 0):.3f}",
                'allowances': f"{float(s.get('total_allowances') or 0):.3f}",
                'deductions': f"{float(s.get('total_deductions') or 0):.3f}"}
        out.append([vals[c] for c in lay['columns']])
    return out, problems, ('saved' if run else 'live')


def build(conn, month, year, value_date=None):
    """(bytes، اسم الملف، mimetype، المشاكل، المصدر)."""
    lay = load_layout(conn)
    data, problems, src = rows(conn, month, year, value_date)
    head = [c.upper() for c in lay['columns']]
    name = f'bank_{year}-{month:02d}'
    if lay['format'] == 'xlsx':
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        if lay['header']:
            ws.append(head)
        for r in data:
            ws.append(r)                                    # نص زي ما هو: IBAN والرقم المدني مايتقلبوش أرقام
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), name + '.xlsx', \
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', problems, src
    sio = io.StringIO()
    w = csv.writer(sio, delimiter=lay['delimiter'], lineterminator='\r\n')
    if lay['header']:
        w.writerow(head)
    w.writerows(data)
    return sio.getvalue().encode('utf-8'), name + '.csv', 'text/csv', problems, src
