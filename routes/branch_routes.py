from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_babel import gettext
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission

branch_bp = Blueprint('branch', __name__)

@branch_bp.route('/branches', methods=['GET'])
@login_required
@require_permission('page.branches')
def branches_list():
    conn = get_db_connection()
    try:
        raw_branches = conn.execute('''
            SELECT * FROM branches ORDER BY is_active DESC, id ASC
        ''').fetchall()

        branches = []
        for b in raw_branches:
            b_dict = dict(b)
            # Count employees linked to this branch
            b_name = b['name'] or ''
            emp_count = conn.execute('''
                SELECT COUNT(*) FROM employees 
                WHERE branch_location = ? OR branch_location LIKE ?
            ''', (b_name, f'%{b_name}%')).fetchone()[0]
            
            # Count fingerprint devices linked to this branch
            dev_count = conn.execute('''
                SELECT COUNT(*) FROM fingerprint_devices 
                WHERE branch_id = ? OR branch_name = ?
            ''', (b['id'], b_name)).fetchone()[0]

            b_dict['employees_count'] = emp_count
            b_dict['devices_count'] = dev_count
            b_dict['has_gps'] = bool(b['latitude'] is not None and b['longitude'] is not None)
            b_dict['has_ip'] = bool(b['allowed_ips'] and b['allowed_ips'].strip())
            branches.append(b_dict)

        stats = {
            'total': len(branches),
            'active': sum(1 for b in branches if b.get('is_active')),
            'with_gps': sum(1 for b in branches if b.get('has_gps')),
            'total_devices': sum(b.get('devices_count', 0) for b in branches)
        }

        return render_template('branches.html', branches=branches, stats=stats)
    except Exception as e:
        flash(f"حدث خطأ أثناء تحميل الفروع: {e}", "error")
        return render_template('branches.html', branches=[], stats={'total': 0, 'active': 0, 'with_gps': 0, 'total_devices': 0})

@branch_bp.route('/branches/save', methods=['POST'])
@login_required
@require_permission('admin.settings')
def save_branch():
    conn = get_db_connection()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json
    data = request.get_json(silent=True) or request.form

    branch_id = data.get('id')
    name = (data.get('name') or '').strip()
    location = (data.get('location') or '').strip()
    address_details = (data.get('address_details') or '').strip()
    wifi_name = (data.get('wifi_name') or '').strip()
    allowed_ips = (data.get('allowed_ips') or '').strip()

    lat_raw = data.get('latitude')
    lon_raw = data.get('longitude')
    radius_raw = data.get('geofence_radius')

    if not name:
        msg = "اسم الفرع مطلوب ولا يمكن تركه فارغاً"
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, "error")
        return redirect(url_for('branch.branches_list'))

    # Parse numeric coordinates
    latitude = None
    longitude = None
    geofence_radius = 150

    try:
        if lat_raw not in (None, ''):
            latitude = float(lat_raw)
    except (ValueError, TypeError):
        latitude = None

    try:
        if lon_raw not in (None, ''):
            longitude = float(lon_raw)
    except (ValueError, TypeError):
        longitude = None

    try:
        if radius_raw not in (None, ''):
            geofence_radius = max(20, min(5000, int(radius_raw)))
    except (ValueError, TypeError):
        geofence_radius = 150

    try:
        if branch_id:
            # Update existing branch
            existing = conn.execute('SELECT id FROM branches WHERE id = ?', (branch_id,)).fetchone()
            if not existing:
                msg = "الفرع المطلوب تعديله غير موجود"
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 404
                flash(msg, "error")
                return redirect(url_for('branch.branches_list'))

            # Check duplicate name with other branch
            dup = conn.execute('SELECT id FROM branches WHERE name = ? AND id != ?', (name, branch_id)).fetchone()
            if dup:
                msg = f"يوجد فرع آخر مسجل مسبقاً بنفس الاسم ({name})"
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 400
                flash(msg, "error")
                return redirect(url_for('branch.branches_list'))

            conn.execute('''
                UPDATE branches 
                SET name = ?, location = ?, latitude = ?, longitude = ?,
                    geofence_radius = ?, allowed_ips = ?, wifi_name = ?, address_details = ?
                WHERE id = ?
            ''', (name, location, latitude, longitude, geofence_radius, allowed_ips, wifi_name, address_details, branch_id))
            conn.commit()

            msg = f"تم تحديث بيانات الفرع ({name}) بنجاح"
            if is_ajax:
                return jsonify({'success': True, 'message': msg, 'branch_id': branch_id})
            flash(msg, "success")
            return redirect(url_for('branch.branches_list'))

        else:
            # Insert new branch
            dup = conn.execute('SELECT id FROM branches WHERE name = ?', (name,)).fetchone()
            if dup:
                msg = f"يوجد فرع مسجل مسبقاً بهذا الاسم ({name})"
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 400
                flash(msg, "error")
                return redirect(url_for('branch.branches_list'))

            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO branches (name, location, latitude, longitude, geofence_radius, allowed_ips, wifi_name, address_details, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (name, location, latitude, longitude, geofence_radius, allowed_ips, wifi_name, address_details))
            new_id = cursor.lastrowid
            conn.commit()

            msg = f"تمت إضافة الفرع الجديد ({name}) بنجاح"
            if is_ajax:
                return jsonify({'success': True, 'message': msg, 'branch_id': new_id})
            flash(msg, "success")
            return redirect(url_for('branch.branches_list'))

    except Exception as e:
        conn.rollback()
        msg = f"حدث خطأ أثناء حفظ الفرع: {e}"
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 500
        flash(msg, "error")
        return redirect(url_for('branch.branches_list'))

