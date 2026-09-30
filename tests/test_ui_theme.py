"""هويّةُ الألوان: تُحمَّل بعد Bootstrap فتغلبه، وألوانُها مقروءة.

كانت الهويّةُ قبل Bootstrap فيغلبها: رؤوسُ البطاقات نصٌّ داكن على الأخضر
الداكن، والأزرارُ زرقاء، وشاراتُ Bootstrap 4 القديمة بيضاء بلا خلفية.
والفحصُ الآليّ للتباين على خمسين شاشة وجد 929 نصًّا دون الحدّ المقروء.

يُشغَّل:  python -m pytest tests/test_ui_theme.py -v
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


def _contrast(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        r, g, bb = map(f, c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * bb
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_the_theme_loads_after_bootstrap():
    s = _read('templates/base.html')
    assert s.index("{% include '_partials/assets.html' %}") < s.index('<style>'), \
        'Bootstrap قبل الهويّة، وإلّا غلبها'


def test_theme_colors_go_through_bootstrap_variables():
    s = _read('templates/base.html')
    for var in ('--bs-primary-rgb', '--bs-danger-rgb', '--bs-info-rgb', '--bs-warning-rgb'):
        assert var in s, 'لونٌ صلب يُسقط الشفافية (bg-opacity) — عبر المتغيّر'
    assert not re.search(r'\.bg-danger\s*\{\s*background-color:\s*var\(--debit\)\s*!important', s)
    for legacy in ('.badge-success', '.badge-info', '.badge-danger', '.badge-light'):
        assert legacy in s, 'شاراتُ Bootstrap 4 في القوالب تحتاج لونًا'
    assert '.card > .card-header' in s and '--bs-card-cap-color: #fff' in s


def test_muted_text_is_readable():
    s = _read('templates/base.html')
    faint = re.search(r'--ink-faint:\s*#([0-9A-Fa-f]{6})', s).group(1)
    for bg in ('FFFFFF', 'FAFAF7'):
        assert _contrast(faint, bg) >= 4.5, f'#{faint} على #{bg}'
    portal = _read('templates/portal/index.html')
    sub = re.search(r'--ios-subtext:\s*#([0-9A-Fa-f]{6})', portal).group(1)
    green = re.search(r'--ios-green:\s*#([0-9A-Fa-f]{6})', portal).group(1)
    assert _contrast(sub, 'F2F2F7') >= 4.5 and _contrast(green, 'FFFFFF') >= 4.5
