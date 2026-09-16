"""هل يغطّي التطبيق ما تغطّيه البوابة؟

نقلتُ أقسام البوابة إلى التطبيق بالقراءة، فنسيتُ تبويبة «حسابي»،
وأرسلتُ `permission` حيث تُرسل الشاشة `personal`، ولم أُرسل
`punch_type` أصلًا. ثلاثتها وجدها **جردٌ** للصفحة لا قراءةٌ ثانية
لها — ولذا صار الجرد اختبارًا.

فما يُقارَن هنا يُستخرج من الملفّين نفسيهما لا من قائمتين أكتبهما:
`templates/portal/index.html` و`field_app/lib/`. ومتى أُضيف قسمٌ
للبوابة ولم يُضف للتطبيق، سقط هذا.

يُشغَّل:  python -m pytest tests/test_app_covers_portal.py -v
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(ROOT, 'templates', 'portal', 'index.html')
DART_DIR = os.path.join(ROOT, 'field_app', 'lib')


def _portal_html():
    with open(PORTAL, encoding='utf-8') as f:
        return f.read()


def _dart_source():
    out = []
    for base, _dirs, files in os.walk(DART_DIR):
        for f in files:
            if f.endswith('.dart'):
                with open(os.path.join(base, f), encoding='utf-8') as fh:
                    out.append(fh.read())
    return '\n'.join(out)


# تبويبة البوابة ← الشاشة التي تقابلها في التطبيق
TAB_TO_SCREEN = {
    'tab-today': 'home_screen.dart',
    'tab-punch': 'punch_screen.dart',
    'tab-attendance': 'attendance_screen.dart',
    'tab-leaves': 'requests_screen.dart',
    'tab-team': 'team_screen.dart',
    'tab-profile': 'profile_screen.dart',
}


def test_every_portal_tab_has_a_screen():
    """تبويبة «حسابي» سقطت مني هنا بالضبط."""
    tabs = set(re.findall(r"switchTab\('(tab-[a-z]+)'", _portal_html()))
    assert tabs, 'لم تُستخرج تبويبات — الاستخراج معطوب'

    unknown = tabs - set(TAB_TO_SCREEN)
    assert not unknown, (
        'تبويبات في البوابة لا يعرفها هذا الاختبار — أُضيفت ولم تُنقل؟ ' +
        ', '.join(sorted(unknown)))

    missing = [
        f'{tab} → {TAB_TO_SCREEN[tab]}'
        for tab in sorted(tabs)
        if not os.path.exists(os.path.join(DART_DIR, 'screens', TAB_TO_SCREEN[tab]))
    ]
    assert not missing, 'أقسامٌ في البوابة بلا شاشة في التطبيق:\n  ' + '\n  '.join(missing)


def test_excuse_types_match_the_portal_exactly():
    """القيمة تُكتب في القاعدة كما تصل — والخادم لا يفحصها.

    فقيمةٌ يكتبها التطبيق وحده تسقط من كل تصفيةٍ أو تقرير مبنيّ على
    قيم الشاشة، ولا شيء يشتكي.
    """
    html = _portal_html()
    block = re.search(r'name="type"(.*?)</select>', html, re.S)
    assert block, 'لم يُعثر على قائمة أنواع الاستئذان في البوابة'
    portal_types = set(re.findall(r'value="([a-z_]+)"', block.group(1)))
    assert portal_types, 'لم تُستخرج أنواع — الاستخراج معطوب'

    dart = _dart_source()
    app_types = set(re.findall(r"'([a-z_]+)': '[^']*'", 
                               re.search(r'_kinds = \{(.*?)\};', dart, re.S).group(1)))

    assert app_types == portal_types, (
        f'أنواع الاستئذان تفترق.\n  البوابة: {sorted(portal_types)}'
        f'\n  التطبيق: {sorted(app_types)}')


def test_punch_types_match_the_portal_exactly():
    """بلا `punch_type` يستنتج الخادم النوع من عدد بصمات اليوم،
    فبصمةٌ زائدة تقلب الباقي."""
    portal_modes = set(re.findall(r"setPunchMode\('([a-z_]+)'\)", _portal_html()))
    assert portal_modes, 'لم تُستخرج أنواع البصمة'

    dart = _dart_source()
    block = re.search(r'_modes = \{(.*?)\};', dart, re.S)
    assert block, 'التطبيق لا يعرّف أنواع بصمة إطلاقًا'
    app_modes = set(re.findall(r"'([a-z_]+)':", block.group(1)))

    assert app_modes == portal_modes, (
        f'أنواع البصمة تفترق.\n  البوابة: {sorted(portal_modes)}'
        f'\n  التطبيق: {sorted(app_modes)}')
    assert 'punch_type' in dart, 'التطبيق لا يرسل punch_type أصلًا'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
