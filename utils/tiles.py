"""خلفية الخريطة بلا إنترنت: مربّعات محفوظة لمناطق العميل وحدها.

المكتبات أُنزلت إلى static/vendor فصارت الخريطة تعمل بلا اتصال —
العلامات والخطوط والمناطق تُرسم — وبقيت **الخلفية** رمادية، لأن
مربّعاتها صورٌ للعالم لا تُحمل في التركيب.

والحلّ هنا ليس حمل العالم: العميل يرسم مناطقه، ومنطقةُ مندوبٍ حيٌّ لا
بلد. فتُحفظ مربّعات تلك الرقعة وحدها — عشرات الميغابايت لا مئاتها.

وطريقان بحسب حال التركيب، وكلاهما يكتب في المخزن نفسه:

  * **متّصل:** ما يعرضه المدير يُحفظ وهو يُعرض (وسيطٌ يخزّن)، ويمكن
    تنزيل رقعة منطقةٍ دفعةً واحدة قبل انقطاع متوقَّع.
  * **منفصل تمامًا:** لا الخادم نفسه يصل إلى الإنترنت. فتُصدَّر الرقعة
    من تركيبٍ متّصل ملفًّا واحدًا، ويُستورَد عند العميل. وهذا هو الحال
    الذي لا يحلّه أي وسيط.

**عن سياسة OpenStreetMap:** خدمتهم تطوّعية وشروطها تمنع التنزيل
الجملي. فالتنزيل هنا محدود بسقف صريح، وبمهلة بين الطلبات، وباسم
تعريف صحيح — ولمناطق العميل لا لبلد. ومن أراد رقعةً أوسع فمكانه مزوّد
تجاري أو خادم مربّعات خاص، وهذا مكتوب في الشاشة لا في الشيفرة وحدها.
"""

import math
import os
import shutil
import threading
import time
import zipfile

from utils.db import DB_PATH

DEFAULT_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
DEFAULT_ATTRIBUTION = '© OpenStreetMap'

SETTING_URL = 'map_tile_url'
SETTING_ATTR = 'map_tile_attribution'
SETTING_CACHE = 'map_tile_cache_allowed'

# اسم تعريف صحيح شرطٌ في سياستهم؛ والمجهول يُحجب.
USER_AGENT = 'OnPointHR-FieldModule/1.0 (+https://onz.one)'

# سقفان لا واحد، والفرق بينهما ليس تقنيًّا بل قانونيّ:
#
# خوادم OpenStreetMap تطوّعية، وسياستهم تمنع «التنزيل الجملي» صراحةً.
# فتصفّحٌ عاديّ يخزّن ما يُعرض مقبول، وسحبُ رقعةٍ كبيرة ليس كذلك. ولذا
# السقف على المصدر الافتراضي منخفض عمدًا: يكفي حيًّا أو حيّين، ويمنع
# سحب بلدٍ سهوًا أو قصدًا.
#
# ومن يضبط مصدرًا خاصًّا — مزوّدًا تجاريًّا اشترك فيه، أو خادم مربّعات
# يملكه — فالرخصة رخصته والسقف سقفه، فيُرفع.
MAX_TILES_OSM = 4000
MAX_TILES_CUSTOM = 200000

# مهلة بين الطلبات: خدمتهم تطوّعية، والاندفاع عليها يُحجب ويستحقّ
# الحجب.
DELAY_SECONDS = 0.35

# التقريبات المفيدة: ١١ يُري المدينة، و١٦ يُري الشوارع بأسمائها.
MIN_ZOOM, MAX_ZOOM = 11, 16

_state = {'running': False, 'done': 0, 'total': 0, 'message': '', 'stopped': False}
_lock = threading.Lock()


