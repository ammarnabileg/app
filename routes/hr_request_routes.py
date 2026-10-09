# -*- coding: utf-8 -*-
"""طلبات الموظّفين ومسار موافقاتها — البوّابة وشاشة «الطلبات والموافقات».

المنطقُ كلُّه في utils/hr_requests؛ هنا الشاشاتُ والصلاحيّات فقط:
- الموظّف (من البوّابة): يقدّم ويتابع ويلغي طلبه، ويطبع خطابه المعتمد.
- من عليه الدور (مدير مباشر/مدير قسم/موارد بشرية): يعتمد أو يرفض — من البوّابة أو الشاشة.
- الموارد البشريّة (`hr.requests`): كلُّ الطلبات، وسلاسلُ الموافقة، وقوالبُ الخطابات.
"""
import os

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_from_directory, \
    session, url_for

from utils import hr_requests as H
from utils.auth import get_current_user, login_required
from utils.db import get_db_connection
from utils.rbac import RBACService, require_permission

hr_requests_bp = Blueprint('hr_requests', __name__)


def _user():
    u = get_current_user()
    return (u['id'], u['full_name'] or u['username']) if u else (None, None)


def _own_employee_id():
    """موظّفُ الجلسة نفسه — لا «استعراض المسؤول»: الطلبُ يُقدَّم باسم صاحبه فقط."""
    if session.get('employee_id'):
        return session['employee_id']
    uid = session.get('user_id')
    if not uid:
        return None
    r = get_db_connection().execute('SELECT employee_id FROM users WHERE id = ?', (uid,)).fetchone()
    return r['employee_id'] if r and r['employee_id'] else None


def _is_hr():
    uid = session.get('user_id')
    return bool(uid) and RBACService.has_permission(uid, H.PERMISSION)


def _public(req, user_id=None, conn=None):
    """ما يُرسَل للمتصفّح."""
    d = {k: req[k] for k in ('id', 'employee_id', 'employee_name', 'employee_number', 'department', 'type',
                             'type_label', 'status', 'status_label', 'created_at', 'decided_at', 'result_ref')}
    d['payload'] = {k: v for k, v in req['payload'].items() if k != 'attachment'}
    d['has_attachment'] = bool(req['payload'].get('attachment'))
    d['summary'] = H.summary(req)
    d['steps'] = [{'label': s['label'], 'status': s['status'], 'by': s['decided_by_name'], 'at': s['decided_at'],
                   'note': s['note']} for s in req['steps']]
    if conn is not None and user_id:
        d['can_act'] = H.can_act(conn, req, user_id) is not None
    return d


# ------------------------------------------------------------ البوّابة

@hr_requests_bp.route('/portal/api/requests/types')
@login_required
def api_types():
    conn = get_db_connection()
    types = [{'code': t['code'], 'label': t['label'],
              'chain': [H.approver_label(conn, a) for a in t['chain']]}
             for t in H.type_settings(conn).values() if t['enabled']]
    letters = [{'id': t['id'], 'name': t['name']} for t in H.letter_templates(conn)]
    return jsonify(success=True, types=types, letters=letters, can_request=bool(_own_employee_id()))


@hr_requests_bp.route('/portal/api/requests', methods=['POST'])
@login_required
def api_create():
    emp = _own_employee_id()
    if not emp:
        return jsonify(success=False, message='الحساب ده مش مربوط بموظف — الطلب لازم يتقدّم من حساب صاحبه'), 403
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    payload = dict(data.get('payload') or {})
    attachment = payload.pop('attachment', None)
    try:
        rid = H.create(conn, emp, data.get('type'), payload, created_by=session.get('user_id'))
        if attachment:
            name = H.save_attachment(rid, attachment)
            if name:
                req = H.get(conn, rid)
                req['payload']['attachment'] = name
                import json
                conn.execute('UPDATE hr_requests SET payload = ? WHERE id = ?',
                             (json.dumps(req['payload'], ensure_ascii=False), rid))
                conn.commit()
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    return jsonify(success=True, id=rid, message='اتبعت الطلب')


@hr_requests_bp.route('/portal/api/requests/mine')
@login_required
def api_mine():
    emp = _own_employee_id()
    if not emp:
        return jsonify(success=True, requests=[])
    conn = get_db_connection()
    return jsonify(success=True, requests=[_public(r) for r in H.list_for_employee(conn, emp)])


@hr_requests_bp.route('/portal/api/requests/<int:rid>/cancel', methods=['POST'])
@login_required
def api_cancel(rid):
    emp = _own_employee_id()
    ok = bool(emp) and H.cancel(get_db_connection(), rid, emp)
    return jsonify(success=ok, message='اتلغى الطلب' if ok else 'مينفعش تلغي الطلب ده')


@hr_requests_bp.route('/portal/api/requests/approvals')
@login_required
def api_approvals():
    uid, _ = _user()
    conn = get_db_connection()
    return jsonify(success=True, requests=[_public(r, uid, conn) for r in H.pending_for_user(conn, uid)])


