# -*- coding: utf-8 -*-
"""زرّ النشر (publish/PUBLISH.bat → publish.ps1) — ما يكسره بصمت لو تغيّر.

- PowerShell 5.1 (الويندوز) يقرأ ملفًّا بلا BOM بترميز النظام، فيتحوّل العربيّ رموزًا
  ويسقط السكربت قبل أن يبدأ.
- دالةٌ اسمُها `Git` تُخفي أمر git نفسه (الأسماء بلا حالة) فتنادي نفسَها إلى الأبد.
- ملفُّ .bat يُقرأ بترميز الويندوز قبل `chcp 65001`، فيبقى ASCII.
- والحمايةُ: قواعدُ البيانات والمفاتيح لا تُرفع.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PS1 = os.path.join(ROOT, 'publish', 'publish.ps1')
BAT = os.path.join(ROOT, 'publish', 'PUBLISH.bat')


def test_the_script_has_a_bom_for_windows_powershell():
    with open(PS1, 'rb') as f:
        assert f.read(3) == b'\xef\xbb\xbf'


def test_no_function_shadows_the_git_command():
    src = open(PS1, encoding='utf-8-sig').read()
    assert not re.search(r'(?im)^\s*function\s+git\s*[\(\{]', src)
    assert 'function Invoke-Git' in src


def test_the_launcher_is_plain_ascii_and_bypasses_policy():
    raw = open(BAT, 'rb').read()
    raw.decode('ascii')
    assert b'-ExecutionPolicy Bypass' in raw and b'publish.ps1' in raw


def test_databases_keys_and_live_payment_keys_are_blocked():
    src = open(PS1, encoding='utf-8-sig').read()
    for pat in (r"'\.db$'", r"\.local\.php$", r"\.sqlite3?$", r"(^|/)\.env", r"sk_live_"):
        assert pat in src, pat


def test_it_pushes_main_and_the_version_branch_that_triggers_the_release():
    src = open(PS1, encoding='utf-8-sig').read()
    assert "'HEAD:main'" in src and '"HEAD:refs/heads/v$new"' in src
    wf = open(os.path.join(ROOT, '.github', 'workflows', 'build-windows.yml'), encoding='utf-8').read()
    assert "branches: ['v*.*.*.*']" in wf and 'gh release create' in wf
    # الـRelease بعد اختبار التشغيل لا قبله.
    assert wf.index('name: Smoke test') < wf.index('name: GitHub Release')