@branch_bp.route('/branches/toggle/<int:id>', methods=['POST'])
@login_required
@require_permission('admin.settings')
def toggle_branch(id):
    conn = get_db_connection()
    branch = conn.execute('SELECT id, name, is_active FROM branches WHERE id = ?', (id,)).fetchone()
    if not branch:
        return jsonify({'success': False, 'message': 'الفرع غير موجود'}), 404

    new_status = 0 if branch['is_active'] else 1
    conn.execute('UPDATE branches SET is_active = ? WHERE id = ?', (new_status, id))
    conn.commit()

    action_text = "تفعيل" if new_status else "تعطيل"
    return jsonify({
        'success': True,
        'is_active': new_status,
        'message': f"تم {action_text} الفرع ({branch['name']}) بنجاح"
    })

@branch_bp.route('/branches/delete/<int:id>', methods=['POST'])
@login_required
@require_permission('admin.settings')
def delete_branch(id):
    conn = get_db_connection()
    branch = conn.execute('SELECT id, name FROM branches WHERE id = ?', (id,)).fetchone()
    if not branch:
        flash("الفرع غير موجود", "error")
        return redirect(url_for('branch.branches_list'))

    b_name = branch['name']
    emp_count = conn.execute('SELECT COUNT(*) FROM employees WHERE branch_location = ?', (b_name,)).fetchone()[0]
    dev_count = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE branch_id = ? OR branch_name = ?', (id, b_name)).fetchone()[0]

    if emp_count > 0 or dev_count > 0:
        # Prevent hard delete if associated with data to protect integrity
        flash(f"لا يمكن حذف الفرع ({b_name}) لوجود {emp_count} موظف و {dev_count} جهاز بصمة مرتبطين به. يمكنك تعطيل الفرع بدلاً من ذلك.", "warning")
        return redirect(url_for('branch.branches_list'))

    try:
        conn.execute('DELETE FROM branches WHERE id = ?', (id,))
        conn.commit()
        flash(f"تم حذف الفرع ({b_name}) بنجاح", "success")
    except Exception as e:
        conn.rollback()
        flash(f"حدث خطأ أثناء حذف الفرع: {e}", "error")

    return redirect(url_for('branch.branches_list'))

@branch_bp.route('/api/branches', methods=['GET'])
@login_required
def get_branches_api():
    conn = get_db_connection()
    try:
        rows = conn.execute('''
            SELECT id, name, location, latitude, longitude, geofence_radius, allowed_ips, wifi_name, address_details, is_active
            FROM branches 
            WHERE is_active = 1
            ORDER BY id ASC
        ''').fetchall()
        return jsonify({
            'success': True,
            'branches': [dict(r) for r in rows]
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
