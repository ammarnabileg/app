# -*- coding: utf-8 -*-
"""الإعلانات والاستبيانات — شاشة الإدارة وواجهة البوّابة (utils/engagement)."""
import json

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from utils import engagement as G
from utils.auth import get_current_user, login_required
from utils.db import get_db_connection
from utils.rbac import require_permission

engagement_bp = Blueprint('engagement', __name__)


def _emp():
    from routes.hr_request_routes import _own_employee_id
    return _own_employee_id()


# ------------------------------------------------------------ البوّابة

@engagement_bp.route('/portal/api/engagement')
@login_required
def api_feed():
    emp = _emp()
    if not emp:
        return jsonify(success=True, announcements=[], surveys=[])
    conn = get_db_connection()
    anns = [{k: a[k] for k in ('id', 'title', 'body', 'pinned', 'created_at', 'read_at')}
            for a in G.announcements_for(conn, emp)]
    return jsonify(success=True, announcements=anns, surveys=G.surveys_for(conn, emp))


@engagement_bp.route('/portal/api/announcements/<int:aid>/read', methods=['POST'])
@login_required
def api_read(aid):
    emp = _emp()
    if emp:
        G.mark_read(get_db_connection(), aid, emp)
    return jsonify(success=bool(emp))


@engagement_bp.route('/portal/api/surveys/<int:sid>', methods=['POST'])
@login_required
def api_answer(sid):
    emp = _emp()
    if not emp:
        return jsonify(success=False, message='الحساب ده مش مربوط بموظف'), 403
    try:
        G.answer(get_db_connection(), sid, emp, (request.get_json(silent=True) or {}).get('answers'))
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    return jsonify(success=True, message='شكرًا — اتسجّل ردّك')


# ------------------------------------------------------------ الإدارة

@engagement_bp.route('/hr/engagement/')
@login_required
@require_permission(G.PERMISSION)
def index():
    conn = get_db_connection()
    G.ensure_schema(conn)
    depts = [r[0] for r in conn.execute("SELECT DISTINCT department FROM employees WHERE department IS NOT NULL "
                                        "AND department != '' ORDER BY 1")]
    branches = [r[0] for r in conn.execute("SELECT DISTINCT branch_location FROM employees WHERE branch_location "
                                           "IS NOT NULL AND branch_location != '' ORDER BY 1")]
    return render_template('engagement.html', anns=G.announcements_admin(conn), surveys=G.surveys_admin(conn),
                           depts=depts, branches=branches, qtypes=G.QTYPES, tab=request.args.get('tab') or 'ann')


def _audience(form):
    t = form.get('audience_type') or 'all'
    v = form.get('audience_dept') if t == 'department' else (form.get('audience_branch') if t == 'branch' else None)
    return t, v


@engagement_bp.route('/hr/engagement/announcements', methods=['POST'])
@login_required
@require_permission(G.PERMISSION)
def save_announcement():
    conn = get_db_connection()
    f = request.form
    if f.get('id'):
        conn.execute('UPDATE announcements SET is_active = ? WHERE id = ?', (int(f.get('is_active') == '1'), f['id']))
        conn.commit()
        flash('اتحفظ', 'success')
    else:
        t, v = _audience(f)
        u = get_current_user()
        try:
            G.create_announcement(conn, f.get('title'), f.get('body'), t, v, f.get('pinned') == '1',
                                  f.get('expires_on') or None, u['id'] if u else None)
            flash('اتنشر الإعلان ووصل إشعار للموظفين', 'success')
        except ValueError as e:
            flash(str(e), 'warning')
    return redirect(url_for('engagement.index', tab='ann'))


@engagement_bp.route('/hr/engagement/surveys', methods=['POST'])
@login_required
@require_permission(G.PERMISSION)
def save_survey():
    conn = get_db_connection()
    f = request.form
    if f.get('id'):
        conn.execute('UPDATE surveys SET is_open = ? WHERE id = ?', (int(f.get('is_open') == '1'), f['id']))
        conn.commit()
        flash('اتحفظ', 'success')
        return redirect(url_for('engagement.index', tab='surveys'))
    try:
        questions = json.loads(f.get('questions') or '[]')
    except ValueError:
        questions = []
    t, v = _audience(f)
    u = get_current_user()
    try:
        G.create_survey(conn, f.get('title'), questions, f.get('description'), f.get('anonymous') == '1', t, v,
                        f.get('closes_on') or None, u['id'] if u else None)
        flash('اتنشر الاستبيان', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    return redirect(url_for('engagement.index', tab='surveys'))
