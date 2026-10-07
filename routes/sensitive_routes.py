# -*- coding: utf-8 -*-
"""صفحة العمليات الحساسة — للسوبر أدمن (صلاحية `admin.danger`).

مسحُ موظّفين (من النظام فقط، أو من النظام والأجهزة المختارة)، ومسحُ أجهزة،
وسلّةُ المحذوفات للاسترجاع، وسجلُّ مَن عمل ماذا. التفاصيل في utils/sensitive_ops.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from utils import sensitive_ops
from utils.auth import login_required
from utils.db import get_db_connection
from utils.rbac import require_permission

sensitive_bp = Blueprint('sensitive', __name__, url_prefix='/admin/sensitive')


def _ints(values):
    out = []
    for v in values:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


@sensitive_bp.route('/')
@login_required
@require_permission('admin.danger')
def index():
    conn = get_db_connection()
    sensitive_ops.ensure_schema(conn)
    employees = conn.execute('SELECT id, name, employee_number, department, is_active FROM employees '
                             'ORDER BY is_active DESC, name').fetchall()
    devices = conn.execute('SELECT * FROM fingerprint_devices ORDER BY device_name').fetchall()
    show_restored = request.args.get('restored') == '1'
    return render_template('sensitive_ops.html',
                           employees=employees, devices=devices,
                           bin_items=sensitive_ops.bin_items(conn, show_restored=show_restored),
                           show_restored=show_restored,
                           audit=sensitive_ops.audit_rows(conn),
                           tab=request.args.get('tab') or 'employees',
                           # من زرّ «حذف» في إدارة الموظفين: الموظّفُ مُعلَّمٌ جاهز.
                           preselect=set(_ints(request.args.getlist('emp'))))


@sensitive_bp.route('/employees/delete', methods=['POST'])
@login_required
@require_permission('admin.danger')
def delete_employees():
    ids = _ints(request.form.getlist('employee_ids'))
    mode = 'devices' if request.form.get('mode') == 'devices' else 'system'
    device_ids = _ints(request.form.getlist('device_ids')) if mode == 'devices' else []
    note = (request.form.get('note') or '').strip()[:300]
    if not ids:
        flash('اختار موظف واحد على الأقل', 'warning')
        return redirect(url_for('sensitive.index', tab='employees'))
    if mode == 'devices' and not device_ids:
        flash('اخترت «من النظام والأجهزة» ومفيش ولا جهاز متعلَّم — علّم الأجهزة أو اختار «من النظام فقط»', 'warning')
        return redirect(url_for('sensitive.index', tab='employees'))
    conn = get_db_connection()
    queue = []
    done = 0
    for emp_id in ids:
        if sensitive_ops.delete_employee(conn, emp_id, mode=mode, device_ids=device_ids, note=note,
                                         direct_queue=queue):
            done += 1
    sensitive_ops.flush_direct(queue)
    where = 'من النظام والأجهزة المختارة' if mode == 'devices' else 'من النظام فقط'
    flash(f'اتمسح {done} موظف {where} — موجودين في سلّة المحذوفات لو حبيت ترجّعهم', 'success')
    return redirect(url_for('sensitive.index', tab='bin'))


@sensitive_bp.route('/devices/delete', methods=['POST'])
@login_required
@require_permission('admin.danger')
def delete_devices():
    ids = _ints(request.form.getlist('device_ids'))
    note = (request.form.get('note') or '').strip()[:300]
    if not ids:
        flash('اختار جهاز واحد على الأقل', 'warning')
        return redirect(url_for('sensitive.index', tab='devices'))
    conn = get_db_connection()
    done = sum(1 for d in ids if sensitive_ops.delete_device(conn, d, note=note))
    flash(f'اتمسح {done} جهاز — موجودين في سلّة المحذوفات لو حبيت ترجّعهم', 'success')
    return redirect(url_for('sensitive.index', tab='bin'))


@sensitive_bp.route('/restore/<int:bin_id>', methods=['POST'])
@login_required
@require_permission('admin.danger')
def restore(bin_id):
    conn = get_db_connection()
    ok, msg = sensitive_ops.restore(conn, bin_id, reupload=request.form.get('reupload') == '1')
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('sensitive.index', tab='bin'))
