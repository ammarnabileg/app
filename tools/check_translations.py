#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""فحص مفاتيح الترجمة — Translation Key Check

    python3 tools/check_translations.py

يمنع الخطأ الذي يظهر للمستخدم كنص خام مثل `x.err_reason_required` بدل رسالة
مفهومة. هذا النوع من الأخطاء لا يكسر شيئًا تقنيًا — الصفحة تعمل، والاختبار
الوظيفي ينجح — لكنه يجعل النظام يبدو غير مكتمل في اللحظة التي يقرأ فيها
المستخدم رسالة خطأ، أي في أسوأ لحظة ممكنة.

يفحص:
  1. مفاتيح مستخدمة في القوالب أو الكود وغير معرَّفة في ملف الترجمة
  2. مفاتيح معرَّفة بترجمة فارغة (تظهر كمفتاح خام أيضًا)
  3. اختلاف المفاتيح بين العربية والإنجليزية

رمز الخروج 0 = سليم، 1 = يوجد مفتاح سيظهر خامًا للمستخدم.
"""
import glob
import os
import re
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FAIL, WARN = '\033[92m✓\033[0m', '\033[91m✗\033[0m', '\033[93m!\033[0m'


def collect_used():
    """كل مفتاح يُطلب فعليًا من طبقة الترجمة."""
    used = {}
    patterns = [
        (r"_\(\s*['\"]([^'\"]+)['\"]\s*\)", 'templates/**/*.html'),
        (r"gettext\(\s*['\"]([^'\"]+)['\"]\s*\)", 'routes/*.py'),
        (r"gettext\(\s*['\"]([^'\"]+)['\"]\s*\)", 'utils/*.py'),
    ]
    for pat, glb in patterns:
        for f in glob.glob(os.path.join(APP_DIR, glb), recursive=True):
            src = open(f, encoding='utf-8').read()
            for k in re.findall(pat, src):
                if k.strip():
                    used.setdefault(k, set()).add(os.path.relpath(f, APP_DIR))
    return used


def collect_defined(lang):
    """المفاتيح المعرَّفة، والمعرَّفة بترجمة فارغة."""
    p = os.path.join(APP_DIR, 'translations', lang, 'LC_MESSAGES', 'messages.po')
    if not os.path.isfile(p):
        return set(), set()
    src = open(p, encoding='utf-8').read()
    # تجاهل المدخلات المُعطَّلة (#~) فهي غير فعّالة
    live = '\n'.join(ln for ln in src.split('\n') if not ln.startswith('#~'))
    defined, empty = set(), set()
    # gettext يسمح بترجمة موزّعة على أسطر متتابعة بعد msgstr "":
    #     msgstr ""
    #     "الجزء الأول "
    #     "والجزء الثاني"
    # فقراءة سطر msgstr وحده تصنّف ترجمة سليمة كأنها فارغة — وهو تحديدًا
    # الإنذار الكاذب الذي يجعل أداة فحص كهذه تُهمَل بعد أول تشغيل.
    for m in re.finditer(r'^msgid "([^"]+)"\s*\nmsgstr "([^"]*)"((?:\s*\n"[^"]*")*)',
                         live, re.M):
        key = m.group(1)
        val = m.group(2) + ''.join(re.findall(r'"([^"]*)"', m.group(3) or ''))
        defined.add(key)
        if not val.strip():
            empty.add(key)
    return defined, empty


def main():
    used = collect_used()
    ar_def, ar_empty = collect_defined('ar')
    en_def, en_empty = collect_defined('en')

    print(f'مفاتيح مستخدمة: {len(used)} | معرَّفة (ar): {len(ar_def)} '
          f'| (en): {len(en_def)}')

    problems = 0

    missing_ar = sorted(k for k in used if k not in ar_def)
    if missing_ar:
        print(f'\n{FAIL} غير معرَّفة في العربية ({len(missing_ar)}) — ستظهر خامًا:')
        for k in missing_ar:
            print(f'    {k}  ←  {", ".join(sorted(used[k]))}')
        problems += len(missing_ar)
    else:
        print(f'\n{OK} كل المفاتيح المستخدمة معرَّفة في العربية')

    missing_en = sorted(k for k in used if k not in en_def)
    if missing_en:
        print(f'\n{FAIL} غير معرَّفة في الإنجليزية ({len(missing_en)}):')
        for k in missing_en:
            print(f'    {k}')
        problems += len(missing_en)
    else:
        print(f'{OK} كل المفاتيح المستخدمة معرَّفة في الإنجليزية')

    empty_used = sorted(k for k in used if k in (ar_empty | en_empty))
    if empty_used:
        print(f'\n{FAIL} معرَّفة بترجمة فارغة ({len(empty_used)}) — تظهر خامًا:')
        for k in empty_used:
            langs = [l for l, s in (('ar', ar_empty), ('en', en_empty)) if k in s]
            print(f'    {k}  ({", ".join(langs)})')
        problems += len(empty_used)
    else:
        print(f'{OK} لا مفاتيح مستخدمة بترجمة فارغة')

    drift = (ar_def ^ en_def) & set(used)
    if drift:
        print(f'\n{WARN} موجودة في لغة وغائبة في الأخرى ({len(drift)}):')
        for k in sorted(drift):
            print(f'    {k} — {"ar فقط" if k in ar_def else "en فقط"}')

    mo_missing = [l for l in ('ar', 'en') if not os.path.isfile(
        os.path.join(APP_DIR, 'translations', l, 'LC_MESSAGES', 'messages.mo'))]
    if mo_missing:
        print(f'\n{FAIL} ملف .mo غير مُصرَّف للغات: {mo_missing}')
        print('    شغّل تصريف الترجمات قبل البناء')
        problems += 1

    print(f'\n{"=" * 60}')
    if problems:
        print(f'{FAIL} {problems} مفتاح سيظهر خامًا للمستخدم — أصلحها قبل البناء')
        return 1
    print(f'{OK} الترجمات سليمة')
    return 0


if __name__ == '__main__':
    sys.exit(main())
