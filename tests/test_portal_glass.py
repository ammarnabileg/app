# -*- coding: utf-8 -*-
"""بوابة الموظف بالتصميم الزجاجيّ (٢.٢٦) — ما وُجد معطوبًا أثناء إعادة التصميم.

- الأيقوناتُ في manifest.json لم تكن موجودة: «أضِف إلى الشاشة الرئيسيّة»
  بلا أيقونة، وأندرويد لا يَعدّ البوابةَ تطبيقًا قابلًا للتثبيت.
- «رحلة اليوم» بلا رجوع: مثبَّتةً على الشاشة لا زرَّ رجوعٍ للمتصفّح، فيعلق
  المندوبُ فيها.
- التاريخُ بالإنجليزيّة في واجهةٍ عربيّة («THURSDAY, 01 OCTOBER 2026»).
"""
import json
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding='utf-8').read()


def test_every_manifest_icon_exists_and_is_the_size_it_claims():
    from PIL import Image
    m = json.loads(_read('static', 'portal', 'manifest.json'))
    assert m['icons'], 'لا أيقونات'
    purposes = set()
    for ic in m['icons']:
        path = os.path.join(ROOT, ic['src'].lstrip('/'))
        assert os.path.isfile(path), f'أيقونةٌ مُعلنة غير موجودة: {ic["src"]}'
        w, h = Image.open(path).size
        assert f'{w}x{h}' == ic['sizes'], (ic['src'], w, h)
        purposes.add(ic['purpose'])
    assert {'any', 'maskable'} <= purposes
    assert os.path.isfile(os.path.join(ROOT, 'static', 'portal', 'apple-touch-icon.png'))


def test_both_portal_pages_load_the_glass_design():
    for page in ('index.html', 'trip.html'):
        html = _read('templates', 'portal', page)
        assert "filename='portal/glass.css'" in html, page
        # بعد أنماط الصفحة لا قبلها — وإلا علتها القديمة.
        assert html.index('portal/glass.css') > html.index("_partials/assets.html"), page


def test_the_trip_page_leads_back_to_the_portal():
    html = _read('templates', 'portal', 'trip.html')
    assert re.search(r'<a href="/portal/dashboard" class="trip-back"', html)


def test_the_date_is_written_in_arabic():
    from routes.portal_routes import arabic_date
    assert arabic_date(date(2026, 10, 1)) == 'الخميس، 1 أكتوبر 2026'
    assert arabic_date(date(2026, 1, 4)) == 'الأحد، 4 يناير 2026'


def _lum(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_secondary_text_stays_readable_on_the_glass_background():
    css = _read('static', 'portal', 'glass.css')
    val = lambda name: re.search(rf'{name}:\s*(#[0-9A-Fa-f]{{6}})', css).group(1)
    assert _ratio(val('--ios-subtext'), val('--ios-bg')) >= 4.5
    assert _ratio(val('--ios-text'), val('--ios-bg')) >= 7


def test_the_service_worker_caches_local_files_and_a_new_version():
    sw = _read('static', 'portal', 'sw.js')
    v = re.search(r"portal-cache-v(\d+)", sw)
    assert v and int(v.group(1)) >= 2, 'بلا رفع الإصدار يبقى التصميمُ القديم في ذاكرة الهاتف'
    assert '/static/portal/glass.css' in sw
    assert 'cdn.jsdelivr.net' not in sw and 'fonts.googleapis.com' not in sw


# ------------------------------------------------ تثبيتُ التطبيق (٢.٢٧)

def test_the_service_worker_is_served_from_the_portal_so_it_controls_it(tmp_path, monkeypatch):
    """من /static/portal/sw.js لا يتحكّم إلا في /static/portal/ — فلا تثبيت."""
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import importlib
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import app as A
    r = A.app.test_client().get('/portal/sw.js')
    assert r.status_code == 200
    assert 'javascript' in r.headers['Content-Type']
    assert r.headers.get('Cache-Control') == 'no-cache'
    assert b'portal-cache-v' in r.data
    html = _read('templates', 'portal', 'index.html')
    assert "register('/portal/sw.js', { scope: '/portal/' })" in html
    assert "register('/static/portal/sw.js'" not in html


def test_the_install_prompt_shows_once_and_never_when_installed():
    html = _read('templates', 'portal', 'index.html')
    for piece in ('id="installOverlay"', 'id="installIos"', 'id="installOther"', 'id="installAuto"',
                  'id="pwaInstallCard"', "beforeinstallprompt", "appinstalled",
                  "(display-mode: standalone)", "navigator.standalone"):
        assert piece in html, piece
    # مرّةً واحدة: يُعلَّم قبل أن يُعرض، ولا يُعرض إن كان معلَّمًا إلا بطلبٍ صريح (البطاقة).
    i = html.index('function openInstallSheet(force)')
    body = html[i:i + 600]
    assert "if (pwaStandalone()) return;" in body
    assert "!force && (pwaGet(PWA_SEEN)" in body
    assert body.index('pwaSet(PWA_SEEN') < body.index("style.display = 'flex'")


def test_logout_is_not_painted_as_a_submit_button():
    assert 'btn-ios-submit btn-logout' in _read('templates', 'portal', 'index.html')
    assert '.btn-logout' in _read('static', 'portal', 'glass.css')
