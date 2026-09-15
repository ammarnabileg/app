"""نظام المناديب: خط السير، ومحطات الوقوف، وصورة حيّة عند كل زيارة.

ثلاثة أشياء يقوم عليها التصميم، وكلّها اعترافات لا ادّعاءات:

**١ — الموقع يأتي من المتصفح، فهو قابل للتزوير.** لا يستطيع الخادم أن
يُثبت أن مندوبًا كان في مكان. فالنظام لا يدّعي اليقين: يسجّل كل ما يمكن
قياسه — الدقّة المُعلَنة، والسرعة الضمنية بين نقطتين، ومصدر الصورة،
والمسافة عن المحطة — ويعرضه للمدير ليحكم. والقياسات المستحيلة تُعلَّم
لا تُخفى.

**٢ — المتصفح لا يقرأ الموقع والشاشة مقفلة.** فالتتبّع «طول الطريق»
يحتاج شاشة الرحلة مفتوحة مع Wake Lock. وما ينقطع يظهر **فجوةً معلَّمة**
في الخط لا خطًّا مستقيمًا يوهم بأن المندوب سار في الهواء. الفجوات
تُحسب من الأزمنة عند العرض، فلا يملك العميل إخفاءها بعدم الإبلاغ عنها.

**٣ — لا صورة «حيّة» يقينًا من متصفح.** ما يمكن فعله: منع اختيار ملف
(الالتقاط من getUserMedia مباشرةً)، ورمزٌ قصير العمر يصدره الخادم فلا
تُقبل صورة أُعدّت قبل الزيارة، ورفض ما يحمل EXIF — لأن مخرجات canvas
لا تحمله وصور المعرض تحمله دائمًا تقريبًا. وهذه دلائل لا براهين،
والختم يُكتب على الخادم لا على العميل حتى لا يكون تحت يده.
"""

import hashlib
import io
import math
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

from utils.db import DB_PATH, get_db_connection

# ما بين نقطتين أبعد من هذا يُعدّ انقطاعًا لا سيرًا.
GAP_SECONDS = 180

# سرعة يستحيل بلوغها برًّا: 300 كم/س. ما فوقها قفزة لا رحلة.
IMPOSSIBLE_KMH = 300.0

# عمر رمز الزيارة: يكفي لالتقاط صورة، ولا يكفي لتحضيرها من البيت.
TOKEN_TTL_SECONDS = 180

MAX_PHOTO_BYTES = 6 * 1024 * 1024
MAX_POINTS_PER_BATCH = 500

_ARABIC_FONTS = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSerif.ttf',
    'C:\\Windows\\Fonts\\tahoma.ttf',
    'C:\\Windows\\Fonts\\segoeui.ttf',
    'C:\\Windows\\Fonts\\arial.ttf',
    '/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf',
    '/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf',
)

# محارف الختم كلها: عربية موصولة، وأرقام، وفواصل. الخط يُختار بأنه
# يغطّيها لا بأنه أوّل ملف موجود.
_PROBE_CHARS = 'م-·—/:,5'

_chosen_font = {}


# ------------------------------------------------------------- المخطَّط

