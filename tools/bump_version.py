#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ترقية الإصدار — Version Bump

    python3 tools/bump_version.py feature "دعم الشفت المقسّم"
    python3 tools/bump_version.py fix     "إصلاح احتساب ساعات الشفت المقسّم"
    python3 tools/bump_version.py build   "بناء للمراجعة"

## لماذا أداة لا اتفاق

الترقيم اليدوي يُنسى. وحين يُنسى تنشأ حالة أسوأ من غياب الترقيم أصلًا:
عميل يحمل رقمًا لا يطابق ما يشغّله، فيصبح رقم الإصدار مضلِّلًا في الدعم
الفني بدل أن يكون مرجعًا. وهذا ما حدث فعلًا في هذا المشروع: كان الإصدار
2.5.0 وقد أُضيفت إليه عشرات التغييرات بلا تحريك رقم واحد.

فالأداة تحرّك ثلاثة أشياء معًا — الرقم، وسجل التغييرات، والكوميت — ولا
تسمح بتحريك واحد دون الآخرين.

## القواعد

    feature  →  MINOR ↑ ثم PATCH = 0 وBUILD = 0
    fix      →  PATCH ↑ ثم BUILD = 0
    build    →  BUILD ↑ وحده
    major    →  MAJOR ↑ ثم الباقي = 0   (يستوجب تأكيدًا)

## ما تفحصه قبل الترقية

- أن شجرة العمل ليست نظيفة: ترقية بلا تغيير ترفع رقمًا بلا سبب
- أن الترجمات سليمة: مفتاح خام يظهر للمستخدم أسوأ من تأخير إصدار
- أن الرقم الجديد أكبر من القديم فعلًا (لا تراجع يعلّق العملاء)
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import date

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(APP_DIR, 'utils', 'version_info.py')
CHANGELOG = os.path.join(APP_DIR, 'CHANGELOG.md')

OK, FAIL, WARN = '\033[92m✓\033[0m', '\033[91m✗\033[0m', '\033[93m!\033[0m'

KIND_AR = {'major': 'إصدار رئيسي', 'feature': 'ميزة',
           'fix': 'إصلاح', 'build': 'بناء'}
SECTION_AR = {'major': '### تغييرات جذرية', 'feature': '### ميزات',
              'fix': '### إصلاحات', 'build': '### بناء'}


def read_version():
    src = open(VERSION_FILE, encoding='utf-8').read()
    out = {}
    for part in ('MAJOR', 'MINOR', 'PATCH', 'BUILD'):
        m = re.search(r'^VERSION_%s\s*=\s*(\d+)' % part, src, re.M)
        if not m:
            raise SystemExit(f'{FAIL} VERSION_{part} غير موجود في version_info.py')
        out[part] = int(m.group(1))
    return out


def write_version(v, release_name=None):
    src = open(VERSION_FILE, encoding='utf-8', newline='').read()
    for part in ('MAJOR', 'MINOR', 'PATCH', 'BUILD'):
        src = re.sub(r'^VERSION_%s\s*=\s*\d+' % part,
                     f'VERSION_{part} = {v[part]}', src, count=1, flags=re.M)
    src = re.sub(r'^BUILD_DATE\s*=\s*"[^"]*"',
                 f'BUILD_DATE = "{date.today().isoformat()}"',
                 src, count=1, flags=re.M)
    if release_name:
        safe = release_name.replace('"', "'")
        src = re.sub(r'^RELEASE_NAME\s*=\s*"[^"]*"',
                     f'RELEASE_NAME = "{safe}"', src, count=1, flags=re.M)
    open(VERSION_FILE, 'w', encoding='utf-8', newline='').write(src)


def bump(v, kind):
    v = dict(v)
    if kind == 'major':
        v['MAJOR'] += 1
        v['MINOR'] = v['PATCH'] = v['BUILD'] = 0
    elif kind == 'feature':
        v['MINOR'] += 1
        v['PATCH'] = v['BUILD'] = 0
    elif kind == 'fix':
        v['PATCH'] += 1
        v['BUILD'] = 0
    else:
        v['BUILD'] += 1
    return v


def vstr(v):
    return f"{v['MAJOR']}.{v['MINOR']}.{v['PATCH']}.{v['BUILD']}"


def vtuple(v):
    return (v['MAJOR'], v['MINOR'], v['PATCH'], v['BUILD'])


