"""مسارات المناديب: شاشة الرحلة للمندوب، وشاشة المتابعة للمدير.

الصلاحية هنا على محورين لا محور واحد:

  * المندوب لا يرى ولا يكتب إلا ما يخصّه هو. رقم الموظف يأتي من الجلسة
    لا من الطلب — ولو جاء من الطلب لصار كل مندوب قادرًا على الكتابة
    باسم غيره بتغيير رقم.
  * المدير يرى مرؤوسيه المباشرين، والمسؤول يرى الجميع. ولا أحد يرى
    صورة زيارة لا يملك رؤية صاحبها: الصور تُقدَّم من مسار محروس لا من
    مجلد ثابت، وإلا كفى تخمينُ اسم ملف.
"""

import os
from datetime import datetime

from functools import wraps

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from utils import field
from utils.auth import get_current_user, login_required
from utils.db import get_db_connection

field_bp = Blueprint('field', __name__)


def module_required(f):
    """لا شيء من هذه الوحدة يعمل وهي مُطفأة.

    الحارس على كل مسار لا على القائمة وحدها: إخفاء الرابط يُخفيه عمّن
    لا يعرف العنوان، ولا يمنع من يعرفه. ووحدةٌ تتتبّع مواقع الموظفين
    يجب أن تكون مغلقةً بالفعل عند من لم يشترها، لا مخفيّةً.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not field.module_enabled(get_db_connection()):
            if request.path.startswith('/api/') or request.path.startswith('/portal/api/'):
                return jsonify({'success': False,
                                'message': 'وحدة المناديب غير مُفعَّلة في هذا النظام'}), 404
            return render_template('field_off.html'), 404
        return f(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------ مساعدات

def _emp_id(for_write=True):
    """رقم موظف صاحب الجلسة — من الجلسة وحدها.

    for_write: لا يسقط إلى «أول موظف نشط» كما تفعل بوابة الموظف عند
    الاستعراض. الكتابة باسم موظفٍ لم يتحرّك تُفسد سجلًّا لا يُصحَّح.
    """
    from routes.portal_routes import get_portal_employee_id
    return get_portal_employee_id(for_write=for_write)


def _is_admin():
    u = get_current_user()
    return bool(u and u['role'] == 'admin')


def _visible_employee_ids(conn):
    """من يحقّ لصاحب الجلسة أن يتابعهم. None تعني الجميع (مسؤول)."""
    if _is_admin():
        return None
    me = _emp_id(for_write=False)
    if not me:
        return []
    rows = conn.execute(
        'SELECT id FROM employees WHERE manager_id = ? AND is_active = 1', (me,)).fetchall()
    return [r['id'] for r in rows]


def _may_view(conn, employee_id):
    allowed = _visible_employee_ids(conn)
    return allowed is None or int(employee_id) in allowed


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ====================================================== شاشة المندوب

@field_bp.route('/portal/trip')
@login_required
@module_required
def trip_screen():
    emp_id = _emp_id()
    conn = get_db_connection()
    field.init_schema(conn)

    # شاشة الرحلة لمن عُرِّف مندوبًا. وغيره لا يُقال له «ممنوع» بل
    # يُقال له إن حسابه ليس مندوبًا — فالفرق بينهما هو ما يفعله بعدها.
    if emp_id and not field.is_field_rep(conn, emp_id):
        return render_template('field_not_rep.html'), 403

    employee = None
    if emp_id:
        employee = conn.execute(
            'SELECT id, name, arabic_name FROM employees WHERE id = ?', (emp_id,)).fetchone()

    return render_template('portal/trip.html', employee=employee,
                           today=datetime.now().strftime('%Y-%m-%d'))


@field_bp.route('/portal/api/field/start', methods=['POST'])
@login_required
@module_required
def api_start():
    emp_id = _emp_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حسابك غير مرتبط بموظف'}), 400

    conn = get_db_connection()
    field.init_schema(conn)
    data = request.get_json(silent=True) or {}
    trip_id, created = field.open_trip(conn, emp_id, data.get('device_uuid'))

    return jsonify({
        'success': True, 'trip_id': trip_id, 'resumed': not created,
        'plan': field.day_plan(conn, emp_id),
        'summary': field.day_summary(conn, emp_id),
        'gap_seconds': field.GAP_SECONDS,
    })


@field_bp.route('/portal/api/field/track', methods=['POST'])
@login_required
@module_required
def api_track():
    emp_id = _emp_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حسابك غير مرتبط بموظف'}), 400

    conn = get_db_connection()
    data = request.get_json(silent=True) or {}
    trip_id = data.get('trip_id')

    # الرحلة لصاحب الجلسة: رقم رحلةٍ من طلبٍ يعني الكتابة في خط غيرك.
    trip = conn.execute('SELECT id, employee_id, ended_at FROM field_trips WHERE id = ?',
                        (trip_id,)).fetchone()
    if not trip or trip['employee_id'] != emp_id:
        return jsonify({'success': False, 'message': 'رحلة غير معروفة'}), 404
    if trip['ended_at']:
        return jsonify({'success': False, 'message': 'الرحلة منتهية'}), 400

    n = field.add_points(conn, trip_id, emp_id, data.get('points'))
    return jsonify({'success': True, 'accepted': n})


@field_bp.route('/portal/api/field/stop', methods=['POST'])
@login_required
@module_required
def api_stop():
    emp_id = _emp_id()
    conn = get_db_connection()
    data = request.get_json(silent=True) or {}

    trip = conn.execute('SELECT id, employee_id FROM field_trips WHERE id = ?',
                        (data.get('trip_id'),)).fetchone()
    if not trip or trip['employee_id'] != emp_id:
        return jsonify({'success': False, 'message': 'رحلة غير معروفة'}), 404

    field.close_trip(conn, trip['id'], (data.get('reason') or 'manual')[:32])
    return jsonify({'success': True, 'summary': field.day_summary(conn, emp_id)})


@field_bp.route('/portal/api/field/plan')
@login_required
@module_required
def api_plan():
    emp_id = _emp_id(for_write=False)
    if not emp_id:
        return jsonify({'success': False, 'message': 'حسابك غير مرتبط بموظف'}), 400
    conn = get_db_connection()
    field.init_schema(conn)
    return jsonify({'success': True,
                    'plan': field.day_plan(conn, emp_id),
                    'summary': field.day_summary(conn, emp_id),
                    'open_visit': dict(field.open_visit(conn, emp_id) or {}) or None})


@field_bp.route('/portal/api/field/token', methods=['POST'])
@login_required
@module_required
def api_token():
    """رمز الزيارة — يُطلب عند فتح شاشة المحطة، قبل الصورة مباشرةً."""
    emp_id = _emp_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حسابك غير مرتبط بموظف'}), 400

    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    if kind not in ('in', 'out'):
        return jsonify({'success': False, 'message': 'نوع الخطوة غير صحيح'}), 400

    conn = get_db_connection()
    station_id = data.get('station_id')
    if not conn.execute('SELECT 1 FROM field_stations WHERE id = ? AND is_active = 1',
                        (station_id,)).fetchone():
        return jsonify({'success': False, 'message': 'المحطة غير معروفة'}), 404

    field.purge_tokens(conn)
    return jsonify({'success': True,
                    'token': field.issue_token(conn, emp_id, station_id, kind),
                    'ttl_seconds': field.TOKEN_TTL_SECONDS})


def _check_photo():
    """(البايتات، رسالة الخطأ). الصورة إلزامية ومن الكاميرا."""
    f = request.files.get('photo')
    if not f:
        return None, 'الصورة مطلوبة — التقطها من الكاميرا'
    raw = f.read()
    if not raw:
        return None, 'الصورة فارغة'
    if len(raw) > field.MAX_PHOTO_BYTES:
        return None, 'الصورة أكبر من الحدّ المسموح'

    # يُفتح الملف فعلًا: ما لا يُفتح ليس صورة مهما قال نوعه المُعلَن.
    try:
        from PIL import Image
        Image.open(__import__('io').BytesIO(raw)).verify()
    except Exception:
        return None, 'الملف ليس صورة صالحة'

    if field.has_exif(raw):
        # كاميرا الصفحة تُخرج canvas بلا EXIF؛ ووجوده يعني ملفًا من
        # المعرض. دليل لا برهان — ومكتوب في الرسالة صراحةً.
        return None, ('الصورة ليست من كاميرا التطبيق (تحمل بيانات ملف). '
                      'افتح المحطة والتقطها الآن.')
    return raw, None


@field_bp.route('/portal/api/field/check-in', methods=['POST'])
@login_required
@module_required
def api_check_in():
    emp_id = _emp_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حسابك غير مرتبط بموظف'}), 400

    conn = get_db_connection()
    field.init_schema(conn)

    station_id = request.form.get('station_id', type=int)
    lat, lon = _f(request.form.get('latitude')), _f(request.form.get('longitude'))
    accuracy = _f(request.form.get('accuracy')) or 0
    trip_id = request.form.get('trip_id', type=int)

    station = conn.execute(
        'SELECT * FROM field_stations WHERE id = ? AND is_active = 1', (station_id,)).fetchone()
    if not station:
        return jsonify({'success': False, 'message': 'المحطة غير معروفة'}), 404

    today = datetime.now().strftime('%Y-%m-%d')
    assignment = conn.execute('''
        SELECT id FROM field_assignments
        WHERE employee_id = ? AND station_id = ? AND visit_date = ?
    ''', (emp_id, station_id, today)).fetchone()
    if not assignment:
        return jsonify({'success': False,
                        'message': 'هذه المحطة ليست في خطة يومك'}), 403

    if conn.execute('''SELECT 1 FROM field_visits
                       WHERE employee_id = ? AND station_id = ? AND DATE(check_in_at) = ?''',
                    (emp_id, station_id, today)).fetchone():
        return jsonify({'success': False, 'message': 'سُجّلت زيارة لهذه المحطة اليوم'}), 400

    if lat is None or lon is None:
        return jsonify({'success': False, 'message': 'الموقع مطلوب — فعّل GPS'}), 400

    distance = field.haversine(lat, lon, station['latitude'], station['longitude'])
    allowed = (station['radius_meters'] or 100) + max(0, min(accuracy, 30))
    if distance > allowed:
        return jsonify({'success': False,
                        'message': f'أنت خارج نطاق المحطة (تبعد {int(distance)} متراً)'}), 400

    ok, why = field.consume_token(conn, request.form.get('token'), emp_id, station_id, 'in')
    if not ok:
        return jsonify({'success': False, 'message': why}), 400

    raw, err = _check_photo()
    if err:
        return jsonify({'success': False, 'message': err}), 400

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO field_visits
            (trip_id, employee_id, station_id, assignment_id, check_in_at,
             check_in_lat, check_in_lon, check_in_distance, check_in_accuracy, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
    ''', (trip_id, emp_id, station_id, assignment['id'], now,
          lat, lon, distance, accuracy))
    visit_id = cur.lastrowid

    emp = conn.execute('SELECT name FROM employees WHERE id = ?', (emp_id,)).fetchone()
    try:
        field.save_visit_photo(conn, visit_id, 'in', raw, {
            'lat': lat, 'lon': lon, 'accuracy': accuracy, 'distance': distance,
            'station_name': station['name'], 'employee_name': emp['name'] if emp else '',
            'at': now,
        })
    except Exception as e:
        # لا زيارة بلا صورتها: الصفّ يُلغى بدل أن يبقى بلا دليل.
        conn.execute('DELETE FROM field_visits WHERE id = ?', (visit_id,))
        conn.commit()
        return jsonify({'success': False, 'message': f'تعذّر حفظ الصورة: {e}'}), 500

    conn.commit()
    return jsonify({'success': True, 'visit_id': visit_id,
                    'message': f"سُجّل الدخول إلى {station['name']} الساعة {now[11:16]}",
                    'distance': round(distance),
                    'summary': field.day_summary(conn, emp_id)})


@field_bp.route('/portal/api/field/check-out', methods=['POST'])
@login_required
@module_required
def api_check_out():
    emp_id = _emp_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حسابك غير مرتبط بموظف'}), 400

    conn = get_db_connection()
    visit_id = request.form.get('visit_id', type=int)
    lat, lon = _f(request.form.get('latitude')), _f(request.form.get('longitude'))
    accuracy = _f(request.form.get('accuracy')) or 0

    visit = conn.execute('''
        SELECT v.*, s.name AS station_name, s.latitude, s.longitude, s.radius_meters,
               s.min_minutes
        FROM field_visits v JOIN field_stations s ON s.id = v.station_id
        WHERE v.id = ?
    ''', (visit_id,)).fetchone()

    if not visit or visit['employee_id'] != emp_id:
        return jsonify({'success': False, 'message': 'الزيارة غير معروفة'}), 404
    if visit['status'] != 'open':
        return jsonify({'success': False, 'message': 'الزيارة مغلقة بالفعل'}), 400
    if lat is None or lon is None:
        return jsonify({'success': False, 'message': 'الموقع مطلوب — فعّل GPS'}), 400

    started = field._parse(visit['check_in_at'])
    minutes = (datetime.now() - started).total_seconds() / 60 if started else 0
    if visit['min_minutes'] and minutes < visit['min_minutes']:
        left = int(visit['min_minutes'] - minutes) + 1
        return jsonify({'success': False,
                        'message': f'أقلّ مدة للوقوف {visit["min_minutes"]} دقيقة — بقي {left}'}), 400

    distance = field.haversine(lat, lon, visit['latitude'], visit['longitude'])

    ok, why = field.consume_token(conn, request.form.get('token'),
                                  emp_id, visit['station_id'], 'out')
    if not ok:
        return jsonify({'success': False, 'message': why}), 400

    raw, err = _check_photo()
    if err:
        return jsonify({'success': False, 'message': err}), 400

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    emp = conn.execute('SELECT name FROM employees WHERE id = ?', (emp_id,)).fetchone()
    try:
        field.save_visit_photo(conn, visit_id, 'out', raw, {
            'lat': lat, 'lon': lon, 'accuracy': accuracy, 'distance': distance,
            'station_name': visit['station_name'],
            'employee_name': emp['name'] if emp else '', 'at': now,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'تعذّر حفظ الصورة: {e}'}), 500

    conn.execute('''
        UPDATE field_visits
        SET check_out_at = ?, check_out_lat = ?, check_out_lon = ?,
            check_out_distance = ?, check_out_accuracy = ?, status = 'closed'
        WHERE id = ?
    ''', (now, lat, lon, distance, accuracy, visit_id))
    conn.commit()

    return jsonify({'success': True,
                    'message': f"سُجّل الخروج من {visit['station_name']} بعد {int(minutes)} دقيقة",
                    'minutes': int(minutes),
                    'summary': field.day_summary(conn, emp_id)})


# ====================================================== شاشة المتابعة

@field_bp.route('/field')
@login_required
@module_required
def monitor():
    conn = get_db_connection()
    field.init_schema(conn)
    return render_template('field_monitor.html',
                           today=datetime.now().strftime('%Y-%m-%d'),
                           is_admin=_is_admin())


@field_bp.route('/field/setup')
@login_required
@module_required
def setup():
    """إدارة المحطات وخطة اليوم — لمسؤول النظام.

    الصفحة تُعرض للمسؤول وحده، ومسارات الكتابة تتحقّق من ذلك بنفسها؛
    إخفاء الشاشة ليس صلاحية.
    """
    if not _is_admin():
        return render_template('field_setup.html', denied=True,
                               employees=[], today=''), 403

    conn = get_db_connection()
    field.init_schema(conn)
    # قوائم الإسناد تعرض المناديب وحدهم: إسناد منطقة إلى موظف مكتب
    # خطأٌ يُكتشف بعد أسبوع حين لا يظهر في شاشة المتابعة.
    return render_template('field_setup.html', denied=False,
                           reps=field.field_reps(conn),
                           all_employees=[dict(e) for e in conn.execute(
                               'SELECT id, name, employee_number, work_mode '
                               'FROM employees WHERE is_active = 1 ORDER BY name')],
                           modes=field.WORK_MODES,
                           today=datetime.now().strftime('%Y-%m-%d'))


@field_bp.route('/api/field/territories', methods=['GET', 'POST'])
@login_required
@module_required
def api_territories():
    """المناطق: مضلَّع من عدة نقاط، ومن يغطّيها من المناديب."""
    from utils import geo

    conn = get_db_connection()
    field.init_schema(conn)

    if request.method == 'GET':
        rows = conn.execute(
            'SELECT * FROM field_territories ORDER BY is_active DESC, name').fetchall()
        out = []
        for r in rows:
            d = dict(r)
            pts, _ = geo.parse_polygon(r['polygon'])
            d['points'] = pts or []
            d['area_km2'] = round(geo.area_km2(pts), 2) if pts else 0
            d['members'] = [dict(m) for m in conn.execute('''
                SELECT e.id, e.name FROM field_territory_members m
                JOIN employees e ON e.id = m.employee_id
                WHERE m.territory_id = ? ORDER BY e.name''', (r['id'],))]
            d['station_count'] = conn.execute(
                'SELECT COUNT(*) FROM field_stations WHERE territory_id = ? AND is_active = 1',
                (r['id'],)).fetchone()[0]
            out.append(d)
        return jsonify({'success': True, 'territories': out})

    denied = _admin_only()
    if denied:
        return denied

    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'اسم المنطقة مطلوب'}), 400

    pts, why = geo.parse_polygon(d.get('points'))
    if not pts:
        return jsonify({'success': False, 'message': why}), 400

    color = (d.get('color') or '#0d6efd')[:9]
    cur = conn.cursor()
    if d.get('id'):
        cur.execute('''UPDATE field_territories SET name=?, polygon=?, color=?,
                       is_active=?, notes=? WHERE id=?''',
                    (name, geo.dumps_polygon(pts), color,
                     1 if d.get('is_active', 1) else 0, d.get('notes'), d['id']))
        tid = int(d['id'])
    else:
        cur.execute('''INSERT INTO field_territories (name, polygon, color, notes)
                       VALUES (?, ?, ?, ?)''',
                    (name, geo.dumps_polygon(pts), color, d.get('notes')))
        tid = cur.lastrowid

    if isinstance(d.get('member_ids'), list):
        # الإسناد يُستبدل لا يُضاف إليه: الإضافة تُبقي مندوبًا نُقل عنها.
        conn.execute('DELETE FROM field_territory_members WHERE territory_id = ?', (tid,))
        for emp in d['member_ids'][:200]:
            conn.execute('''INSERT OR IGNORE INTO field_territory_members
                            (territory_id, employee_id) VALUES (?, ?)''', (tid, emp))

    conn.commit()
    return jsonify({'success': True, 'territory_id': tid,
                    'area_km2': round(geo.area_km2(pts), 2)})


@field_bp.route('/api/field/schedule', methods=['GET', 'POST'])
@login_required
@module_required
def api_schedule():
    """جدول المندوب الأسبوعي — نسخةً لها تاريخ بداية."""
    conn = get_db_connection()
    field.init_schema(conn)

    if request.method == 'GET':
        emp = request.args.get('employee_id', type=int)
        if not emp:
            return jsonify({'success': False, 'message': 'الموظف مطلوب'}), 400
        if not _may_view(conn, emp):
            return jsonify({'success': False, 'message': 'لا تملك متابعة هذا الموظف'}), 403

        history = field.schedule_history(conn, emp)
        sid = request.args.get('schedule_id', type=int)
        if not sid:
            day = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
            cur = field.active_schedule(conn, emp, day)
            sid = cur['id'] if cur else (history[0]['id'] if history else None)

        return jsonify({
            'success': True,
            'weekdays': field.WEEKDAYS,
            'history': history,
            'schedule_id': sid,
            'grid': field.schedule_grid(conn, sid) if sid else {},
        })

    denied = _admin_only()
    if denied:
        return denied

    d = request.get_json(silent=True) or {}
    emp = d.get('employee_id')
    starts_on = d.get('starts_on')
    if not emp or not starts_on:
        return jsonify({'success': False, 'message': 'الموظف وتاريخ البداية مطلوبان'}), 400

    days = d.get('days') or {}
    if not isinstance(days, dict):
        return jsonify({'success': False, 'message': 'صيغة الأيام غير صحيحة'}), 400

    # محطات خارج مناطقه لا تدخل جدوله — كالخطة اليومية تمامًا.
    terrs = {t['id'] for t in field.territories_of(conn, emp)}
    wanted = {int(s) for lst in days.values() for s in (lst or [])}
    if terrs and wanted:
        marks = ','.join('?' * len(wanted))
        rows = conn.execute(
            f'SELECT id, name, territory_id FROM field_stations WHERE id IN ({marks})',
            list(wanted)).fetchall()
        stray = [r['name'] for r in rows if r['territory_id'] not in terrs]
        if stray:
            return jsonify({'success': False,
                            'message': 'محطات خارج مناطق هذا المندوب: '
                                       + '، '.join(stray[:5])}), 400

    try:
        sid = field.save_schedule(conn, emp, starts_on, days, d.get('name'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    # اليوم الجاري يُولَّد فورًا ليراه المندوب دون انتظار
    today = datetime.now().strftime('%Y-%m-%d')
    generated = field.generate_day(conn, emp, today, force=True)

    return jsonify({'success': True, 'schedule_id': sid,
                    'generated_today': generated,
                    'history': field.schedule_history(conn, emp)})


@field_bp.route('/api/field/schedule/preview')
@login_required
@module_required
def api_schedule_preview():
    """ما الذي سينزل في الأيام القادمة؟ — قبل الحفظ لا بعده."""
    conn = get_db_connection()
    emp = request.args.get('employee_id', type=int)
    if not emp or not _may_view(conn, emp):
        return jsonify({'success': False, 'message': 'الموظف مطلوب'}), 400

    from datetime import timedelta
    start = datetime.now()
    out = []
    for i in range(14):
        d = start + timedelta(days=i)
        day = d.strftime('%Y-%m-%d')
        sched = field.active_schedule(conn, emp, day)
        names = ([s['name'] for s in
                  field.schedule_stations(conn, sched['id'], field.weekday_index(d))]
                 if sched else [])
        out.append({'date': day, 'weekday': field.WEEKDAYS[field.weekday_index(d)],
                    'schedule_id': sched['id'] if sched else None,
                    'stations': names})
    return jsonify({'success': True, 'days': out})


@field_bp.route('/api/field/territory/<int:territory_id>', methods=['DELETE'])
@login_required
@module_required
def api_territory_delete(territory_id):
    """تعطيل لا حذف — كالمحطات، للسبب نفسه."""
    denied = _admin_only()
    if denied:
        return denied
    conn = get_db_connection()
    conn.execute('UPDATE field_territories SET is_active = 0 WHERE id = ?', (territory_id,))
    conn.commit()
    return jsonify({'success': True})


@field_bp.route('/api/field/station/<int:station_id>', methods=['DELETE'])
@login_required
@module_required
def api_station_delete(station_id):
    """تعطيل لا حذف: الزيارات المسجّلة تشير إلى المحطة، وحذفها يترك
    تاريخًا بلا أسماء."""
    denied = _admin_only()
    if denied:
        return denied

    conn = get_db_connection()
    conn.execute('UPDATE field_stations SET is_active = 0 WHERE id = ?', (station_id,))
    conn.commit()
    return jsonify({'success': True})


@field_bp.route('/api/field/reps')
@login_required
@module_required
def api_reps():
    """من أتابعهم، وحالة كلٍّ اليوم."""
    conn = get_db_connection()
    field.init_schema(conn)
    day = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')

    allowed = _visible_employee_ids(conn)
    if allowed is None:
        rows = conn.execute(
            'SELECT id, name, employee_number FROM employees WHERE is_active = 1 '
            'ORDER BY name').fetchall()
    elif allowed:
        marks = ','.join('?' * len(allowed))
        rows = conn.execute(
            f'SELECT id, name, employee_number FROM employees WHERE id IN ({marks}) '
            'ORDER BY name', allowed).fetchall()
    else:
        rows = []

    out = []
    for r in rows:
        summary = field.day_summary(conn, r['id'], day)
        if not summary['stations_total'] and not conn.execute(
                'SELECT 1 FROM field_trips WHERE employee_id = ? AND trip_date = ?',
                (r['id'], day)).fetchone():
            continue         # ليس مندوبًا اليوم

        trip = conn.execute('''
            SELECT id, started_at, ended_at, point_count FROM field_trips
            WHERE employee_id = ? AND trip_date = ? ORDER BY id DESC LIMIT 1
        ''', (r['id'], day)).fetchone()

        last = conn.execute('''
            SELECT latitude, longitude, recorded_at, accuracy, speed, heading
            FROM field_track_points
            WHERE employee_id = ? AND DATE(recorded_at) = ?
            ORDER BY recorded_at DESC LIMIT 1
        ''', (r['id'], day)).fetchone()

        # منذ متى لم نسمع منه؟ هذا ما يفرّق «متحرّك الآن» عن «علامة
        # على الخريطة منذ ساعتين» — ونقطةٌ قديمة تبدو كموقع حاليّ هي
        # أسوأ ما في شاشة متابعة.
        stale_minutes = None
        if last:
            t = field._parse(last['recorded_at'])
            if t:
                stale_minutes = int((datetime.now() - t).total_seconds() / 60)

        if trip and trip['ended_at']:
            state = 'ended'
        elif stale_minutes is None:
            state = 'not_started'
        elif stale_minutes <= 5:
            state = 'live'
        elif stale_minutes <= 30:
            state = 'idle'
        else:
            state = 'lost'

        # مسافة اليوم من المسار نفسه: الفجوات لا تُحسب سيرًا.
        pts = conn.execute('''
            SELECT latitude, longitude, accuracy, recorded_at FROM field_track_points
            WHERE employee_id = ? AND DATE(recorded_at) = ? ORDER BY recorded_at
        ''', (r['id'], day)).fetchall()
        _segs, stats = field.build_path([dict(p) for p in pts])

        open_v = conn.execute('''
            SELECT v.id, v.check_in_at, s.name FROM field_visits v
            JOIN field_stations s ON s.id = v.station_id
            WHERE v.employee_id = ? AND v.status = 'open'
            ORDER BY v.id DESC LIMIT 1
        ''', (r['id'],)).fetchone()

        out.append({
            'employee_id': r['id'], 'name': r['name'],
            'employee_number': r['employee_number'],
            'trip_id': trip['id'] if trip else None,
            'started_at': trip['started_at'] if trip else None,
            'ended_at': trip['ended_at'] if trip else None,
            'points': trip['point_count'] if trip else 0,
            'last_seen': last['recorded_at'] if last else None,
            'last_lat': last['latitude'] if last else None,
            'last_lon': last['longitude'] if last else None,
            'accuracy': last['accuracy'] if last else None,
            'heading': last['heading'] if last else None,
            'speed': last['speed'] if last else None,
            'stale_minutes': stale_minutes,
            'state': state,
            'distance_meters': stats['distance_meters'],
            'gaps': stats['gaps'],
            'jumps': stats['jumps'],
            'at_station': open_v['name'] if open_v else None,
            'at_station_since': open_v['check_in_at'] if open_v else None,
            'summary': summary,
        })

    return jsonify({'success': True, 'date': day, 'reps': out})


@field_bp.route('/api/field/rep/<int:employee_id>')
@login_required
@module_required
def api_rep_day(employee_id):
    """خط السير والزيارات ليوم واحد."""
    conn = get_db_connection()
    if not _may_view(conn, employee_id):
        return jsonify({'success': False, 'message': 'لا تملك متابعة هذا الموظف'}), 403

    day = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')

    pts = conn.execute('''
        SELECT latitude, longitude, accuracy, recorded_at FROM field_track_points
        WHERE employee_id = ? AND DATE(recorded_at) = ? ORDER BY recorded_at ASC
    ''', (employee_id, day)).fetchall()
    segments, stats = field.build_path([dict(p) for p in pts])

    visits = conn.execute('''
        SELECT v.*, s.name AS station_name, s.customer_name,
               s.latitude AS station_lat, s.longitude AS station_lon, s.radius_meters
        FROM field_visits v JOIN field_stations s ON s.id = v.station_id
        WHERE v.employee_id = ? AND DATE(v.check_in_at) = ?
        ORDER BY v.check_in_at ASC
    ''', (employee_id, day)).fetchall()

    visit_list = []
    for v in visits:
        d = dict(v)
        photos = conn.execute('''
            SELECT id, kind, captured_at, accuracy, distance_meters, had_exif, sha256
            FROM field_visit_photos WHERE visit_id = ? ORDER BY id ASC
        ''', (v['id'],)).fetchall()
        d['photos'] = [dict(p) for p in photos]
        if v['check_in_at'] and v['check_out_at']:
            a, b = field._parse(v['check_in_at']), field._parse(v['check_out_at'])
            d['minutes'] = int((b - a).total_seconds() / 60) if a and b else None
        visit_list.append(d)

    track = [(p['latitude'], p['longitude']) for p in pts]
    out_n, out_ratio = field.outside_own_territory(conn, employee_id, track)
    terrs = field.territories_of(conn, employee_id)

    return jsonify({
        'success': True, 'date': day, 'employee_id': employee_id,
        'territories': [{'id': t['id'], 'name': t['name'], 'color': t['color'],
                         'points': t['points']} for t in terrs],
        'outside': {'points': out_n, 'ratio': out_ratio},
        'segments': [{
            'kind': s['kind'],
            'points': [[p['lat'], p['lon']] for p in s['points']],
            'seconds': s.get('seconds'), 'meters': s.get('meters'), 'kmh': s.get('kmh'),
        } for s in segments],
        'stats': stats,
        'visits': visit_list,
        'plan': field.day_plan(conn, employee_id, day),
        'summary': field.day_summary(conn, employee_id, day),
    })


@field_bp.route('/api/field/photo/<int:photo_id>')
@login_required
@module_required
def api_photo(photo_id):
    """الصورة من مسار محروس.

    لو قُدّمت من مجلد ثابت لكفى تخمينُ اسم ملف لرؤية زيارة موظفٍ لا
    يملك الرائي متابعته — والصور فيها وجوه ومقارّ عملاء.
    """
    conn = get_db_connection()
    row = conn.execute('''
        SELECT p.file_path, v.employee_id FROM field_visit_photos p
        JOIN field_visits v ON v.id = p.visit_id WHERE p.id = ?
    ''', (photo_id,)).fetchone()

    if not row:
        return jsonify({'success': False, 'message': 'غير موجودة'}), 404

    me = _emp_id(for_write=False)
    if row['employee_id'] != me and not _may_view(conn, row['employee_id']):
        return jsonify({'success': False, 'message': 'لا تملك رؤية هذه الصورة'}), 403

    base = os.path.abspath(field.photos_dir())
    full = os.path.abspath(os.path.join(base, row['file_path']))
    # المسار من القاعدة، لكنّه يُتحقَّق منه: صفٌّ مكتوب بمسار خارجي
    # يقرأ ملفات الخادم.
    if os.path.commonpath([full, base]) != base or not os.path.exists(full):
        return jsonify({'success': False, 'message': 'الملف مفقود'}), 404

    return send_file(full, mimetype='image/jpeg')


# ------------------------------------------------------ إدارة المحطات

def _admin_only():
    return None if _is_admin() else (
        jsonify({'success': False, 'message': 'هذه الصفحة لمسؤول النظام'}), 403)


@field_bp.route('/api/field/stations', methods=['GET', 'POST'])
@login_required
@module_required
def api_stations():
    conn = get_db_connection()
    field.init_schema(conn)

    if request.method == 'GET':
        # ?employee_id= يعطي محطات مناطقه وحدها: الخطة تُبنى مما يخصّه،
        # لا من كل محطة في الشركة.
        emp = request.args.get('employee_id', type=int)
        if emp:
            terrs = [t['id'] for t in field.territories_of(conn, emp)]
            if not terrs:
                return jsonify({'success': True, 'stations': [],
                                'message': 'لا منطقة مُسنَدة لهذا المندوب'})
            marks = ','.join('?' * len(terrs))
            rows = conn.execute(
                f'''SELECT s.*, t.name AS territory_name, t.color AS territory_color
                    FROM field_stations s
                    LEFT JOIN field_territories t ON t.id = s.territory_id
                    WHERE s.territory_id IN ({marks})
                    ORDER BY s.is_active DESC, s.name''', terrs).fetchall()
        else:
            rows = conn.execute(
                '''SELECT s.*, t.name AS territory_name, t.color AS territory_color
                   FROM field_stations s
                   LEFT JOIN field_territories t ON t.id = s.territory_id
                   ORDER BY s.is_active DESC, s.name''').fetchall()
        return jsonify({'success': True, 'stations': [dict(r) for r in rows]})

    denied = _admin_only()
    if denied:
        return denied

    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    lat, lon = _f(d.get('latitude')), _f(d.get('longitude'))
    if not name or lat is None or lon is None:
        return jsonify({'success': False, 'message': 'الاسم والإحداثيات مطلوبة'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({'success': False, 'message': 'إحداثيات خارج المدى'}), 400

    radius = int(_f(d.get('radius_meters')) or 100)
    radius = max(20, min(radius, 5000))

    # المنطقة: إمّا يختارها المستخدم، أو تُستنتَج من موقع المحطة نفسه.
    # والاستنتاج هو الحالة الغالبة: من يضع دبّوسًا داخل مضلَّع السالمية
    # يقصد السالمية، ولا معنى لأن يُسأل عنها ثانيةً.
    territory_id = d.get('territory_id')
    if territory_id in (None, '', 0, '0'):
        found = field.station_territory(conn, lat, lon)
        territory_id = found['id'] if found else None
    else:
        territory_id = int(territory_id)

    cur = conn.cursor()
    if d.get('id'):
        cur.execute('''
            UPDATE field_stations SET name=?, customer_name=?, address=?, latitude=?,
                   longitude=?, radius_meters=?, min_minutes=?, is_active=?, notes=?,
                   territory_id=?
            WHERE id=?
        ''', (name, d.get('customer_name'), d.get('address'), lat, lon, radius,
              int(_f(d.get('min_minutes')) or 0),
              1 if d.get('is_active', 1) else 0, d.get('notes'), territory_id, d['id']))
        sid = d['id']
    else:
        cur.execute('''
            INSERT INTO field_stations
                (name, customer_name, address, latitude, longitude, radius_meters,
                 min_minutes, is_active, notes, territory_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, d.get('customer_name'), d.get('address'), lat, lon, radius,
              int(_f(d.get('min_minutes')) or 0),
              1 if d.get('is_active', 1) else 0, d.get('notes'), territory_id))
        sid = cur.lastrowid
    conn.commit()
    return jsonify({'success': True, 'station_id': sid, 'territory_id': territory_id})


@field_bp.route('/api/field/assignments', methods=['GET', 'POST'])
@login_required
@module_required
def api_assignments():
    conn = get_db_connection()
    field.init_schema(conn)
    day = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')

    if request.method == 'GET':
        emp = request.args.get('employee_id', type=int)
        if emp and not _may_view(conn, emp):
            return jsonify({'success': False, 'message': 'لا تملك متابعة هذا الموظف'}), 403
        if emp:
            return jsonify({'success': True, 'date': day,
                            'plan': field.day_plan(conn, emp, day)})
        rows = conn.execute('''
            SELECT a.*, s.name AS station_name, e.name AS employee_name
            FROM field_assignments a
            JOIN field_stations s ON s.id = a.station_id
            JOIN employees e ON e.id = a.employee_id
            WHERE a.visit_date = ? ORDER BY e.name, a.sort_order
        ''', (day,)).fetchall()
        return jsonify({'success': True, 'date': day,
                        'assignments': [dict(r) for r in rows]})

    denied = _admin_only()
    if denied:
        return denied

    d = request.get_json(silent=True) or {}
    emp = d.get('employee_id')
    stations = d.get('station_ids') or []
    visit_date = (d.get('visit_date') or day)[:10]

    if not emp or not isinstance(stations, list):
        return jsonify({'success': False, 'message': 'الموظف والمحطات مطلوبة'}), 400

    # محطة خارج مناطق المندوب لا تُسنَد إليه: الخطة التي لا يستطيع
    # تنفيذها تظهر في آخر اليوم «تخلّفًا» وهي ليست منه.
    terrs = {t['id'] for t in field.territories_of(conn, emp)}
    if terrs and stations:
        marks = ','.join('?' * len(stations))
        rows = conn.execute(
            f'SELECT id, name, territory_id FROM field_stations WHERE id IN ({marks})',
            list(stations)).fetchall()
        stray = [r['name'] for r in rows if r['territory_id'] not in terrs]
        if stray:
            return jsonify({
                'success': False,
                'message': 'محطات خارج مناطق هذا المندوب: ' + '، '.join(stray[:5])
            }), 400

    # الخطة تُستبدل لا تُضاف إليها: الإضافة تترك محطات يومٍ ملغًى قائمة.
    conn.execute('DELETE FROM field_assignments WHERE employee_id = ? AND visit_date = ?',
                 (emp, visit_date))
    for i, sid in enumerate(stations[:100]):
        conn.execute('''
            INSERT OR IGNORE INTO field_assignments
                (employee_id, station_id, visit_date, sort_order) VALUES (?, ?, ?, ?)
        ''', (emp, sid, visit_date, i))
    conn.commit()

    return jsonify({'success': True, 'count': len(stations),
                    'plan': field.day_plan(conn, emp, visit_date)})


@field_bp.route('/api/field/work-mode', methods=['POST'])
@login_required
@module_required
def api_work_mode():
    """نمط عمل موظف: مكتب، أو مندوب، أو الاثنان.

    وهو ما يقرّر ما يراه — لا صلاحيةَ أمنية، فلا يُفتح به شيء مغلق.
    لكنه يُكتب بيد المسؤول وحده: من يجعل نفسه مندوبًا يفتح على نفسه
    تتبّع موقعه، ومن يجعل غيره كذلك يفتحه على غيره.
    """
    denied = _admin_only()
    if denied:
        return denied

    d = request.get_json(silent=True) or {}
    emp = d.get('employee_id')
    mode = d.get('work_mode')
    if not emp:
        return jsonify({'success': False, 'message': 'الموظف مطلوب'}), 400

    conn = get_db_connection()
    if not conn.execute('SELECT 1 FROM employees WHERE id = ?', (emp,)).fetchone():
        return jsonify({'success': False, 'message': 'موظف غير معروف'}), 404

    try:
        field.set_work_mode(conn, emp, mode)
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    return jsonify({'success': True, 'work_mode': mode,
                    'label': field.WORK_MODES[mode]})


# ==================================================== خرائط بلا إنترنت

# حدود دولة الكويت تقريبًا — جاهزة لمن يريد البلد كلّه لا منطقةً.
KUWAIT_BBOX = (28.52, 46.55, 30.10, 48.43)


@field_bp.route('/api/field/tile/<int:z>/<int:x>/<int:y>.png')
@login_required
@module_required
def api_tile(z, x, y):
    """مربّع خريطة: من المخزن، وإلا من المصدر، وإلا 404.

    الخريطة تطلبه بدل أن تطلب المزوّد مباشرةً — فالمفتاح يبقى في
    الخادم ولا يصل إلى المتصفح.

    والحفظ **مشروط**: المصدر الافتراضي يُحفظ فيعمل في المرّة التالية
    بلا اتصال، والمصدر الخارجي يُمرَّر بلا حفظ حتى يُقال إن رخصته
    تسمح. لأن أكثر المزوّدين التجاريين يمنعون التخزين الوسيط، والحفظ
    الصامت يجعل العميل مخالفًا وهو لا يدري.
    """
    from flask import Response
    from utils import tiles

    data = tiles.read(z, x, y)
    if data is None:
        data = tiles.fetch(z, x, y)
        if data and tiles.cache_allowed(get_db_connection()):
            tiles.store(z, x, y, data)

    if not data:
        # 404 لا صورة فارغة: Leaflet يعرف معناها ويترك المربّع شفّافًا،
        # والصورة الفارغة تُخزَّن في المتصفح فتبقى بيضاء بعد عودة
        # الاتصال.
        return jsonify({'success': False}), 404

    return Response(data, mimetype='image/png',
                    headers={'Cache-Control': 'public, max-age=604800'})


@field_bp.route('/api/field/maps', methods=['GET', 'POST'])
@login_required
@module_required
def api_maps():
    """حالة مخزن الخرائط، وتنزيل رقعة، وإيقافه."""
    from utils import geo, tiles

    conn = get_db_connection()
    _url, attr, is_osm = tiles.tile_source(conn)

    if request.method == 'GET':
        # رقع مناطق العميل: كل منطقة حدودُ مضلَّعها.
        boxes, names = [], []
        for t in conn.execute(
                'SELECT name, polygon FROM field_territories WHERE is_active = 1'):
            pts, _ = geo.parse_polygon(t['polygon'])
            if pts:
                boxes.append(geo.bounds(pts))
                names.append(t['name'])

        want_t, have_t = tiles.estimate(boxes) if boxes else (0, 0)
        kw_t, kw_have = tiles.estimate([KUWAIT_BBOX], zmax=14)

        return jsonify({
            'success': True,
            'cache': tiles.cache_stats(),
            # الرابط **لا** يخرج: مفتاح المزوّد فيه، وهذا المسار مفتوح
            # لكل من دخل لا للمسؤول وحده. والواجهة لا تستعمله أصلًا —
            # المربّعات تُطلب من وسيطنا. ويكفي المضيف للتشخيص.
            'source': {'host': tiles.source_host(conn), 'attribution': attr,
                       'is_default': is_osm, 'max_tiles': tiles.max_tiles(conn),
                       'cache_allowed': tiles.cache_allowed(conn)},
            'territories': {'count': len(boxes), 'names': names,
                            'tiles': want_t, 'have': have_t},
            'kuwait': {'tiles': kw_t, 'have': kw_have, 'zmax': 14},
            'zoom': {'min': tiles.MIN_ZOOM, 'max': tiles.MAX_ZOOM},
            'progress': tiles.progress(),
        })

    denied = _admin_only()
    if denied:
        return denied

    d = request.get_json(silent=True) or {}
    action = d.get('action')

    if action == 'stop':
        tiles.stop()
        return jsonify({'success': True, 'message': 'أُوقف'})

    scope = d.get('scope', 'territories')
    zmax = int(d.get('zmax') or tiles.MAX_ZOOM)
    zmax = max(tiles.MIN_ZOOM, min(zmax, 17))

    if scope == 'kuwait':
        boxes = [KUWAIT_BBOX]
    else:
        boxes = []
        for t in conn.execute(
                'SELECT polygon FROM field_territories WHERE is_active = 1'):
            pts, _ = geo.parse_polygon(t['polygon'])
            if pts:
                boxes.append(geo.bounds(pts))
        if not boxes:
            return jsonify({'success': False,
                            'message': 'لا مناطق مرسومة — ارسم منطقة أولًا'}), 400

    started, msg = tiles.download(boxes, zmax=zmax, conn=conn)
    return jsonify({'success': started, 'message': msg,
                    'progress': tiles.progress()})


@field_bp.route('/api/field/maps/export')
@login_required
@module_required
def api_maps_export():
    """حزمة المربّعات ملفًّا واحدًا — تُحمل إلى تركيب بلا إنترنت."""
    denied = _admin_only()
    if denied:
        return denied

    from flask import Response
    from utils import tiles

    stats = tiles.cache_stats()
    if not stats['tiles']:
        return jsonify({'success': False, 'message': 'المخزن فارغ'}), 400

    tmp = os.path.join(os.path.dirname(tiles.cache_dir()),
                       f'maps-{os.getpid()}-{os.urandom(4).hex()}.zip')
    try:
        tiles.export_pack(tmp)
        with open(tmp, 'rb') as f:
            data = f.read()
    finally:
        for p in (tmp, tmp + '.part'):
            if os.path.exists(p):
                os.remove(p)

    name = f"map-tiles-{datetime.now().strftime('%Y-%m-%d')}.zip"
    return Response(data, mimetype='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{name}"',
        'Content-Length': str(len(data)), 'Cache-Control': 'no-store'})


@field_bp.route('/api/field/maps/import', methods=['POST'])
@login_required
@module_required
def api_maps_import():
    denied = _admin_only()
    if denied:
        return denied

    from utils import tiles

    f = request.files.get('pack')
    if not f:
        return jsonify({'success': False, 'message': 'اختر ملف الحزمة'}), 400

    tmp = os.path.join(os.path.dirname(tiles.cache_dir()),
                       f'inpack-{os.urandom(4).hex()}.zip')
    try:
        f.save(tmp)
        added, msg = tiles.import_pack(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    return jsonify({'success': added > 0, 'message': msg,
                    'cache': tiles.cache_stats()})


# ========================================================== المباني

@field_bp.route('/api/field/buildings')
@login_required
@module_required
def api_buildings():
    """مباني رقعة: للرسم على الخريطة عند التقريب العالي."""
    from utils import buildings

    def _n(k):
        return _f(request.args.get(k))

    mn_la, mn_lo, mx_la, mx_lo = _n('min_lat'), _n('min_lon'), _n('max_lat'), _n('max_lon')
    if None in (mn_la, mn_lo, mx_la, mx_lo):
        return jsonify({'success': False, 'message': 'حدود الرقعة مطلوبة'}), 400
    if not (-90 <= mn_la <= 90 and -90 <= mx_la <= 90):
        return jsonify({'success': False, 'message': 'إحداثيات خارج المدى'}), 400

    found, fetched = buildings.in_bbox(mn_la, mn_lo, mx_la, mx_lo)
    return jsonify({'success': True, 'buildings': found,
                    'count': len(found), 'fetched': fetched})


@field_bp.route('/api/field/place')
@login_required
@module_required
def api_place():
    """أيّ مبنًى/فرعٍ عند هذه النقطة — للسؤال عن زيارة بعينها."""
    from utils import buildings

    lat, lon = _f(request.args.get('lat')), _f(request.args.get('lon'))
    if lat is None or lon is None:
        return jsonify({'success': False, 'message': 'الإحداثيات مطلوبة'}), 400

    hits = buildings.at_point(lat, lon)
    return jsonify({'success': True, 'places': hits,
                    'summary': buildings.describe(lat, lon)})