SCHEMA = [
    # المنطقة: مضلَّع من عدة نقاط. المحطة تقع داخلها، والمندوب يُسنَد
    # إليها — فيصير «خرج عن منطقته» سؤالًا له جواب.
    '''CREATE TABLE IF NOT EXISTS field_territories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        polygon TEXT NOT NULL,
        color TEXT DEFAULT '#0d6efd',
        is_active INTEGER NOT NULL DEFAULT 1,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    # موظف واحد قد يغطّي أكثر من منطقة، والمنطقة قد يتقاسمها اثنان.
    '''CREATE TABLE IF NOT EXISTS field_territory_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        territory_id INTEGER NOT NULL,
        employee_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (territory_id, employee_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS field_stations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        customer_name TEXT,
        address TEXT,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        radius_meters INTEGER NOT NULL DEFAULT 100,
        min_minutes INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    # الجدول: نمط أسبوعي له فترة سريان. «كل فترة يتغيّر» تعني نسخةً
    # جديدة تبدأ من تاريخ، لا تعديلًا على القديمة — فخطة الشهر الماضي
    # تبقى كما نُفّذت، ولا يُعاد كتابة التاريخ.
    '''CREATE TABLE IF NOT EXISTS field_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        name TEXT,
        starts_on TEXT NOT NULL,
        ends_on TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    '''CREATE TABLE IF NOT EXISTS field_schedule_days (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_id INTEGER NOT NULL,
        weekday INTEGER NOT NULL,
        station_id INTEGER NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        UNIQUE (schedule_id, weekday, station_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS field_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        station_id INTEGER NOT NULL,
        visit_date TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        is_required INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (employee_id, station_id, visit_date)
    )''',
    '''CREATE TABLE IF NOT EXISTS field_trips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        trip_date TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        end_reason TEXT,
        device_uuid TEXT,
        point_count INTEGER NOT NULL DEFAULT 0,
        distance_meters REAL NOT NULL DEFAULT 0
    )''',
    '''CREATE TABLE IF NOT EXISTS field_track_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trip_id INTEGER NOT NULL,
        employee_id INTEGER NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        accuracy REAL,
        speed REAL,
        heading REAL,
        recorded_at TEXT NOT NULL,
        received_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    '''CREATE TABLE IF NOT EXISTS field_visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trip_id INTEGER,
        employee_id INTEGER NOT NULL,
        station_id INTEGER NOT NULL,
        assignment_id INTEGER,
        check_in_at TEXT,
        check_in_lat REAL, check_in_lon REAL,
        check_in_distance REAL, check_in_accuracy REAL,
        check_out_at TEXT,
        check_out_lat REAL, check_out_lon REAL,
        check_out_distance REAL, check_out_accuracy REAL,
        status TEXT NOT NULL DEFAULT 'open',
        notes TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS field_visit_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        visit_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        file_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        bytes INTEGER NOT NULL,
        captured_at TEXT NOT NULL,
        latitude REAL, longitude REAL, accuracy REAL,
        distance_meters REAL,
        had_exif INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''',
    '''CREATE TABLE IF NOT EXISTS field_visit_tokens (
        token TEXT PRIMARY KEY,
        employee_id INTEGER NOT NULL,
        station_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        used_at TEXT
    )''',
    'CREATE INDEX IF NOT EXISTS idx_ftp_trip ON field_track_points (trip_id, recorded_at)',
    'CREATE INDEX IF NOT EXISTS idx_fv_emp ON field_visits (employee_id, check_in_at)',
    'CREATE INDEX IF NOT EXISTS idx_fa_day ON field_assignments (visit_date, employee_id)',
    'CREATE INDEX IF NOT EXISTS idx_ft_day ON field_trips (trip_date, employee_id)',
]


# أعمدة أُضيفت بعد أن صارت قواعد عند عملاء. ALTER يفشل إن وُجد العمود،
# فيُتجاهَل خطؤه وحده — لا كل خطأ.
_ADDED_COLUMNS = [
    ('field_stations', 'territory_id', 'INTEGER'),
    # من أين جاءت هذه المهمة: 'manual' بيد المسؤول، أو 'schedule'
    # مولَّدة من الجدول. الفرق ليس توثيقًا: تغييرُ الجدول يُعيد توليد
    # المولَّد وحده ولا يمسّ ما وُضع باليد.
    ('field_assignments', 'source', "TEXT NOT NULL DEFAULT 'manual'"),
]


# ------------------------------------------------- وحدة اختيارية

SETTING_ENABLED = 'field_module_enabled'


def module_enabled(conn=None):
    """أوحدةُ المناديب مُفعَّلة في هذا التركيب؟

    مُطفأة افتراضًا: أكثر الشركات لا مناديب لها، ووحدةٌ تتتبّع مواقع
    الموظفين لا ينبغي أن تعمل عند من لم يطلبها. فالإطفاء هو الوضع
    الآمن، لا مجرّد الوضع الأنسب.

    والقراءة تسامحية: جدول الإعدادات قد لا يكون مهيّأً بعد عند أول
    إقلاع، وسقوط الصفحة كلها لأجل هذا الفحص أسوأ من إطفاء الوحدة.
    """
    try:
        conn = conn or get_db_connection()
        row = conn.execute(
            'SELECT setting_value FROM salary_settings_v2 WHERE setting_name = ?',
            (SETTING_ENABLED,)).fetchone()
    except Exception:
        return False

    if row is None:
        # لم يُضبط بعد: تُقرأ البيئة مرةً واحدة — بها تُجهّز اللوحة
        # المستأجرين الذين اشتروا الوحدة دون أن يفتح أحدٌ الإعدادات.
        return os.environ.get('HR_FIELD_MODULE', '0').strip() in ('1', 'true', 'yes', 'on')

    value = row[0] if not hasattr(row, 'keys') else row['setting_value']
    return str(value).strip() in ('1', 'true', 'yes', 'on')


def set_module_enabled(conn, on):
    conn.execute('''INSERT INTO salary_settings_v2 (setting_name, setting_value)
                    VALUES (?, ?)
                    ON CONFLICT(setting_name) DO UPDATE SET setting_value = excluded.setting_value''',
                 (SETTING_ENABLED, '1' if on else '0'))
    conn.commit()


WORK_MODES = {
    'office': 'يبصم في المكتب',
    'field': 'مندوب ميداني',
    'both': 'الاثنان معًا',
}


def work_mode(conn, employee_id):
    """نمط عمل الموظف. 'office' عند الغياب أو التلف.

    الافتراض 'office' لا 'field': الترقية لا يجوز أن تُحوّل موظفًا إلى
    مندوب يُتتبَّع موقعه دون أن يقرّر أحد ذلك.
    """
    try:
        row = conn.execute('SELECT work_mode FROM employees WHERE id = ?',
                           (employee_id,)).fetchone()
    except Exception:
        return 'office'
    if not row:
        return 'office'
    mode = (row[0] if not hasattr(row, 'keys') else row['work_mode']) or 'office'
    return mode if mode in WORK_MODES else 'office'


def is_field_rep(conn, employee_id):
    """أهو مندوب؟ — من الحقل الصريح، لا من كونه أُسنِدت له منطقة.

    كان التعريف ضمنيًّا: «من له منطقة». وهذا يفشل في ثلاث حالات
    حقيقية — مندوبٌ عُيّن اليوم ولم تُرسم منطقته بعد، ومندوبٌ سُحبت
    منطقته مؤقّتًا، ومن ينظر إلى ملف الموظف فلا يجد ما يقول إنه مندوب
    أصلًا. والتعريف الضمني لا يُضبط لأنه لا يُرى.
    """
    return work_mode(conn, employee_id) in ('field', 'both')


def punches_at_office(conn, employee_id):
    return work_mode(conn, employee_id) in ('office', 'both')


def field_reps(conn):
    """الموظفون المعرَّفون مناديبَ — لقوائم الإسناد."""
    try:
        rows = conn.execute('''
            SELECT id, name, employee_number, work_mode FROM employees
            WHERE is_active = 1 AND work_mode IN ('field', 'both')
            ORDER BY name''').fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def set_work_mode(conn, employee_id, mode):
    if mode not in WORK_MODES:
        raise ValueError('نمط عمل غير معروف')
    conn.execute('UPDATE employees SET work_mode = ? WHERE id = ?', (mode, employee_id))
    conn.commit()


def module_footprint(conn):
    """ما الذي يوجد من بيانات الوحدة — ليُعرف ما يُخفى عند الإطفاء.

    الإطفاء إخفاءٌ لا حذف: من أطفأها بالخطأ ثم أعادها يجد بياناته.
    والشاشة تقول له العدد صراحةً قبل أن يطفئ.
    """
    out = {}
    for label, table in (('stations', 'field_stations'),
                         ('territories', 'field_territories'),
                         ('trips', 'field_trips'),
                         ('visits', 'field_visits'),
                         ('photos', 'field_visit_photos')):
        try:
            out[label] = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        except Exception:
            out[label] = 0
    return out


def init_schema(conn=None):
    conn = conn or get_db_connection()
    for stmt in SCHEMA:
        conn.execute(stmt)

    for table, col, decl in _ADDED_COLUMNS:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
        if col not in cols:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_ftm_emp '
                 'ON field_territory_members (employee_id)')
    conn.commit()


