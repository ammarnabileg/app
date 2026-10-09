# -*- coding: utf-8 -*-
"""الوكيل المحلّيّ: واجهة الوكيل (بمفتاح، بلا جلسة) وشاشة إدارته (utils/connector)."""
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from utils import connector as C
from utils.auth import login_required
from utils.db import get_db_connection
from utils.rbac import require_permission

connector_bp = Blueprint('connector', __name__)
ADMIN_PERMISSION = 'attendance.devices'

# تتخطّى فحص الترخيص بالتحويل (app.check_license_globally): الوكيلُ برنامجٌ لا يتبع
# تحويلةً لصفحة الترخيص — والمفتاحُ هو ما يحميها.
CONNECTOR_API_ENDPOINTS = frozenset(f'connector.{n}' for n in
                                    ('api_config', 'api_punches', 'api_users', 'api_commands', 'api_ack'))


def _auth():
    key = request.headers.get('X-Connector-Key') or ''
    if not key and request.headers.get('Authorization', '').startswith('Bearer '):
        key = request.headers['Authorization'][7:]
    from utils.net import client_ip
    conn = get_db_connection()
    return conn, C.authenticate(conn, key.strip(), ip=client_ip(request, conn),
                                version=request.headers.get('X-Connector-Version'))


def _deny():
    return jsonify(ok=False, message='مفتاح الوكيل غلط أو موقوف'), 401


@connector_bp.route('/api/connector/config')
def api_config():
    conn, c = _auth()
    if not c:
        return _deny()
    return jsonify(ok=True, **C.config(conn, c))


@connector_bp.route('/api/connector/punches', methods=['POST'])
def api_punches():
    conn, c = _auth()
    if not c:
        return _deny()
    data = request.get_json(silent=True) or {}
    punches = data.get('punches') if isinstance(data.get('punches'), list) else []
    if len(punches) > 5000:
        return jsonify(ok=False, message='دفعة كبيرة — ابعت 5000 بالكتير'), 413
    res = C.receive_punches(conn, c, data.get('device_id'), punches)
    return jsonify(res), (200 if res.get('ok') else 404)


@connector_bp.route('/api/connector/users', methods=['POST'])
def api_users():
    conn, c = _auth()
    if not c:
        return _deny()
    data = request.get_json(silent=True) or {}
    res = C.receive_users(conn, c, data.get('device_id'), data.get('users') or [])
    return jsonify(res), (200 if res.get('ok') else 404)


@connector_bp.route('/api/connector/commands')
def api_commands():
    conn, c = _auth()
    if not c:
        return _deny()
    return jsonify(ok=True, commands=C.fetch_commands(conn, c))


@connector_bp.route('/api/connector/ack', methods=['POST'])
def api_ack():
    conn, c = _auth()
    if not c:
        return _deny()
    data = request.get_json(silent=True) or {}
    if data.get('error'):
        conn.execute('UPDATE connectors SET last_error = ? WHERE id = ?', (str(data['error'])[:300], c['id']))
        conn.commit()
    return jsonify(ok=True, done=C.ack(conn, c, data.get('results') or []))


# ------------------------------------------------------------ الإدارة

@connector_bp.route('/fingerprint/connectors')
@login_required
@require_permission(ADMIN_PERMISSION)
def admin():
    conn = get_db_connection()
    return render_template('connectors.html', connectors=C.connectors(conn), commands=C.recent_commands(conn),
                           new_key=session.pop('connector_new_key', None), server=request.host_url.rstrip('/'))


@connector_bp.route('/fingerprint/connectors', methods=['POST'])
@login_required
@require_permission(ADMIN_PERMISSION)
def admin_save():
    conn = get_db_connection()
    f = request.form
    action = f.get('action')
    if action == 'create':
        cid, key = C.create(conn, f.get('name'))
        session['connector_new_key'] = {'id': cid, 'key': key}
        try:
            from utils.devices import registry
            if C.DRIVER_KEY not in registry.enabled_keys():
                registry.set_enabled([k for k in registry.enabled_keys() if k not in registry.LEGACY_KEYS] + [C.DRIVER_KEY])
        except Exception:
            pass
    elif action == 'rotate':
        key = C.rotate(conn, int(f.get('id')))
        session['connector_new_key'] = {'id': int(f.get('id')), 'key': key}
    elif action == 'toggle':
        conn.execute('UPDATE connectors SET is_active = 1 - is_active WHERE id = ?', (f.get('id'),))
        conn.commit()
        flash('اتحفظ', 'success')
    return redirect(url_for('connector.admin'))
