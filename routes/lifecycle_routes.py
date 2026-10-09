# -*- coding: utf-8 -*-
"""شاشة «متابعة الموظفين»: وثائق قربت تنتهي، وقوائم الاستلام والتسليم (utils/employee_lifecycle)."""
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from utils import employee_lifecycle as L
from utils.auth import get_current_user, login_required
from utils.db import get_db_connection
from utils.rbac import require_permission

lifecycle_bp = Blueprint('lifecycle', __name__, url_prefix='/hr/lifecycle')


@lifecycle_bp.route('/')
@login_required
@require_permission(L.PERMISSION)
def index():
    conn = get_db_connection()
    L.ensure_schema(conn)
    within = request.args.get('within', type=int) or 60
    employees = conn.execute('SELECT id, name, employee_number FROM employees WHERE COALESCE(is_active, 1) = 1 '
                             'ORDER BY name').fetchall()
    return render_template('lifecycle.html', expiring=L.expiring(conn, within=within), within=within,
                           lists=L.checklists(conn, open_only=request.args.get('done') != '1'),
                           show_done=request.args.get('done') == '1', suggestions=L.suggestions(conn),
                           templates={k: L.template(conn, k) for k in L.KINDS}, kinds=L.KINDS, owners=L.OWNERS,
                           employees=employees, tab=request.args.get('tab') or 'expiry')


@lifecycle_bp.route('/start', methods=['POST'])
@login_required
@require_permission(L.PERMISSION)
def start():
    conn = get_db_connection()
    u = get_current_user()
    try:
        L.start(conn, int(request.form.get('employee_id') or 0), request.form.get('kind'), u['id'] if u else None)
        flash('اتعملت القائمة', 'success')
    except (ValueError, TypeError) as e:
        flash(str(e) or 'اختار الموظف', 'warning')
    return redirect(url_for('lifecycle.index', tab='lists'))


@lifecycle_bp.route('/item/<int:item_id>', methods=['POST'])
@login_required
@require_permission(L.PERMISSION)
def item(item_id):
    u = get_current_user()
    data = request.get_json(silent=True) or {}
    cid = L.toggle(get_db_connection(), item_id, bool(data.get('done')),
                   (u['full_name'] or u['username']) if u else '', data.get('note'))
    return jsonify(success=cid is not None)


@lifecycle_bp.route('/template/<kind>', methods=['POST'])
@login_required
@require_permission(L.PERMISSION)
def save_template(kind):
    items = [{'owner': o, 'title': t} for o, t in zip(request.form.getlist('owner'), request.form.getlist('title'))]
    try:
        L.save_template(get_db_connection(), kind, items)
        flash('اتحفظ القالب', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    return redirect(url_for('lifecycle.index', tab='templates'))
