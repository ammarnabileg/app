"""الساعاتُ «ساعات:دقائق» في كل التقارير — لا 8.85 تُقرأ «8 ساعات و85 دقيقة».

ثلاثُ طبقات، وكلٌّ لها اختبار:
  * بايثون والقوالب: `utils/timefmt.hours_hm` (ومرشِّح `hm`).
  * المتصفّح: `fmtHM` في <head> القالب الأساسيّ — قبل أيّ سكربتٍ يستعمله.
  * كشفُ العمالة للوزارة وحده يبقى عشريًّا — عن قصد، وله اختبار.
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

# كشفُ الوزارة (العمالة) يبقى عشريًّا عن قصد: جهةٌ رسميّة تراجعه بصيغته،
# وفيه «ساعات × سعر الساعة = الإجمالي» — 12:30 تُحسب عندها 12.30 × السعر.
EXEMPT_FILES = {'manpower_report.html', 'manpower_report_print.html'}


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


def test_the_ministry_sheet_keeps_decimal_hours():
    """الاستثناءُ الوحيد، ومقصود: كشفُ العمالة يُراجَع «ساعات × سعر = إجمالي»."""
    src = open(os.path.join(ROOT, 'routes', 'manpower_routes.py'), encoding='utf-8').read()
    assert 'excel_hours' not in src and 'EXCEL_HOURS_FORMAT' not in src
    tpl = open(os.path.join(TEMPLATES, 'manpower_report_print.html'), encoding='utf-8').read()
    assert "'%.1f'|format(r.ot_normal_hours)" in tpl
