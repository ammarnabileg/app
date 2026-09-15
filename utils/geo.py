"""مناطق العمل: مضلَّع من عدة نقاط، لا دائرة حول نقطة واحدة.

المنطقة ليست تفصيلًا تجميليًّا فوق المحطات. هي التي تجعل السؤال ممكنًا:
«هل خرج المندوب عن منطقته؟» — وهو سؤال لا تجيب عنه محطاتٌ متفرّقة، لأن
بين المحطة والأخرى طريقٌ كامل لا يُقاس.

ولماذا مضلَّع لا دائرة: الأحياء ليست دوائر. دائرةٌ تسع حيّ السالمية
تبتلع معها شارعًا من حيٍّ آخر، ودائرةٌ لا تبتلعه تترك ثلث الحيّ خارجها.
والمضلَّع يُرسم على حدود الشوارع الفعلية، فيقبل «منطقة كاملة أو حتّة
منها» كما تُرسم لا كما تُقرَّب.

**حدّ الدقّة، صراحةً:** الحساب هنا يعامل خطّي الطول والعرض كمستوٍ
مسطّح. وهذا صحيح عمليًّا لمنطقة في حجم حيّ أو مدينة — الخطأ فيها
بالأمتار — ويصير خاطئًا لمضلَّع يمتدّ آلاف الكيلومترات أو يعبر خطّ
التاريخ. ولا حاجة إلى غير ذلك هنا: المنطقة التي يزورها مندوب في يوم لا
تتجاوز مدينة.
"""

import json
import math

# أقلّ ما يصنع مساحة. نقطتان خطٌّ لا منطقة له داخل.
MIN_VERTICES = 3

# حدٌّ أعلى لعدد الرؤوس: مضلَّع بآلاف النقاط يُبطئ كل فحص موقع، ولا
# يضيف دقّةً يحتاجها أحد على حدود حيّ.
MAX_VERTICES = 200


def parse_polygon(raw):
    """(النقاط، السبب). يقبل JSON أو قائمة، ويردّ ما لا يصنع منطقة.

    الصيغة: [[lat, lon], [lat, lon], ...] — العرض أولًا، كترتيب
    الخرائط وGPS، لا كترتيب GeoJSON. وهذا اختيارٌ واحد يلزم الالتزام به
    في الشيفرة كلها؛ الخلط بينهما يضع الكويت في الصومال.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None, 'صيغة المضلَّع غير صالحة'

    if not isinstance(raw, (list, tuple)):
        return None, 'المضلَّع يجب أن يكون قائمة نقاط'

    pts = []
    for item in raw:
        if isinstance(item, dict):
            lat, lon = item.get('lat', item.get('latitude')), item.get('lon', item.get('longitude'))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            lat, lon = item[0], item[1]
        else:
            return None, 'نقطة غير مفهومة في المضلَّع'

        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None, 'إحداثيات ليست أرقامًا'
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None, 'إحداثيات خارج المدى'
        pts.append((lat, lon))

    # النقطة الأخيرة المكرّرة للإغلاق تُحذف: الحساب يغلق المضلَّع ضمنًا،
    # وبقاؤها يجعل ضلعًا بطول صفر.
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts.pop()

    if len(pts) < MIN_VERTICES:
        return None, f'المنطقة تحتاج {MIN_VERTICES} نقاط على الأقل'
    if len(pts) > MAX_VERTICES:
        return None, f'المضلَّع أكثر من {MAX_VERTICES} نقطة'

    return pts, 'صالح'


def dumps_polygon(points):
    return json.dumps([[round(a, 6), round(b, 6)] for a, b in points])


def bounds(points):
    """(أدنى عرض، أدنى طول، أقصى عرض، أقصى طول)."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return min(lats), min(lons), max(lats), max(lons)


def centroid(points):
    """مركز المضلَّع — لتوسيط الخريطة عليه."""
    n = len(points)
    return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n


def contains(points, lat, lon):
    """أتقع النقطة داخل المضلَّع؟ (خوارزمية الشعاع)

    يُرسَم شعاع من النقطة إلى ما لا نهاية ويُعدّ كم ضلعًا قطع: عددٌ فردي
    يعني الداخل. والنقطة على الحدّ تمامًا حالةٌ حدّية تقع في أيّ الجهتين
    بحسب الفاصلة العشرية — ولذلك لا يُبنى عليها قرار وحدها في هذا
    النظام، بل مع هامش المحطة ودقّة الجهاز.
    """
    if not points or len(points) < MIN_VERTICES:
        return False

    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        yi, xi = points[i]
        yj, xj = points[j]
        if (yi > lat) != (yj > lat):
            # نقطة تقاطع الضلع مع الخطّ الأفقي المارّ بالنقطة
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def distance_to_edge(points, lat, lon):
    """أقرب مسافة بالأمتار من النقطة إلى حدّ المنطقة.

    تُستعمل للهامش: جهازٌ دقّته ٤٠ مترًا قد يضع مندوبًا داخل منطقته على
    بُعد عشرة أمتار من حدّها، فالحكم بالخروج عليه ظلمٌ للقياس لا كشفٌ
    لمخالفة.
    """
    if not points:
        return None

    best = float('inf')
    n = len(points)
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        best = min(best, _point_segment_meters(lat, lon, a, b))
    return best


def _point_segment_meters(lat, lon, a, b):
    """المسافة من نقطة إلى قطعة مستقيمة، بالأمتار.

    الإسقاط على مستوٍ محلي: خط الطول يُضرب في جيب تمام العرض لأن دوائر
    الطول تتقارب كلّما ابتعدنا عن خطّ الاستواء. وبدونه تُحسب المسافة
    شرقًا أكبر مما هي بنحو ١٣٪ عند خطّ عرض الكويت.
    """
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))

    px = (lon - a[1]) * m_per_deg_lon
    py = (lat - a[0]) * m_per_deg_lat
    bx = (b[1] - a[1]) * m_per_deg_lon
    by = (b[0] - a[0]) * m_per_deg_lat

    seg2 = bx * bx + by * by
    if seg2 == 0:
        return math.hypot(px, py)

    t = max(0.0, min(1.0, (px * bx + py * by) / seg2))
    return math.hypot(px - t * bx, py - t * by)


def area_km2(points):
    """مساحة تقريبية — ليقرأها من يرسم المنطقة ويعرف حجم ما رسم."""
    if len(points) < MIN_VERTICES:
        return 0.0

    lat0 = centroid(points)[0]
    m_lat = 111320.0
    m_lon = 111320.0 * math.cos(math.radians(lat0))

    total = 0.0
    n = len(points)
    for i in range(n):
        y1, x1 = points[i]
        y2, x2 = points[(i + 1) % n]
        total += (x1 * m_lon) * (y2 * m_lat) - (x2 * m_lon) * (y1 * m_lat)
    return abs(total) / 2.0 / 1_000_000.0


def outside_ratio(points, track, margin_meters=60):
    """(عدد النقاط خارج المنطقة، نسبتها). للمتابعة لا للعقاب.

    الهامش لأن الخروج المُقاس قد يكون خطأ قياس: نقطةٌ خارج الحدّ بمترين
    ودقّة الجهاز أربعون مترًا لا تقول شيئًا.
    """
    if not points or not track:
        return 0, 0.0

    out = 0
    for t in track:
        lat, lon = t[0], t[1]
        if contains(points, lat, lon):
            continue
        d = distance_to_edge(points, lat, lon)
        if d is not None and d > margin_meters:
            out += 1
    return out, round(out / len(track), 3)