def tile_source(conn=None):
    """(الرابط، نسب المصدر، أهو الافتراضي؟)."""
    url = attr = None
    try:
        from utils.db import get_db_connection
        conn = conn or get_db_connection()
        rows = conn.execute(
            'SELECT setting_name, setting_value FROM salary_settings_v2 '
            'WHERE setting_name IN (?, ?)', (SETTING_URL, SETTING_ATTR)).fetchall()
        d = {r[0] if not hasattr(r, 'keys') else r['setting_name']:
             r[1] if not hasattr(r, 'keys') else r['setting_value'] for r in rows}
        url = (d.get(SETTING_URL) or '').strip()
        attr = (d.get(SETTING_ATTR) or '').strip()
    except Exception:
        pass

    if not url or '{z}' not in url or '{x}' not in url or '{y}' not in url:
        return DEFAULT_TILE_URL, DEFAULT_ATTRIBUTION, True
    return url, (attr or 'مصدر خرائط خاص'), False


def cache_allowed(conn=None):
    """أيُسمح بحفظ مربّعات هذا المصدر على القرص؟

    المصدر الافتراضي (OpenStreetMap) نعم: حفظُ ما يُعرض تصفّحٌ عادي،
    والتنزيل الجملي وحده ممنوع — ويمنعه السقف.

    والمصدر الخارجي **لا، حتى يقول مالكُ النظام إن رخصته تسمح.** لأن
    شروط المزوّدين التجاريين تختلف: MapTiler Cloud مثلًا يمنع
    التخزين في ذاكرة خادم وسيطة صراحةً. فالافتراض الآمن ألّا يُحفظ،
    ويُشعلها من يملك خادمه أو اشتراكًا يسمح — فتقع المسؤولية حيث
    تُعرف الرخصة، لا على إعدادٍ صامت.
    """
    if tile_source(conn)[2]:
        return True
    try:
        from utils.db import get_db_connection
        conn = conn or get_db_connection()
        row = conn.execute(
            'SELECT setting_value FROM salary_settings_v2 WHERE setting_name = ?',
            (SETTING_CACHE,)).fetchone()
        if not row:
            return False
        v = row[0] if not hasattr(row, 'keys') else row['setting_value']
        return str(v).strip() in ('1', 'on', 'true')
    except Exception:
        return False


def source_host(conn=None):
    """المضيف وحده — بلا مسار ولا استعلام.

    رابط المزوّد يحمل المفتاح في `?key=`، والمفتاح يُدفع ثمنه بالطلبات.
    فما يخرج إلى المتصفح هو ما يكفي للتشخيص: `api.maptiler.com` تقول
    أيّ مزوّد مضبوط ولا تقول بأيّ مفتاح.
    """
    try:
        from urllib.parse import urlparse
        return urlparse(tile_source(conn)[0]).hostname or ''
    except Exception:
        return ''


def max_tiles(conn=None):
    return MAX_TILES_OSM if tile_source(conn)[2] else MAX_TILES_CUSTOM


def cache_dir():
    return os.path.join(os.path.dirname(DB_PATH), 'map_tiles')


def tile_path(z, x, y):
    """مسار المربّع داخل المخزن، أو None لو خرج عنه.

    الأرقام تأتي من عنوان يطلبه المتصفح، فتُفحص أنها أرقام وأنها ضمن
    حدود التقريب — لا تُركَّب في مسار كما وصلت.
    """
    try:
        z, x, y = int(z), int(x), int(y)
    except (TypeError, ValueError):
        return None
    if not (0 <= z <= 19):
        return None
    span = 1 << z
    if not (0 <= x < span) or not (0 <= y < span):
        return None
    return os.path.join(cache_dir(), str(z), str(x), f'{y}.png')


def have(z, x, y):
    p = tile_path(z, x, y)
    return bool(p and os.path.exists(p))


def read(z, x, y):
    p = tile_path(z, x, y)
    if not p or not os.path.exists(p):
        return None
    with open(p, 'rb') as f:
        return f.read()


def store(z, x, y, data):
    p = tile_path(z, x, y)
    if not p or not data:
        return False
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, p)      # لا مربّع نصفه في المخزن
    return True