# ------------------------------------------------------------ المناطق

def territories_of(conn, employee_id):
    """مناطق موظف، ومضلَّع كلٍّ مفكوكًا."""
    from utils import geo

    rows = conn.execute('''
        SELECT t.* FROM field_territories t
        JOIN field_territory_members m ON m.territory_id = t.id
        WHERE m.employee_id = ? AND t.is_active = 1
        ORDER BY t.name
    ''', (employee_id,)).fetchall()

    out = []
    for r in rows:
        pts, _why = geo.parse_polygon(r['polygon'])
        if not pts:
            continue          # مضلَّع تالف لا يُسقط البقيّة
        d = dict(r)
        d['points'] = pts
        out.append(d)
    return out


def station_territory(conn, lat, lon):
    """أوّل منطقة نشطة تقع فيها هذه النقطة، أو None."""
    from utils import geo

    for r in conn.execute(
            'SELECT id, name, polygon FROM field_territories WHERE is_active = 1'):
        pts, _ = geo.parse_polygon(r['polygon'])
        if pts and geo.contains(pts, lat, lon):
            return dict(r)
    return None


def outside_own_territory(conn, employee_id, track, margin_meters=60):
    """نقاط المسار الواقعة خارج كل مناطق الموظف.

    خارج **كلّ** مناطقه لا خارج واحدة: من يغطّي منطقتين يمرّ بينهما.
    ومن لا منطقة له لا يُقاس عليه شيء — فالنتيجة صفر لا «كلّه خارج»،
    لأن غياب الإسناد ليس مخالفةً من الموظف.
    """
    from utils import geo

    terrs = territories_of(conn, employee_id)
    if not terrs or not track:
        return 0, 0.0

    out = 0
    for t in track:
        lat, lon = t[0], t[1]
        if any(geo.contains(x['points'], lat, lon) for x in terrs):
            continue
        near = min(geo.distance_to_edge(x['points'], lat, lon) for x in terrs)
        if near is not None and near > margin_meters:
            out += 1
    return out, round(out / len(track), 3)


# --------------------------------------------------------------- المسافة

