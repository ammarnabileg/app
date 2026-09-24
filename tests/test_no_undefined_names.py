"""لا اسمَ غيرَ معرَّف في كود المنتج.

تعديلٌ جماعيّ قديم استبدل `x_conn.close()` بـ`x_pass # ...` في أربعة مواضع —
أسماءٌ غير معرَّفة ترمي NameError عند التشغيل، ويبتلعها `except` فلا يراها
أحد: مزامنةُ مستخدمي أجهزة ADMS لم تُضف موظفًا قطّ، وطابورُ Oracle يسجّل
«خطأً» كلَّ نبضة. لا يكشفها إلا تحليلٌ ساكن — فهذا هو.

يُشغَّل:  python -m pytest tests/test_no_undefined_names.py -v
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_product_code_has_no_undefined_names():
    pyflakes = pytest.importorskip('pyflakes')
    files = subprocess.run(['git', 'ls-files', '*.py'], cwd=ROOT,
                           capture_output=True, text=True).stdout.split()
    files = [f for f in files if not f.startswith('tests/')]
    assert files, 'لا ملفّات — git ls-files لم يعمل'
    r = subprocess.run([sys.executable, '-m', 'pyflakes', *files], cwd=ROOT,
                       capture_output=True, text=True)
    bad = [l for l in (r.stdout + r.stderr).splitlines() if 'undefined name' in l]
    assert not bad, '\n'.join(bad)