def build_url(template, z, x, y):
    """(الرابط أو None، السبب أو None) — بلا رمي مهما كان القالب.

    `str.format` كانت ترمي KeyError على `{s}` — وهو أشيع ما يُلصَق،
    لأن أمثلة Leaflet كلها `{s}.tile...`. والرمي كان يُبتلع ويُقال
    «لا اتصال»، وهو الخطأ نفسه الذي أصلحتُه للتوّ في موضع آخر: عطلٌ
    في الإعداد يُعرض عطلًا في الشبكة.

    فالاستبدال صريح لا تنسيق: `{s}` و`{r}` اصطلاحا Leaflet فيُلبَّيان،
    وما بقي بين قوسين بعدها قالبٌ لا نعرفه — فيُقال ذلك بعينه.
    """
    if not template:
        return None, 'bad_url'
    out = (template
           .replace('{s}', 'abc'[(x + y) % 3])     # كما يوزّع Leaflet
           .replace('{r}', '')                      # لاحقة الشاشات الدقيقة
           .replace('{z}', str(z))
           .replace('{x}', str(x))
           .replace('{y}', str(y)))
    if '{' in out or '}' in out:
        return None, 'bad_url'
    return out, None


def fetch_ex(z, x, y, timeout=8, url=None):
    """(البايتات أو None، سببُ الإخفاق أو None).

    السبب ليس زينة. المزوّد المدفوع يرفض بـ403 حين لا يوافق المفتاحُ
    قيودَه، والرفض والانقطاع يبدوان سواءً من هنا: كلاهما «لا مربّع».
    فلو قيل للمستخدم «لا اتصال» وهو متّصل، طارد عطلًا في الشبكة لا
    وجود له، والعطل في سطرٍ بلوحة المزوّد.
    """
    target, bad = build_url(url or tile_source()[0], z, x, y)
    if bad:
        return None, bad
    try:
        import requests
        r = requests.get(target,
                         headers={'User-Agent': USER_AGENT}, timeout=timeout)
        if r.status_code == 200 and r.content[:4] == b'\x89PNG':
            return r.content, None
        if r.status_code in (401, 403):
            return None, 'key'
        if r.status_code == 429:
            return None, 'rate'
        if r.status_code == 200:
            return None, 'not_png'
        return None, 'http_%d' % r.status_code
    except Exception:
        return None, 'network'


def fetch(z, x, y, timeout=8, url=None):
    """يجلب مربّعًا من الإنترنت. يعيد البايتات أو None.

    لا يرمي أبدًا: هذا المسار يُنادى أثناء عرض خريطة، وانقطاع الشبكة
    حالةٌ عادية لا خطأ.
    """
    return fetch_ex(z, x, y, timeout=timeout, url=url)[0]


# ما يُقال للمستخدم عند كل سبب — بالسبب لا بأقرب تخمين.
FAIL_MESSAGES = {
    'key': 'خادم الخرائط رفض المفتاح (403). راجع المفتاح وقيوده في لوحة '
           'المزوّد: طلبات هذا النظام تأتي من **الخادم** لا من المتصفح، '
           'فلا تحمل نطاقًا (Referer) — ومفتاحٌ مقيَّد بنطاقات يرفضها.',
    'rate': 'خادم الخرائط يحدّ من الطلبات (429). أمهله ثم أعد — الموجود '
            'محفوظ ولا يُعاد تنزيله.',
    'not_png': 'الردّ ليس صورة. تأكّد أن الرابط قالب مربّعات ينتهي بـ '
               '{z}/{x}/{y}.png لا صفحة خرائط.',
    'bad_url': 'رابط المصدر فيه قالبٌ غير مفهوم. المطلوب رابط مربّعات '
               'فيه {z} و{x} و{y} — و{s} و{r} مفهومان أيضًا، وما عداهما لا.',
    'network': 'لا اتصال بخادم الخرائط.',
}


