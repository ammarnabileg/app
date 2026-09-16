"""المباني: أيّ مبنًى هذا، وأيّ فرعٍ فيه.

المحطة نقطةٌ ونطاق. ونطاق مئة متر في مجمّع تجاري يسع عشرة فروع، فقول
«كان داخل النطاق» لا يجيب سؤال المدير: **في أيّ فرع كان؟**

والجواب في بيانات OpenStreetMap نفسها: المباني فيها مضلَّعات لها أسماء
(«مجمّع الأفنيوز»، «صيدلية الروضة»). فتُجلب مبانيْ الرقعة مرةً،
وتُحفظ، ويُسأل عن النقطة: أيّ مضلَّعٍ يحويها.

**ثلاثة حدود أقولها قبل أن تُبنى عليها توقّعات:**

١ — البيانات تطوّعية. مبنًى لم يرسمه أحد ليس موجودًا هنا، ومبنًى رُسم
بلا اسم يظهر شكلًا بلا اسم. والكويت مغطّاة جيّدًا في المدن، وأقلّ في
الأطراف.

٢ — الدقّة المُعلَنة للهاتف عشرات الأمتار. فنقطةٌ داخل مبنًى قد تكون
لمن وقف بجانبه. ولذا يُعاد أقرب المباني لا مبنًى واحدًا يُجزم به،
والمسافة مذكورة مع كلٍّ.

٣ — Overpass خدمة مجانية بحدود. فالرقعة محدودة، والنتيجة تُحفظ، ولا
تُطلب مرتين.
"""

import json
import math
import os
import time

from utils.db import DB_PATH

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
USER_AGENT = 'OnPointHR-FieldModule/1.0 (+https://onz.one)'

# خليّة التخزين: ٠٫٠١ درجة ≈ ١٫١ كم. الطلبات المتقاربة تقع في الخليّة
# نفسها فتُقرأ من القرص بدل أن تُعاد إلى الخدمة.
CELL = 0.01

# سقف الرقعة في الطلب الواحد. رقعةٌ أوسع تُثقل خدمةً مجانية وتعيد آلاف
# المضلَّعات لا يُرسم منها شيء مفيد.
MAX_SPAN_DEG = 0.06          # ≈ ٦٫٦ كم

CACHE_DAYS = 90              # المباني لا تتغيّر كل يوم


def cache_dir():
    return os.path.join(os.path.dirname(DB_PATH), 'buildings')


def _cell_key(lat, lon):
    return (math.floor(lat / CELL), math.floor(lon / CELL))


def _cell_path(key):
    a, b = key
    return os.path.join(cache_dir(), f'{a}_{b}.json')


def _cells_for(min_lat, min_lon, max_lat, max_lon):
    out = []
    a1, b1 = _cell_key(min_lat, min_lon)
    a2, b2 = _cell_key(max_lat, max_lon)
    for a in range(min(a1, a2), max(a1, a2) + 1):
        for b in range(min(b1, b2), max(b1, b2) + 1):
            out.append((a, b))
    return out


def _cell_bbox(key):
    a, b = key
    return (a * CELL, b * CELL, (a + 1) * CELL, (b + 1) * CELL)


def read_cell(key, max_age_days=CACHE_DAYS):
    p = _cell_path(key)
    if not os.path.exists(p):
        return None
    try:
        if (time.time() - os.path.getmtime(p)) > max_age_days * 86400:
            return None
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_cell(key, buildings):
    os.makedirs(cache_dir(), exist_ok=True)
    p = _cell_path(key)
    tmp = p + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(buildings, f, ensure_ascii=False)
    os.replace(tmp, p)


# ------------------------------------------------------------ الجلب

def build_query(min_lat, min_lon, max_lat, max_lon):
    """استعلام Overpass. `out geom` يُرجع الإحداثيات داخل النتيجة نفسها،
    فلا حاجة إلى طلب ثانٍ للعُقد."""
    bbox = f'{min_lat},{min_lon},{max_lat},{max_lon}'
    return (
        '[out:json][timeout:25];'
        f'(way["building"]({bbox});relation["building"]({bbox}););'
        'out tags geom;'
    )


def parse(payload):
    """يحوّل ردّ Overpass إلى مبانٍ مفهومة. لا يرمي على ردٍّ مشوَّه."""
    out = []
    if not isinstance(payload, dict):
        return out

    for el in payload.get('elements', []) or []:
        if not isinstance(el, dict):
            continue
        tags = el.get('tags') or {}

        geom = el.get('geometry')
        if not geom and el.get('members'):
            # علاقة: أطول حلقة خارجية تكفي للرسم والاحتواء.
            rings = [m.get('geometry') for m in el['members']
                     if isinstance(m, dict) and m.get('role') == 'outer' and m.get('geometry')]
            geom = max(rings, key=len) if rings else None
        if not geom or len(geom) < 3:
            continue

        pts = []
        for g in geom:
            try:
                pts.append([float(g['lat']), float(g['lon'])])
            except (KeyError, TypeError, ValueError):
                continue
        if len(pts) < 3:
            continue

        # الاسم العربي أولًا حيث وُجد — الشاشة عربية.
        name = (tags.get('name:ar') or tags.get('name') or
                tags.get('addr:housename') or tags.get('operator') or
                tags.get('brand') or '')

        out.append({
            'id': el.get('id'),
            'name': name,
            'kind': tags.get('shop') or tags.get('amenity') or tags.get('office')
                    or tags.get('building') or '',
            'levels': tags.get('building:levels') or '',
            'street': tags.get('addr:street') or '',
            'points': pts,
        })
    return out