def haversine(lat1, lon1, lat2, lon2):
    """المسافة بالأمتار على سطح الأرض."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _parse(ts):
    if isinstance(ts, datetime):
        return ts
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
        try:
            return datetime.strptime(str(ts)[:26], fmt)
        except ValueError:
            continue
    return None


def build_path(points):
    """يحوّل نقاطًا خامًا إلى مقاطع: سيرٌ متّصل، أو فجوة، أو قفزة.

    الفجوة تُحسب من الأزمنة لا من إبلاغ العميل: من يقفل الشاشة لا
    يُبلّغ أنه أقفلها. وبغير هذا يُرسم خطٌّ مستقيم بين نقطة قبل الظهر
    وأخرى بعده فيبدو كأن المندوب سار في الهواء.

    يعيد (المقاطع، الملخّص).
    """
    pts = []
    for p in points:
        t = _parse(p['recorded_at'])
        if t is None:
            continue
        pts.append({
            'lat': float(p['latitude']), 'lon': float(p['longitude']),
            'accuracy': p['accuracy'], 'at': t,
            'recorded_at': str(p['recorded_at']),
        })
    pts.sort(key=lambda x: x['at'])

    segments = []
    cur = []
    total_m = 0.0
    gaps = 0
    jumps = 0

    for i, p in enumerate(pts):
        if i == 0:
            cur = [p]
            continue

        prev = pts[i - 1]
        secs = (p['at'] - prev['at']).total_seconds()
        dist = haversine(prev['lat'], prev['lon'], p['lat'], p['lon'])
        kmh = (dist / secs) * 3.6 if secs > 0 else 0.0

        if secs > GAP_SECONDS:
            if len(cur) > 1:
                segments.append({'kind': 'move', 'points': cur})
            segments.append({'kind': 'gap', 'points': [prev, p],
                             'seconds': int(secs), 'meters': round(dist)})
            gaps += 1
            cur = [p]
            continue

        if kmh > IMPOSSIBLE_KMH:
            # لا تُخفى ولا تُحذف: تُعلَّم ليراها المدير.
            if len(cur) > 1:
                segments.append({'kind': 'move', 'points': cur})
            segments.append({'kind': 'jump', 'points': [prev, p],
                             'kmh': round(kmh), 'meters': round(dist)})
            jumps += 1
            cur = [p]
            continue

        total_m += dist
        cur.append(p)

    if len(cur) > 1:
        segments.append({'kind': 'move', 'points': cur})
    elif cur and not segments:
        segments.append({'kind': 'move', 'points': cur})

    return segments, {
        'points': len(pts),
        'distance_meters': round(total_m),
        'gaps': gaps,
        'jumps': jumps,
        'first_at': pts[0]['recorded_at'] if pts else None,
        'last_at': pts[-1]['recorded_at'] if pts else None,
    }


# ------------------------------------------------------------ الصور

def photos_dir():
    return os.path.join(os.path.dirname(DB_PATH), 'field_photos')


def _photo_path(visit_id, kind):
    day = datetime.now().strftime('%Y-%m-%d')
    d = os.path.join(photos_dir(), day)
    os.makedirs(d, exist_ok=True)
    name = f'v{int(visit_id)}-{kind}-{secrets.token_hex(6)}.jpg'
    return os.path.join(d, name), os.path.join(day, name)


def _covers(font, chars):
    """أيرسم الخط هذه المحارف فعلًا، أم يرسم مربّع «لا glyph»؟

    الفحص بالمقارنة مع محرفٍ في منطقة الاستعمال الخاص — مفقود يقينًا في
    كل خط. وكان فحصي الأول `getmask(c).getbbox() is not None` وهو لا
    يُثبت شيئًا: مربّع .notdef نفسه له حبر، فيمرّ كأنه موجود. وقد مرّ
    عليّ فعلًا فخرج الختم «موظف □ صيدلية الروضة · 2026□09□15» — خطوط
    نوتو العربية في دبيان مقتطعة بلا شَرطة ولا نقطة وسطى.
    """
    import hashlib

    from PIL import Image, ImageDraw

    def ink(ch):
        img = Image.new('L', (64, 64), 0)
        ImageDraw.Draw(img).text((6, 6), ch, font=font, fill=255)
        return hashlib.md5(img.tobytes()).hexdigest()

    notdef = ink('')
    return all(ink(c) != notdef for c in chars)


def _font(size):
    from PIL import ImageFont

    path = _chosen_font.get('path')
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            _chosen_font.pop('path', None)

    fallback = None
    for cand in _ARABIC_FONTS:
        if not os.path.exists(cand):
            continue
        try:
            f = ImageFont.truetype(cand, size)
        except Exception:
            continue
        if _covers(f, _PROBE_CHARS):
            _chosen_font['path'] = cand
            return f
        fallback = fallback or f     # عربيّ ناقص خيرٌ من لا شيء

    return fallback or ImageFont.load_default()


def has_raqm():
    """أتملك Pillow محرّك التخطيط المركّب (raqm/HarfBuzz)؟"""
    try:
        from PIL import features
        return bool(features.check('raqm'))
    except Exception:
        return False


def _shape(text, raqm=None):
    """يهيّئ النصّ العربي للرسم — بالطريقة التي يفهمها المحرّك الموجود.

    هذا موضع خطأ وقعتُ فيه ثم رأيته في الصورة: كنت أُشكّل دائمًا
    بـarabic_reshaper ثم أعكس بـbidi. وحين يكون raqm موجودًا — وهو
    موجود في عجلات Pillow الحديثة — يُشكّل HarfBuzz النصّ بنفسه، فيصل
    إليه نصٌّ مُشكَّل سلفًا بأشكال العرض (U+FExx) فيعدّها حروفًا منفردة
    ولا يصلها. فخرج الختم حروفًا مفكّكة مقلوبة: «ةضورلا ةيلديص فظوم».
    والكشف لم يأتِ من قراءة الشيفرة بل من النظر إلى الصورة.

    فمع raqm: يُمرَّر النصّ خامًا ويُرسم بـdirection='rtl'.
    وبدونه: التشكيل والعكس يدويًّا، وهو الصواب للمحرّك البسيط.
    """
    if raqm is None:
        raqm = has_raqm()
    if raqm:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def stamp_photo(raw_bytes, lines):
    """يكتب سطور الخادم على الصورة ويعيدها JPEG.

    الختم من الخادم لا من العميل: لو كُتب في المتصفح لكان تحت يد من
    يُراد التحقّق منه. والوقت والإحداثيات هنا هي ما سجّله الخادم.
    """
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(raw_bytes))
    img = img.convert('RGB')

    # حدٌّ للأبعاد: صورة هاتف حديثة تتجاوز 4000 بكسل، وعشرات الزيارات
    # يوميًّا لكل مندوب تملأ قرص العميل في أسابيع.
    img.thumbnail((1280, 1280))

    w, h = img.size
    size = max(13, w // 46)
    pad = max(6, w // 100)
    font = _font(size)

    draw = ImageDraw.Draw(img, 'RGBA')
    raqm = has_raqm()
    shaped = [_shape(x, raqm) for x in lines]
    # الاتجاه يُمرَّر لراقم وحده؛ ومن غيره يرمي Pillow.
    kw = {'direction': 'rtl'} if raqm else {}

    heights = []
    for s in shaped:
        box = draw.textbbox((0, 0), s, font=font, **kw)
        heights.append(box[3] - box[1] + pad // 2)
    band = sum(heights) + pad * 2

    draw.rectangle([(0, h - band), (w, h)], fill=(0, 0, 0, 165))

    y = h - band + pad
    for s, hh in zip(shaped, heights):
        draw.text((w - pad, y), s, font=font, fill=(255, 255, 255),
                  anchor='ra', **kw)
        y += hh

    out = io.BytesIO()
    img.save(out, format='JPEG', quality=82, optimize=True)
    return out.getvalue()


def has_exif(raw_bytes):
    """أفي الصورة بيانات EXIF؟

    مخرجات canvas — وهي طريق الالتقاط الوحيد في شاشتنا — لا تحمل EXIF.
    وصور المعرض من الهواتف تحمله دائمًا تقريبًا. فوجوده يعني أن الصورة
    لم تأتِ من كاميرا الصفحة. دليل لا برهان: من يعيد ترميز صورة يمحوه.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw_bytes))
        exif = img.getexif()
        return bool(exif and len(exif) > 0)
    except Exception:
        return False


