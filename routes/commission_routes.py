# -*- coding: utf-8 -*-
"""العمولات (utils/commissions): شاشة استيراد ملف، ومفتاح API للوحة onz.one، والـ API نفسه."""
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from utils import commissions as CM
from utils.auth import login_required
from utils.db import get_db_connection
from utils.rbac import require_permission

commission_bp = Blueprint('commission', __name__)
PERMISSION = 'salary.calculate'
# بمفتاح بلا جلسة، ومابيتبعش تحويلة الترخيص (زي الوكيل المحلي).
COMMISSION_API_ENDPOINTS = frozenset({'commission.api_import'})


@commission_bp.route('/payroll/commissions', methods=['GET', 'POST'])
@login_required
@require_permission(PERMISSION)
def page():
    conn = get_db_connection()
    report, new_key = None, None
    if request.method == 'POST':
        act = request.form.get('action')
        if act == 'new_key':
            new_key = CM.new_api_key()
        elif act == 'revoke_key':
            CM.revoke_api_key()
            flash('المفتاح اتلغى', 'success')
            return redirect(url_for('commission.page'))
        else:
            f = request.files.get('file')
            if not f or not f.filename:
                flash('اختار ملف', 'error')
                return redirect(url_for('commission.page'))
            try:
                items = CM.parse_file(f.read(5_000_000), f.filename)
                report = CM.import_items(conn, items, session.get('user_id'),
                                         month=request.form.get('month') or None,
                                         year=request.form.get('year') or None, source='file')
            except ValueError as e:
                flash(str(e), 'error')
                return redirect(url_for('commission.page'))
    return render_template('payroll_commissions.html', report=report, new_key=new_key,
                           has_key=CM.has_api_key(), recent=CM.recent(conn))


@commission_bp.route('/api/payroll/commissions', methods=['POST'])
def api_import():
    """{"items": [{"employee_number", "amount", "month"?, "year"?, "reference"?, "reason"?}]}"""
    key = request.headers.get('X-Commission-Key') or ''
    if not key and request.headers.get('Authorization', '').startswith('Bearer '):
        key = request.headers['Authorization'][7:]
    if not CM.check_api_key(key.strip()):
        return jsonify(ok=False, message='مفتاح العمولات غلط أو مش متفعّل'), 401
    body = request.get_json(silent=True) or {}
    items = body.get('items')
    if not isinstance(items, list) or not items or len(items) > 5000:
        return jsonify(ok=False, message='items لازم تكون قايمة (لحد 5000)'), 400
    rep = CM.import_items(get_db_connection(), [(i + 1, x if isinstance(x, dict) else {}) for i, x in enumerate(items)],
                          source='api')
    return jsonify(ok=True, **rep)
