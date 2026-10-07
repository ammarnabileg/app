# -*- coding: utf-8 -*-
"""«حفظ» في ملف الموظّف كان يقف: «في تاريخ الراتب بيانات ناقصة» (٢.٣٢.٢).

خانتا «إضافة راتب/شفت» في تبويب التاريخ تتبعان نموذجًا آخر (form="histSalaryForm")
وعليهما required — والفحصُ قبل الحفظ كان يمرّ على كلّ حقلٍ داخل النموذج لا على حقوله
هو، فيجد «الراتب» فارغًا ويمنع الحفظ. الصحيح: `form.elements` — ما يملكه النموذج وحده.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    return open(os.path.join(ROOT, 'templates', name), encoding='utf-8').read()


def test_the_save_check_looks_only_at_the_forms_own_fields():
    for name in ('edit_employee.html', 'add_employee.html'):
        src = _read(name)
        assert "form.querySelectorAll('input, select, textarea')" not in src, name
        assert 'Array.from(form.elements)' in src, name


def test_the_history_inputs_really_belong_to_other_forms():
    src = _read('edit_employee.html')
    assert re.search(r'name="salary" form="histSalaryForm" required', src)
    assert 'id="histSalaryForm"' in src