def save_visit_photo(conn, visit_id, kind, raw_bytes, meta):
    """يختم الصورة ويحفظها ويسجّل صفّها. يعيد الصفّ.

    meta: lat, lon, accuracy, distance, station_name, employee_name, at
    """
    if not raw_bytes:
        raise ValueError('لا صورة')
    if len(raw_bytes) > MAX_PHOTO_BYTES:
        raise ValueError('الصورة أكبر من الحدّ المسموح')

    exif = has_exif(raw_bytes)

    at = meta.get('at') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    coord = (f"{meta['lat']:.5f}, {meta['lon']:.5f}"
             if meta.get('lat') is not None else 'بلا إحداثيات')
    lines = [
        f"{meta.get('employee_name') or ''} — {meta.get('station_name') or ''}",
        f"{'دخول' if kind == 'in' else 'خروج'} · {at}",
        f"{coord} · دقّة {int(meta.get('accuracy') or 0)}م · "
        f"بُعد {int(meta.get('distance') or 0)}م",
    ]

    stamped = stamp_photo(raw_bytes, lines)
    full, rel = _photo_path(visit_id, kind)
    tmp = full + '.part'
    with open(tmp, 'wb') as f:
        f.write(stamped)
    os.replace(tmp, full)      # لا ملف نصفه في المجلد

    digest = hashlib.sha256(stamped).hexdigest()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO field_visit_photos
            (visit_id, kind, file_path, sha256, bytes, captured_at,
             latitude, longitude, accuracy, distance_meters, had_exif)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (visit_id, kind, rel.replace(os.sep, '/'), digest, len(stamped), at,
          meta.get('lat'), meta.get('lon'), meta.get('accuracy'),
          meta.get('distance'), 1 if exif else 0))
    return cur.lastrowid, rel, digest, exif


# ------------------------------------------------------------ الرموز