# ------------------------------------------------- من المضلَّع إلى المربّعات

def deg2tile(lat, lon, z):
    """إحداثيات → رقم مربّع (إسقاط ويب-مركاتور القياسي)."""
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tiles_for_bbox(min_lat, min_lon, max_lat, max_lon, zmin=MIN_ZOOM, zmax=MAX_ZOOM):
    """قائمة المربّعات التي تغطّي الرقعة عبر التقريبات."""
    out = []
    for z in range(zmin, zmax + 1):
        x1, y1 = deg2tile(max_lat, min_lon, z)     # الركن الشمالي الغربي
        x2, y2 = deg2tile(min_lat, max_lon, z)     # الجنوبي الشرقي
        for x in range(min(x1, x2), max(x1, x2) + 1):
            for y in range(min(y1, y2), max(y1, y2) + 1):
                out.append((z, x, y))
    return out


def estimate(bboxes, zmin=MIN_ZOOM, zmax=MAX_ZOOM):
    """(العدد، الموجود منه) — ليرى المستخدم الحجم قبل أن يبدأ."""
    wanted = set()
    for b in bboxes:
        wanted.update(tiles_for_bbox(*b, zmin=zmin, zmax=zmax))
    present = sum(1 for t in wanted if have(*t))
    return len(wanted), present


# ------------------------------------------------------------- التنزيل

def progress():
    with _lock:
        return dict(_state)


def stop():
    with _lock:
        _state['stopped'] = True


def download(bboxes, zmin=MIN_ZOOM, zmax=MAX_ZOOM, conn=None):
    """ينزّل مربّعات الرقع في خيط. يعيد (بدأ؟، رسالة).

    الموجود لا يُعاد تنزيله: من أوقف ثم أعاد يُكمل من حيث وقف.
    """
    with _lock:
        if _state['running']:
            return False, 'تنزيل جارٍ بالفعل'

    wanted = set()
    for b in bboxes:
        wanted.update(tiles_for_bbox(*b, zmin=zmin, zmax=zmax))
    todo = sorted(t for t in wanted if not have(*t))

    if not todo:
        return False, 'كل المربّعات محفوظة بالفعل'
    url, _attr, is_osm = tile_source(conn)

    # التنزيل المسبق حفظٌ جمليّ — أوضح ما تمنعه شروط المزوّدين. فلا
    # يبدأ أصلًا حين لا يُسمح بالحفظ، ويُقال السبب وموضع تغييره.
    if not cache_allowed(conn):
        return False, (
            'الحفظ مُطفأ لهذا المصدر، والتنزيل المسبق حفظٌ جمليّ. '
            'أكثر المزوّدين التجاريين يمنعونه في شروطهم — ومنهم '
            'MapTiler Cloud صراحةً. فإن كانت رخصتك تسمح (خادم تملكه، '
            'أو اشتراك on-prem)، أشعل «المزوّد يسمح بحفظ المربّعات» '
            'من الإعدادات. وإلا فالمصدر الافتراضي يسمح بالحفظ.')
    cap = MAX_TILES_OSM if is_osm else MAX_TILES_CUSTOM
    if len(todo) > cap:
        if is_osm:
            hours = len(todo) * DELAY_SECONDS / 3600
            return False, (
                f'الرقعة تحتاج {len(todo):,} مربّعًا — والحدّ على المصدر '
                f'الافتراضي {cap:,}. خوادم OpenStreetMap تطوّعية وشروطها '
                f'تمنع التنزيل الجملي، وهذا القدر يستغرق {hours:.0f} ساعة. '
                'للرقعة الواسعة اضبط مصدر خرائط خاصًّا من الإعدادات '
                '(مزوّد تجاري أو خادم تملكه)، أو أنقص أقصى تقريب.')
        return False, f'الرقعة تحتاج {len(todo):,} مربّعًا، والحدّ {cap:,}.'
    delay = DELAY_SECONDS if is_osm else 0.05

    with _lock:
        _state.update(running=True, done=0, total=len(todo),
                      message='يبدأ التنزيل…', stopped=False)

    def worker():
        ok = fail = 0
        why = None
        try:
            for z, x, y in todo:
                with _lock:
                    if _state['stopped']:
                        _state['message'] = 'أُوقف بطلبك'
                        break
                data, reason = fetch_ex(z, x, y, url=url)
                if data and store(z, x, y, data):
                    ok += 1
                else:
                    fail += 1
                    why = reason or why
                    # مفتاحٌ مرفوض ورابطٌ معطوب لا يُصلحهما التكرار:
                    # يُوقَفان عند أوّلهما بدل ثلاثين محاولة تنتهي إلى
                    # الرسالة نفسها.
                    if why in ('key', 'bad_url') or (fail > 30 and ok == 0):
                        with _lock:
                            _state['message'] = FAIL_MESSAGES.get(
                                why, FAIL_MESSAGES['network'])
                        break
                with _lock:
                    _state['done'] = ok + fail
                    _state['message'] = f'{ok} مربّعًا' + (f' — تعذّر {fail}' if fail else '')
                time.sleep(delay)
        finally:
            with _lock:
                _state['running'] = False
                if not _state['message'] or _state['message'].startswith('يبدأ'):
                    _state['message'] = f'تمّ: {ok} مربّعًا'

    threading.Thread(target=worker, daemon=True).start()
    return True, f'بدأ تنزيل {len(todo):,} مربّعًا'