def fetch(min_lat, min_lon, max_lat, max_lon, timeout=30):
    """يجلب مباني رقعة من Overpass. يعيد قائمة أو None عند التعذّر.

    لا يرمي: هذا يُنادى أثناء عرض خريطة، وانقطاع الشبكة حالٌ عادي.
    """
    try:
        import requests
        r = requests.post(OVERPASS_URL,
                          data={'data': build_query(min_lat, min_lon, max_lat, max_lon)},
                          headers={'User-Agent': USER_AGENT}, timeout=timeout)
        if r.status_code != 200:
            return None
        return parse(r.json())
    except Exception:
        return None


def in_bbox(min_lat, min_lon, max_lat, max_lon, allow_fetch=True):
    """(المباني، هل جُلب جديد؟). يقرأ من المخزن ويجلب ما ينقص.

    والرقعة تُقصّ إلى الحدّ: طلبٌ لنصف البلد يُثقل خدمةً مجانية ولا
    يُرسم منه شيء مفيد.
    """
    if (max_lat - min_lat) > MAX_SPAN_DEG or (max_lon - min_lon) > MAX_SPAN_DEG:
        cy, cx = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2
        h = MAX_SPAN_DEG / 2
        min_lat, max_lat = cy - h, cy + h
        min_lon, max_lon = cx - h, cx + h

    seen = {}
    fetched = False
    for key in _cells_for(min_lat, min_lon, max_lat, max_lon):
        cell = read_cell(key)
        if cell is None and allow_fetch:
            cell = fetch(*_cell_bbox(key))
            if cell is not None:
                write_cell(key, cell)
                fetched = True
        for b in (cell or []):
            seen[b.get('id') or id(b)] = b

    return list(seen.values()), fetched


# ------------------------------------------------- أيّ مبنًى هذه النقطة

def at_point(lat, lon, radius_meters=60, allow_fetch=True):
    """المباني التي تحوي النقطة أو تقاربها، الأقرب أولًا.

    لا مبنًى واحد يُجزم به: دقّة الهاتف عشرات الأمتار، فمن وقف بجانب
    مبنًى قد تقع نقطته داخله. فيُعرض الاحتواء صريحًا مع المسافة،
    ويحكم من يقرأ.
    """
    from utils import geo

    d = radius_meters / 111320.0 * 1.6
    found, _ = in_bbox(lat - d, lon - d, lat + d, lon + d, allow_fetch=allow_fetch)

    out = []
    for b in found:
        pts = [(p[0], p[1]) for p in b['points']]
        inside = geo.contains(pts, lat, lon)

        # المسافة إلى **جدار** المبنى لا إلى مركزه: مركز مجمّع تجاري
        # قد يبعد مئة متر عن بابه، فمن يقف عند الباب يخرج من النطاق
        # ويُقال إنه ليس عنده. أمسك الاختبار هذا عليّ.
        dist = geo.distance_to_edge(pts, lat, lon) or 0.0
        if inside or dist <= radius_meters:
            out.append({'name': b.get('name') or '', 'kind': b.get('kind') or '',
                        'street': b.get('street') or '', 'inside': inside,
                        'distance': round(dist), 'points': b['points']})

    # الاحتواء أولًا، ثم الأقرب جدارًا: من هو داخل مبنًى أولى بالذكر
    # ممّن بجانبه.
    out.sort(key=lambda x: (not x['inside'], x['distance']))
    return out[:5]


def describe(lat, lon, allow_fetch=True):
    """سطرٌ واحد يصف مكان النقطة، أو '' إن لم يُعرف.

    الصياغة تقول درجة اليقين: «داخل» لمن وقعت نقطته في المضلَّع،
    و«بجوار» لمن قاربه — ولا تُقال جزمًا في الحالين.
    """
    hits = at_point(lat, lon, allow_fetch=allow_fetch)
    named = [h for h in hits if h['name']]
    if not named:
        return ''
    h = named[0]
    if h['inside']:
        return f"داخل {h['name']}"
    return f"بجوار {h['name']} ({h['distance']} م)"


def cache_stats():
    total = count = 0
    for root, _dirs, files in os.walk(cache_dir()):
        for f in files:
            if f.endswith('.json'):
                try:
                    total += os.path.getsize(os.path.join(root, f))
                    count += 1
                except OSError:
                    pass
    return {'cells': count, 'bytes': total}