def issue_token(conn, employee_id, station_id, kind):
    """رمز قصير العمر يُطلب قبل الصورة.

    يمنع صورةً أُعدّت قبل الزيارة: الرمز يصدر عند فتح شاشة المحطة،
    ولا يُقبل بعد TOKEN_TTL_SECONDS، ولا يُقبل مرتين.
    """
    tok = secrets.token_urlsafe(24)
    conn.execute('''
        INSERT INTO field_visit_tokens (token, employee_id, station_id, kind, issued_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (tok, employee_id, station_id, kind,
          datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    return tok


def consume_token(conn, token, employee_id, station_id, kind):
    """(صالح؟، السبب). يُستهلك مرةً واحدة."""
    if not token:
        return False, 'لا رمز للزيارة — أعد فتح شاشة المحطة'

    row = conn.execute(
        'SELECT * FROM field_visit_tokens WHERE token = ?', (token,)).fetchone()
    if not row:
        return False, 'رمز الزيارة غير معروف'
    if row['used_at']:
        return False, 'رمز الزيارة استُعمل من قبل'
    if row['employee_id'] != employee_id or row['station_id'] != station_id:
        return False, 'رمز الزيارة لمحطة أو موظف آخر'
    if row['kind'] != kind:
        return False, 'رمز الزيارة لخطوة أخرى'

    issued = _parse(row['issued_at'])
    if issued is None or (datetime.now() - issued).total_seconds() > TOKEN_TTL_SECONDS:
        return False, 'انتهت مهلة الرمز — التقط الصورة من شاشة المحطة الآن'

    conn.execute('UPDATE field_visit_tokens SET used_at = ? WHERE token = ?',
                 (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), token))
    return True, 'صالح'


def purge_tokens(conn, older_than_hours=24):
    cutoff = (datetime.now() - timedelta(hours=older_than_hours)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('DELETE FROM field_visit_tokens WHERE issued_at < ?', (cutoff,))
    conn.commit()


# ------------------------------------------------------------ الرحلة

def open_trip(conn, employee_id, device_uuid=None):
    """رحلة اليوم — تُفتح مرةً وتُستأنف إن كانت مفتوحة."""
    today = datetime.now().strftime('%Y-%m-%d')
    row = conn.execute('''
        SELECT * FROM field_trips
        WHERE employee_id = ? AND trip_date = ? AND ended_at IS NULL
        ORDER BY id DESC LIMIT 1
    ''', (employee_id, today)).fetchone()
    if row:
        return row['id'], False

    cur = conn.cursor()
    cur.execute('''
        INSERT INTO field_trips (employee_id, trip_date, started_at, device_uuid)
        VALUES (?, ?, ?, ?)
    ''', (employee_id, today, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
          (device_uuid or '')[:64]))
    conn.commit()
    return cur.lastrowid, True


def close_trip(conn, trip_id, reason='manual'):
    conn.execute('''
        UPDATE field_trips SET ended_at = ?, end_reason = ?
        WHERE id = ? AND ended_at IS NULL
    ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), reason, trip_id))
    conn.commit()