# --------------------------------------------------------- النقل والحجم

def cache_stats():
    total = count = 0
    for root, _dirs, files in os.walk(cache_dir()):
        for f in files:
            if not f.endswith('.png'):
                continue
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except OSError:
                pass
    return {'tiles': count, 'bytes': total}


def export_pack(dest_path):
    """يحزم المخزن ملفًّا واحدًا — للنقل إلى تركيب بلا إنترنت."""
    tmp = dest_path + '.part'
    base = cache_dir()
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED, allowZip64=True) as z:
        # ZIP_STORED لا DEFLATED: صور PNG مضغوطة أصلًا، وإعادة ضغطها
        # تستهلك دقائق ولا توفّر شيئًا يُذكر.
        for root, _dirs, files in os.walk(base):
            for f in files:
                if not f.endswith('.png'):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, base).replace(os.sep, '/'))
    os.replace(tmp, dest_path)
    return dest_path


def import_pack(src_path):
    """(عدد المستورَد، رسالة). يرفض ما يخرج عن بنية المخزن.

    الحزمة ملفٌّ يأتي من خارج النظام، فكل مسار فيها يُفحص: مدخلةٌ
    اسمها ‎../../etc/شيء تكتب خارج المخزن.
    """
    base = os.path.abspath(cache_dir())
    added = 0
    try:
        with zipfile.ZipFile(src_path) as z:
            for info in z.infolist():
                name = info.filename
                if info.is_dir() or not name.endswith('.png'):
                    continue
                parts = name.split('/')
                if len(parts) != 3:
                    continue
                zz, xx, yy = parts[0], parts[1], parts[2][:-4]
                p = tile_path(zz, xx, yy)
                if not p:
                    continue
                if os.path.commonpath([os.path.abspath(p), base]) != base:
                    continue
                if info.file_size > 512 * 1024:
                    continue          # مربّع خريطة لا يبلغ هذا
                data = z.read(info)
                if data[:4] != b'\x89PNG':
                    continue          # ليس صورة PNG مهما كان امتداده
                if store(zz, xx, yy, data):
                    added += 1
    except zipfile.BadZipFile:
        return 0, 'الملف ليس حزمة صالحة'
    except OSError as e:
        return 0, f'تعذّرت القراءة: {e}'
    return added, f'استُورد {added:,} مربّعًا'


def clear():
    shutil.rmtree(cache_dir(), ignore_errors=True)