def update_changelog(new_v, kind, message, release_name):
    """إضافة السطر إلى سجل التغييرات.

    إن كان للإصدار قسمٌ قائم، يُضاف السطر إليه؛ وإلا أُنشئ قسم جديد. هذا
    يسمح بتجميع عدّة إصلاحات تحت إصدار واحد بدل قسمٍ لكل سطر.
    """
    if not os.path.isfile(CHANGELOG):
        return False
    text = open(CHANGELOG, encoding='utf-8').read()
    header = f'## {vstr(new_v)}'
    line = f'- {message}'
    section = SECTION_AR[kind]

    if header in text:
        i = text.find(header)
        nxt = text.find('\n## ', i + 1)
        block = text[i:nxt if nxt > 0 else len(text)]
        if section in block:
            j = block.find(section) + len(section)
            new_block = block[:j] + '\n\n' + line + block[j:].lstrip('\n')
            new_block = re.sub(r'\n{3,}', '\n\n', new_block)
        else:
            new_block = block.rstrip() + f'\n\n{section}\n\n{line}\n'
        text = text[:i] + new_block + (text[nxt:] if nxt > 0 else '')
    else:
        entry = (f'\n## {vstr(new_v)} — {date.today().isoformat()}'
                 + (f' · «{release_name}»' if release_name else '')
                 + f'\n\n{section}\n\n{line}\n\n---\n')
        marker = '\n---\n'
        k = text.find(marker, text.find('القاعدة') if 'القاعدة' in text else 0)
        if k > 0:
            k += len(marker)
            text = text[:k] + entry + text[k:]
        else:
            text = text.rstrip() + '\n' + entry
    open(CHANGELOG, 'w', encoding='utf-8').write(text)
    return True


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=APP_DIR, capture_output=True,
                          text=True, **kw)


def main():
    ap = argparse.ArgumentParser(description='ترقية الإصدار')
    ap.add_argument('kind', choices=['major', 'feature', 'fix', 'build'])
    ap.add_argument('message', help='وصف التغيير — يدخل سجل التغييرات')
    ap.add_argument('--release-name', help='اسم الإصدار (يُعرض في التذييل)')
    ap.add_argument('--no-commit', action='store_true',
                    help='حدّث الملفات بلا كوميت')
    ap.add_argument('--skip-checks', action='store_true',
                    help='تخطّي الفحوص — لا يُستعمل قبل تسليم')
    args = ap.parse_args()

    cur = read_version()
    new = bump(cur, args.kind)

    print(f'{KIND_AR[args.kind]}: {vstr(cur)} → {vstr(new)}')
    print(f'  {args.message}\n')

    if vtuple(new) <= vtuple(cur):
        raise SystemExit(f'{FAIL} الرقم الجديد ليس أكبر — تراجعٌ يعلّق العملاء')

    if args.kind == 'major':
        print(f'{WARN} إصدار رئيسي: يعني تغييرًا قد لا يتوافق مع ما قبله.')
        if input('    متأكد؟ اكتب "نعم": ').strip() != 'نعم':
            raise SystemExit('أُلغي')

    if not args.skip_checks:
        st = run(['git', 'status', '--porcelain'])
        if not st.stdout.strip():
            print(f'{WARN} لا تغييرات غير محفوظة — ترقية بلا سبب؟')
            if input('    المتابعة؟ (y/N): ').strip().lower() != 'y':
                raise SystemExit('أُلغي')

        chk = os.path.join(APP_DIR, 'tools', 'check_translations.py')
        if os.path.isfile(chk):
            r = run([sys.executable, chk])
            if r.returncode != 0:
                print(f'{FAIL} فحص الترجمات فشل — مفتاح خام سيظهر للمستخدم:')
                print(r.stdout[-600:])
                raise SystemExit(1)
            print(f'{OK} الترجمات سليمة')

    write_version(new, args.release_name)
    print(f'{OK} version_info.py → {vstr(new)}')

    if update_changelog(new, args.kind, args.message, args.release_name):
        print(f'{OK} CHANGELOG.md')

    if args.no_commit:
        print(f'\n{WARN} بلا كوميت (--no-commit). راجع ثم احفظ يدويًّا.')
        return 0

    run(['git', 'add', '-A'])
    body = (f'{args.message}\n\n'
            f'{KIND_AR[args.kind]}: {vstr(cur)} → {vstr(new)}')
    c = run(['git', 'commit', '-F', '-'], input=body)
    if c.returncode != 0:
        print(f'{FAIL} فشل الكوميت:\n{c.stderr[-300:]}')
        return 1
    print(f'{OK} كوميت: {vstr(new)}')
    print(f'\nللرفع:  git push origin main')
    return 0


if __name__ == '__main__':
    sys.exit(main())
