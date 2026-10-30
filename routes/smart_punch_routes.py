# -*- coding: utf-8 -*-
"""الكشك (رقم سرّي + سيلفي)، وبصمة QR، وشاشة «الحضور الذكي» (utils/smart_punch)."""
from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, \
    send_from_directory, session, url_for

from utils import smart_punch as S
from utils.auth import login_required
from utils.db import get_db_connection, get_setting, set_setting
from utils.rbac import require_permission

smart_punch_bp = Blueprint('smart_punch', __name__)
ADMIN_PERMISSION = 'attendance.edit'


def _kiosk_or_404(token):
    k = S.kiosk_by_token(get_db_connection(), token)
    if not k:
        abort(404)
    return k


# ------------------------------------------------------------ الكشك (من غير تسجيل دخول — الرمز هو المفتاح)

@smart_punch_bp.route('/kiosk/<token>')
def kiosk(token):
    k = _kiosk_or_404(token)
    S.touch(get_db_connection(), k['id'])
    return render_template('kiosk.html', k=k, window=S.QR_WINDOW)


@smart_punch_bp.route('/kiosk/<token>/qr.svg')
def kiosk_qr(token):
    k = _kiosk_or_404(token)
    if not k['allow_qr']:
        abort(404)
    url = request.host_url.rstrip('/') + url_for('smart_punch.qr_page', kid=k['id'], code=S.qr_code(k))
    S.touch(get_db_connection(), k['id'])
    return Response(S.qr_svg(url), mimetype='image/svg+xml', headers={'Cache-Control': 'no-store'})


@smart_punch_bp.route('/kiosk/<token>/punch', methods=['POST'])
def kiosk_punch(token):
    k = _kiosk_or_404(token)
    if not k['allow_pin']:
        abort(404)
    conn = get_db_connection()
    data = request.get_json(silent=True) or {}
    emp_id, err = S.verify_pin(conn, data.get('employee_number'), data.get('pin'))
    if err:
        return jsonify(success=False, message=err), 403
    photo = None
    if k['require_selfie']:
        try:
            photo = S.save_selfie(data.get('photo'), emp_id)
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400
        if not photo:
            return jsonify(success=False, message='الكاميرا لازم تلتقط صورتك — اسمح للكاميرا وجرّب تاني'), 400
    note = f'كشك «{k["name"]}»' + (f' | صورة: {photo}' if photo else '')
    ok, msg = S.record(conn, emp_id, 'kiosk', note)
    return jsonify(success=ok, message=msg), (200 if ok else 409)


# ------------------------------------------------------------ QR من موبايل الموظّف

def _own_employee():
    from routes.hr_request_routes import _own_employee_id
    return _own_employee_id()


@smart_punch_bp.route('/portal/qr/<int:kid>/<code>')
@login_required
def qr_page(kid, code):
    return render_template('kiosk_qr_confirm.html', kid=kid, code=code)


@smart_punch_bp.route('/portal/api/qr-punch', methods=['POST'])
@login_required
def qr_punch():
    emp = _own_employee()
    if not emp:
        return jsonify(success=False, message='الحساب ده مش مربوط بموظف'), 403
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    S.ensure_schema(conn)
    k = conn.execute('SELECT * FROM kiosks WHERE id = ? AND is_active = 1 AND allow_qr = 1',
                     (data.get('kiosk_id'),)).fetchone()
    if not k or not S.qr_valid(dict(k), data.get('code')):
        return jsonify(success=False, message='الكود ده انتهى — امسح الكود اللي على الشاشة دلوقتي'), 400
    ok, msg = S.record(conn, emp, 'qr', f'QR كشك «{k["name"]}»', user_id=session.get('user_id'))
    return jsonify(success=ok, message=msg), (200 if ok else 409)


# ------------------------------------------------------------ الإدارة

@smart_punch_bp.route('/attendance/smart')
@login_required
@require_permission(ADMIN_PERMISSION)
def admin():
    conn = get_db_connection()
    S.ensure_schema(conn)
    emps = conn.execute('''SELECT e.id, e.name, e.employee_number, e.department, p.updated_at AS pin_at, p.locked_until
                           FROM employees e LEFT JOIN kiosk_pins p ON p.employee_id = e.id
                           WHERE COALESCE(e.is_active, 1) = 1 ORDER BY e.name''').fetchall()
    branches = [r[0] for r in conn.execute("SELECT name FROM branches WHERE is_active = 1 ORDER BY name")] \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'branches'").fetchone() else []
    return render_template('smart_punch_admin.html', kiosks=S.kiosks(conn), emps=emps, flags=S.flags(conn),
                           branches=branches, reject_mocked=S.reject_mocked(),
                           new_pin=session.pop('kiosk_new_pin', None), tab=request.args.get('tab') or 'kiosks',
                           mileage_rate=get_setting('mileage_rate_per_km', '0.050'))


@smart_punch_bp.route('/attendance/smart/kiosk', methods=['POST'])
@login_required
@require_permission(ADMIN_PERMISSION)
def admin_kiosk():
    conn = get_db_connection()
    f = request.form
    if f.get('id'):
        conn.execute('UPDATE kiosks SET name = ?, branch_name = ?, allow_pin = ?, allow_qr = ?, require_selfie = ?, '
                     'is_active = ? WHERE id = ?',
                     (f.get('name') or 'كشك', f.get('branch_name') or None, int(f.get('allow_pin') == '1'),
                      int(f.get('allow_qr') == '1'), int(f.get('require_selfie') == '1'), int(f.get('is_active') == '1'),
                      f.get('id')))
        conn.commit()
        flash('اتحفظ الكشك', 'success')
    else:
        try:
            S.create_kiosk(conn, f.get('name'), f.get('branch_name'), f.get('allow_pin') == '1',
                           f.get('allow_qr') == '1', f.get('require_selfie') == '1')
            flash('اتعمل الكشك — افتح الرابط بتاعه على التابلت', 'success')
        except ValueError as e:
            flash(str(e), 'warning')
    return redirect(url_for('smart_punch.admin'))


@smart_punch_bp.route('/attendance/smart/pin', methods=['POST'])
@login_required
@require_permission(ADMIN_PERMISSION)
def admin_pin():
    conn = get_db_connection()
    try:
        emp = int(request.form.get('employee_id'))
        pin = S.set_pin(conn, emp, request.form.get('pin'))
        name = conn.execute('SELECT name FROM employees WHERE id = ?', (emp,)).fetchone()
        session['kiosk_new_pin'] = {'name': name[0] if name else '', 'pin': pin}
    except (TypeError, ValueError) as e:
        flash(str(e) or 'اختار موظف', 'warning')
    return redirect(url_for('smart_punch.admin', tab='pins'))


@smart_punch_bp.route('/attendance/smart/settings', methods=['POST'])
@login_required
@require_permission(ADMIN_PERMISSION)
def admin_settings():
    set_setting('gps_reject_mocked', '1' if request.form.get('reject_mocked') == '1' else '0')
    try:
        rate = float(request.form.get('mileage_rate') or 0)
        set_setting('mileage_rate_per_km', f'{max(rate, 0):.3f}')
    except ValueError:
        pass
    flash('اتحفظت الإعدادات', 'success')
    return redirect(url_for('smart_punch.admin', tab='flags'))


@smart_punch_bp.route('/attendance/smart/photo/<path:name>')
@login_required
@require_permission(ADMIN_PERMISSION)
def admin_photo(name):
    import os
    return send_from_directory(S.selfie_dir(), os.path.basename(name))