@hr_requests_bp.route('/portal/api/requests/<int:rid>/decide', methods=['POST'])
@login_required
def api_decide(rid):
    uid, name = _user()
    data = request.get_json(silent=True) or request.form
    approve = str(data.get('action') or '').lower() in ('approve', 'approved', '1', 'true')
    ok, msg = H.decide(get_db_connection(), rid, uid, name, approve, data.get('note') or '')
    return jsonify(success=ok, message=msg), (200 if ok else 403)


def _may_view(conn, req):
    """صاحبُ الطلب، أو الموارد البشريّة، أو من عليه/كان عليه دورٌ فيه."""
    if not req:
        return False
    if req['employee_id'] == _own_employee_id() or _is_hr():
        return True
    uid = session.get('user_id')
    return any(s['decided_by'] == uid for s in req['steps']) or H.can_act(conn, req, uid) is not None


@hr_requests_bp.route('/requests/<int:rid>/letter')
@login_required
def letter(rid):
    conn = get_db_connection()
    req = H.get(conn, rid)
    if not _may_view(conn, req) or req['type'] != 'letter':
        abort(404)
    if req['status'] != 'approved':
        return render_template('hr_requests/letter.html', pending=True, req=req)
    return render_template('hr_requests/letter.html', pending=False, req=req, letter=H.render_letter(conn, req))


@hr_requests_bp.route('/requests/<int:rid>/attachment')
@login_required
def attachment(rid):
    conn = get_db_connection()
    req = H.get(conn, rid)
    if not _may_view(conn, req) or not req['payload'].get('attachment'):
        abort(404)
    return send_from_directory(H.attachments_dir(), os.path.basename(req['payload']['attachment']))


# ------------------------------------------------------------ الشاشة

@hr_requests_bp.route('/requests/')
@login_required
def index():
    """من عليه دورٌ يرى ما ينتظره؛ والموارد البشريّة ترى كلَّ شيء وتضبط."""
    conn = get_db_connection()
    uid, _ = _user()
    hr = _is_hr()
    status = request.args.get('status') or ('pending' if hr else None)
    rtype = request.args.get('type') or None
    waiting = [_public(r, uid, conn) for r in H.pending_for_user(conn, uid)]
    every = [_public(r, uid, conn) for r in H.list_all(conn, status=status, rtype=rtype)] if hr else []
    users = conn.execute('SELECT id, full_name, username FROM users WHERE COALESCE(is_active, 1) = 1 '
                         'ORDER BY full_name').fetchall() if hr else []
    return render_template('hr_requests/index.html', waiting=waiting, every=every, hr=hr,
                           status=status or '', rtype=rtype or '', types=H.TYPES, statuses=H.STATUS_LABELS,
                           settings=H.type_settings(conn) if hr else {}, step_labels=H.STEP_LABELS,
                           letters=H.letter_templates(conn, active_only=False) if hr else [], users=users,
                           tab=request.args.get('tab') or ('waiting' if waiting or not hr else 'all'))


@hr_requests_bp.route('/requests/<int:rid>/decide', methods=['POST'])
@login_required
def decide(rid):
    uid, name = _user()
    ok, msg = H.decide(get_db_connection(), rid, uid, name, request.form.get('action') == 'approve',
                       request.form.get('note') or '')
    flash(msg, 'success' if ok else 'danger')
    return redirect(request.referrer or url_for('hr_requests.index'))


@hr_requests_bp.route('/requests/settings', methods=['POST'])
@login_required
@require_permission(H.PERMISSION)
def save_settings():
    conn = get_db_connection()
    for code in H.TYPES:
        chain = [c for c in request.form.getlist(f'chain_{code}') if c]
        H.save_type(conn, code, request.form.get(f'enabled_{code}') == '1', chain)
    flash('اتحفظت مسارات الموافقة', 'success')
    return redirect(url_for('hr_requests.index', tab='settings'))


@hr_requests_bp.route('/requests/letters', methods=['POST'])
@login_required
@require_permission(H.PERMISSION)
def save_letter():
    conn = get_db_connection()
    tid = request.form.get('id')
    name = (request.form.get('name') or '').strip()
    body = request.form.get('body') or ''
    active = 1 if request.form.get('is_active') == '1' else 0
    if not name or not body.strip():
        flash('اكتب اسم الخطاب ونصّه', 'warning')
    elif tid:
        conn.execute('UPDATE letter_templates SET name = ?, body = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP '
                     'WHERE id = ?', (name, body, active, tid))
        conn.commit()
        flash('اتحفظ الخطاب', 'success')
    else:
        conn.execute('INSERT INTO letter_templates (name, body, is_active) VALUES (?, ?, ?)', (name, body, active))
        conn.commit()
        flash('اتضاف الخطاب', 'success')
    return redirect(url_for('hr_requests.index', tab='letters'))
