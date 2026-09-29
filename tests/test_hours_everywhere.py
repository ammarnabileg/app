"""الساعاتُ «ساعات:دقائق» في كل التقارير — لا 8.85 تُقرأ «8 ساعات و85 دقيقة».

ثلاثُ طبقات، وكلٌّ لها اختبار:
  * بايثون والقوالب: `utils/timefmt.hours_hm` (ومرشِّح `hm`).
  * المتصفّح: `fmtHM` في <head> القالب الأساسيّ — قبل أيّ سكربتٍ يستعمله.
  * Excel (كشف العمالة): وقتٌ حقيقيّ (ساعات ÷ 24) بتنسيق [h]:mm — يُجمع ويُقرأ 12:30.
وحارسٌ على القوالب: لا عرضَ لساعات العمل بكسرٍ عشريّ.

يُشغَّل:  python -m pytest tests/test_hours_everywhere.py -v
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TEMPLATES = os.path.join(ROOT, 'templates')


@pytest.mark.parametrize('v, want', [
    (8.85, '8:51'), (185.52, '185:31'), (0, '0:00'), (-0.5, '-0:30'), (8.999, '9:00'),
    ('--', '--'), (None, None), ('8.5', '8:30'),
])
def test_hours_hm(v, want):
    from utils.timefmt import hours_hm
    assert hours_hm(v) == want


def test_the_jinja_filter_uses_the_shared_helper():
    import app as A
    assert A.hours_hm_filter(8.85) == '8:51'


# ------------------------------------------------ المتصفّح

def _fmthm_source():
    base = open(os.path.join(TEMPLATES, 'base.html'), encoding='utf-8').read()
    head = base.split('</head>')[0]
    m = re.search(r'window\.fmtHM\s*=\s*function[\s\S]*?\n        \};', head)
    assert m, 'fmtHM معرَّفٌ في <head> — قبل سكربتات الصفحة'
    return m.group(0)


def test_fmthm_is_defined_in_head():
    _fmthm_source()


@pytest.mark.skipif(not shutil.which('node'), reason='node غير متاح')
def test_fmthm_matches_python():
    from utils.timefmt import hours_hm
    js = 'const window = {};\n' + _fmthm_source() + '\n' + \
         'for (const v of [8.85, 185.52, 0, -0.5, 8.999, 8.8167]) console.log(window.fmtHM(v));'
    out = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=30).stdout.split()
    assert out == [hours_hm(v) for v in (8.85, 185.52, 0, -0.5, 8.999, 8.8167)]


# ------------------------------------------------ الحارس

# ساعاتُ عملٍ تُعرض بكسر: toFixed على متغيّر ساعات، أو '%.Nf'|format له.
DECIMAL_HOURS = re.compile(
    r"(?:(?:work_hours|total_work_hours|actual_hours|required_hours|ot_[a-z]*hours|"
    r"missing_hours|late_hours|hours_worked|totalHours|span_h)\w*\)?\.toFixed\()"
    r"|(?:['\"]%\.\d+f['\"]\s*\|\s*format\([^)]*(?:hours|span_h))")

EXEMPT_FILES = set()   # لا استثناء — كشفُ العمالة بالدقائق كذلك (طلبُ صاحب المنتج)


def test_no_template_shows_worked_hours_as_a_decimal():
    bad = []
    for dirpath, _, files in os.walk(TEMPLATES):
        for f in files:
            if not f.endswith(('.html', '.txt')) or f in EXEMPT_FILES:
                continue
            text = open(os.path.join(dirpath, f), encoding='utf-8').read()
            for m in DECIMAL_HOURS.finditer(text):
                line = text[:m.start()].count('\n') + 1
                bad.append(f'{f}:{line}: {m.group(0)}')
    assert not bad, '\n'.join(bad)


def test_the_guard_catches_the_old_forms():
    """الحارسُ يرى الصيغ القديمة فعلًا — لا يمرّ لأنه لا يطابق شيئًا."""
    for old in ("record.work_hours.toFixed(1)", "totalHours.toFixed(1)",
                "'%.1f'|format(r.ot_normal_hours)", '"%.1f"|format(data.total_work_hours)'):
        assert DECIMAL_HOURS.search(old), old


def test_the_anomaly_message_reads_hours_and_minutes():
    src = open(os.path.join(ROOT, 'utils', 'salary_utils.py'), encoding='utf-8').read()
    assert "hours_hm(extreme['work_hours'])" in src
    assert ":.1f} ساعة" not in src


def test_the_manpower_sheet_is_hours_and_minutes_too():
    src = open(os.path.join(ROOT, 'routes', 'manpower_routes.py'), encoding='utf-8').read()
    assert 'HOURS_COLS = (9, 13, 17)' in src and 'EXCEL_HOURS_FORMAT' in src
    tpl = open(os.path.join(TEMPLATES, 'manpower_report_print.html'), encoding='utf-8').read()
    assert 'r.ot_normal_hours|hm' in tpl and "'%.1f'|format(r.ot_normal_hours)" not in tpl


def test_excel_hours_are_real_time_values():
    from utils.timefmt import excel_hours, EXCEL_HOURS_FORMAT
    assert excel_hours(12.5) == pytest.approx(12.5 / 24)
    assert EXCEL_HOURS_FORMAT == '[h]:mm', 'بلا [h] تُقرأ 185 ساعة 17:00'


def test_the_manpower_excel_file_itself(tmp_path, monkeypatch):
    """الملفُّ نفسُه، لا المصدر: عمودا الساعات وقتٌ [h]:mm، والمبالغُ كما كانت."""
    import importlib
    import io
    from openpyxl import load_workbook
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ != 'check_license_globally']
    import routes.manpower_routes as M
    row = {'item': 1, 'sl_no': 1, 'name': 'X', 'designation': 'Tech', 'monthly_salary': 300,
           'daily_salary': 11.5385, 'days_present': 26, 'basic_total': 300,
           'ot_normal_hours': 12.5, 'ot_normal_rate': 1.8029, 'ot_normal_pct': 125, 'ot_normal_total': 28.17,
           'ot_friday_hours': 185.52, 'ot_friday_rate': 2.1635, 'ot_friday_pct': 150, 'ot_friday_total': 401.37,
           'ot_holiday_hours': 0, 'ot_holiday_rate': 2.8846, 'ot_holiday_pct': 200, 'ot_holiday_total': 0,
           'ot_total': 429.54, 'grand_total': 729.54}
    rep = {'contract': {'company_name': 'Co', 'contract_no': '1'}, 'month_en': 'August',
           'rows': [row], 'totals': {'grand_total': 729.54}}
    monkeypatch.setattr(M, 'build_report', lambda *a, **k: rep)
    c = A.app.test_client()
    import sqlite3
    uid = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db')).execute(
        "SELECT id FROM users WHERE role='admin'").fetchone()[0]
    with c.session_transaction() as s:
        s['user_id'] = uid
        s['role'] = 'admin'
        s['username'] = 'admin'
    r = c.get('/reports/manpower/export?contract_id=1&month=8&year=2026')
    assert r.status_code == 200, r.status_code
    ws = load_workbook(io.BytesIO(r.data)).active
    data_row = next(i for i in range(1, ws.max_row + 1) if ws.cell(row=i, column=3).value == 'X')
    for col, hours in ((9, 12.5), (13, 185.52), (17, 0)):
        cell = ws.cell(row=data_row, column=col)
        assert cell.number_format == '[h]:mm', (col, cell.number_format)
        # يُقرأ وقتًا: 12:30:00، و185:31 (أكثر من يوم) — لا 12.5.
        import datetime as _dt
        got = cell.value.total_seconds() / 3600 if isinstance(cell.value, _dt.timedelta) else (
            cell.value.hour + cell.value.minute / 60 if isinstance(cell.value, _dt.time) else cell.value * 24)
        assert got == pytest.approx(hours, abs=1 / 60), (col, cell.value)
    assert ws.cell(row=data_row, column=12).value == 28.17, 'المبالغُ لم تتغيّر'
    assert ws.cell(row=data_row, column=10).number_format == '0.000'