def add_points(conn, trip_id, employee_id, points):
    """يضيف دفعة نقاط ويعيد عدد المقبول.

    النقطة بلا زمن أو بإحداثيات خارج المدى تُهمَل بدل أن تُفسد الخط.
    """
    rows = []
    for p in (points or [])[:MAX_POINTS_PER_BATCH]:
        try:
            lat = float(p.get('lat', p.get('latitude')))
            lon = float(p.get('lon', p.get('longitude')))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            continue

        at = _parse(p.get('at') or p.get('recorded_at'))
        if at is None:
            continue
        # زمن من المستقبل: ساعة الجهاز مضبوطة خطأً أو مُتلاعب بها.
        if at > datetime.now() + timedelta(minutes=5):
            continue

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        rows.append((trip_id, employee_id, lat, lon, _f(p.get('accuracy')),
                     _f(p.get('speed')), _f(p.get('heading')),
                     at.strftime('%Y-%m-%d %H:%M:%S')))

    if not rows:
        return 0

    conn.executemany('''
        INSERT INTO field_track_points
            (trip_id, employee_id, latitude, longitude, accuracy, speed, heading, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', rows)
    conn.execute('UPDATE field_trips SET point_count = point_count + ? WHERE id = ?',
                 (len(rows), trip_id))
    conn.commit()
    return len(rows)


def trip_path(conn, trip_id):
    pts = conn.execute('''
        SELECT latitude, longitude, accuracy, recorded_at
        FROM field_track_points WHERE trip_id = ? ORDER BY recorded_at ASC
    ''', (trip_id,)).fetchall()
    return build_path([dict(p) for p in pts])


# ----------------------------------------------------------- الزيارات

# ------------------------------------------------------------ الجداول

# ترتيب أيام الأسبوع كما تُعرض وتُخزَّن. السبت أولًا لأنه أول الأسبوع
# في الكويت والخليج، ودوام المناديب من الأحد إلى الخميس.
#
# ومصدرٌ واحد للترتيب عمدًا: بايثون يبدأ الأسبوع بالاثنين
# (weekday()=0)، وjavascript يبدؤه بالأحد (getDay()=0). ثلاثة ترتيبات
# في نظام واحد تعني أن جدول يوم الأحد سيُنفَّذ يوم الثلاثاء يومًا ما،
# ولن يُعرف السبب. فالتحويل يمرّ من هنا وحده.
WEEKDAYS = ['السبت', 'الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة']


def weekday_index(d):
    """رقم اليوم في ترتيبنا (٠ = السبت) من كائن تاريخ.

    isoweekday: الاثنين ١ … الأحد ٧. والسبت ٦، فيصير صفرًا بالإزاحة.
    """
    return (d.isoweekday() + 1) % 7


def active_schedule(conn, employee_id, day):
    """الجدول السارية فترتُه على هذا اليوم، أو None.

    الأحدث بدايةً يفوز عند التداخل: من يُدخل جدولًا جديدًا يبدأ اليوم
    يقصد أن يحلّ محلّ القديم، لا أن يتنافسا.
    """
    return conn.execute('''
        SELECT * FROM field_schedules
        WHERE employee_id = ?
          AND DATE(starts_on) <= DATE(?)
          AND (ends_on IS NULL OR DATE(ends_on) >= DATE(?))
        ORDER BY DATE(starts_on) DESC, id DESC LIMIT 1
    ''', (employee_id, day, day)).fetchone()


def schedule_stations(conn, schedule_id, weekday):
    rows = conn.execute('''
        SELECT d.station_id, d.sort_order, s.name
        FROM field_schedule_days d
        JOIN field_stations s ON s.id = d.station_id
        WHERE d.schedule_id = ? AND d.weekday = ? AND s.is_active = 1
        ORDER BY d.sort_order, s.name
    ''', (schedule_id, weekday)).fetchall()
    return [dict(r) for r in rows]


def generate_day(conn, employee_id, day, force=False):
    """يُنزل خطة يومٍ من الجدول السارية فترتُه. يعيد عدد ما وُلِّد.

    التوليد عند الطلب لا بمهمّة ليلية: مهمّةٌ تفشل ليلةً تترك مندوبًا
    بلا خطة صباحًا ولا يُعرف السبب إلا بعد فوات اليوم. وهنا يُولَّد
    اليوم أوّل مرة يُسأل عنها — من شاشة المندوب أو من شاشة المسؤول.

    ولا يُمسّ ما وُضع باليد: التوليد يتخطّى اليوم إن كان فيه مهامّ
    أصلًا، إلا أن يُطلب صراحةً (force) وحينها يُمحى المولَّد وحده.
    """
    existing = conn.execute(
        'SELECT COUNT(*) FROM field_assignments WHERE employee_id = ? AND visit_date = ?',
        (employee_id, day)).fetchone()[0]

    if existing and not force:
        return 0
    if force:
        conn.execute('''DELETE FROM field_assignments
                        WHERE employee_id = ? AND visit_date = ? AND source = 'schedule' ''',
                     (employee_id, day))

    sched = active_schedule(conn, employee_id, day)
    if not sched:
        conn.commit()
        return 0

    d = _parse(day + ' 00:00:00') or _parse(day)
    if d is None:
        return 0

    stations = schedule_stations(conn, sched['id'], weekday_index(d))
    for i, st in enumerate(stations):
        conn.execute('''INSERT OR IGNORE INTO field_assignments
                        (employee_id, station_id, visit_date, sort_order, source)
                        VALUES (?, ?, ?, ?, 'schedule')''',
                     (employee_id, st['station_id'], day, i))
    conn.commit()
    return len(stations)


def save_schedule(conn, employee_id, starts_on, days, name=None):
    """نسخة جديدة من جدول موظف تبدأ من تاريخ.

    وتُقفل النسخة السابقة في اليوم الذي قبله بدل أن تُحذف: ما نُفّذ
    الشهر الماضي يبقى مقروءًا كما كان.

    days: {رقم_اليوم: [أرقام المحطات]}
    """
    from datetime import timedelta

    start = _parse(str(starts_on) + ' 00:00:00')
    if start is None:
        raise ValueError('تاريخ البداية غير صالح')
    starts_on = start.strftime('%Y-%m-%d')
    prev_end = (start - timedelta(days=1)).strftime('%Y-%m-%d')

    conn.execute('''UPDATE field_schedules
                    SET ends_on = ?
                    WHERE employee_id = ? AND DATE(starts_on) < DATE(?)
                      AND (ends_on IS NULL OR DATE(ends_on) >= DATE(?))''',
                 (prev_end, employee_id, starts_on, starts_on))

    # نسخة بالتاريخ نفسه تُستبدل: الحفظ مرتين في يوم واحد تصحيحٌ لا
    # نسختان متنافستان.
    old = conn.execute('SELECT id FROM field_schedules WHERE employee_id = ? '
                       'AND DATE(starts_on) = DATE(?)',
                       (employee_id, starts_on)).fetchall()
    for r in old:
        conn.execute('DELETE FROM field_schedule_days WHERE schedule_id = ?', (r['id'],))
        conn.execute('DELETE FROM field_schedules WHERE id = ?', (r['id'],))

    cur = conn.cursor()
    cur.execute('''INSERT INTO field_schedules (employee_id, name, starts_on)
                   VALUES (?, ?, ?)''', (employee_id, name, starts_on))
    sid = cur.lastrowid

    for wd, station_ids in (days or {}).items():
        try:
            wd = int(wd)
        except (TypeError, ValueError):
            continue
        if not 0 <= wd <= 6:
            continue
        for i, st in enumerate(list(station_ids)[:100]):
            conn.execute('''INSERT OR IGNORE INTO field_schedule_days
                            (schedule_id, weekday, station_id, sort_order)
                            VALUES (?, ?, ?, ?)''', (sid, wd, st, i))

    # أيام المستقبل المولَّدة من الجدول القديم تُمحى لتُولَّد من الجديد.
    # الماضي لا يُمسّ: تغيير جدولٍ اليوم لا يعيد كتابة ما نُفّذ أمس.
    conn.execute('''DELETE FROM field_assignments
                    WHERE employee_id = ? AND source = 'schedule'
                      AND DATE(visit_date) >= DATE(?)''', (employee_id, starts_on))
    conn.commit()
    return sid


def schedule_history(conn, employee_id):
    rows = conn.execute('''
        SELECT s.*, (SELECT COUNT(*) FROM field_schedule_days d
                     WHERE d.schedule_id = s.id) AS entries
        FROM field_schedules s WHERE s.employee_id = ?
        ORDER BY DATE(s.starts_on) DESC, s.id DESC
    ''', (employee_id,)).fetchall()
    return [dict(r) for r in rows]


def schedule_grid(conn, schedule_id):
    """{رقم اليوم: [محطات]} — كما تُعرض في الشاشة."""
    grid = {i: [] for i in range(7)}
    for r in conn.execute('''
            SELECT d.weekday, d.station_id, d.sort_order, s.name
            FROM field_schedule_days d
            JOIN field_stations s ON s.id = d.station_id
            WHERE d.schedule_id = ? ORDER BY d.weekday, d.sort_order''', (schedule_id,)):
        grid[r['weekday']].append({'station_id': r['station_id'], 'name': r['name']})
    return grid


def day_plan(conn, employee_id, day=None, autogenerate=True):
    """محطات اليوم وحالة كل واحدة.

    والتوليد من الجدول يقع هنا: أوّل من يسأل عن خطة اليوم — المندوب
    صباحًا أو المسؤول في شاشته — يجدها مولَّدةً. ولو انتظرنا مهمّةً
    ليلية لتعطّل مندوب صباح كل ليلةٍ فشلت فيها.
    """
    day = day or datetime.now().strftime('%Y-%m-%d')
    if autogenerate:
        try:
            generate_day(conn, employee_id, day)
        except Exception:
            pass          # قراءة الخطة لا تسقط لأن التوليد تعثّر
    rows = conn.execute('''
        SELECT a.id AS assignment_id, a.sort_order, a.is_required, a.source,
               s.id AS station_id, s.name, s.customer_name, s.address,
               s.latitude, s.longitude, s.radius_meters, s.min_minutes,
               v.id AS visit_id, v.check_in_at, v.check_out_at, v.status
        FROM field_assignments a
        JOIN field_stations s ON s.id = a.station_id
        LEFT JOIN field_visits v
               ON v.station_id = a.station_id
              AND v.employee_id = a.employee_id
              AND DATE(v.check_in_at) = a.visit_date
        WHERE a.employee_id = ? AND a.visit_date = ? AND s.is_active = 1
        ORDER BY a.sort_order ASC, s.name ASC
    ''', (employee_id, day)).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        if d['check_out_at']:
            d['state'] = 'done'
        elif d['check_in_at']:
            d['state'] = 'inside'
        else:
            d['state'] = 'pending'
        out.append(d)
    return out


def open_visit(conn, employee_id):
    return conn.execute('''
        SELECT v.*, s.name AS station_name, s.latitude, s.longitude, s.radius_meters
        FROM field_visits v JOIN field_stations s ON s.id = v.station_id
        WHERE v.employee_id = ? AND v.status = 'open'
        ORDER BY v.id DESC LIMIT 1
    ''', (employee_id,)).fetchone()


def day_summary(conn, employee_id, day=None):
    """التزام اليوم: كم محطة من كم، وما الذي تخلّف."""
    day = day or datetime.now().strftime('%Y-%m-%d')
    plan = day_plan(conn, employee_id, day)
    required = [p for p in plan if p['is_required']]
    done = [p for p in required if p['state'] == 'done']
    return {
        'date': day,
        'stations_total': len(required),
        'stations_done': len(done),
        'missed': [p['name'] for p in required if p['state'] == 'pending'],
        'open': [p['name'] for p in plan if p['state'] == 'inside'],
    }
