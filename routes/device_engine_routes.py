# -*- coding: utf-8 -*-
"""محرّك الأجهزة الموحّد — مسارات الماركات الجديدة (utils/devices).

- `/devices/push/<driver>/<token>`: الجهاز (Hikvision) يرسل الحضور لحظيًّا. بلا دخولٍ ولا
  فحص ترخيص — كـ /iclock تمامًا (الجهازُ لا يتبع تحويلة). والرمزُ في العنوان يخصّ
  جهازًا واحدًا مسجّلًا ونشطًا؛ وغيرُه يُردّ عليه OK بلا أثر (لا نعلّم المخمِّن شيئًا).
- إعداد «الماركات المدعومة» لهذه الشركة، وعنوانُ الاستقبال لكلّ جهاز، ومزامنةُ جهازٍ الآن.
"""
from flask import Blueprint, flash, jsonify, redirect, request, url_for

from utils.auth import login_required
from utils.db import get_db_connection
from utils.rbac import require_permission

device_engine_bp = Blueprint('device_engine', __name__)

# للفحوص العامّة في app.py — الجهاز لا يحمل جلسة ولا يتبع تحويلة.
DEVICE_PUSH_ENDPOINTS = frozenset({'device_engine.device_push'})


@device_engine_bp.route('/devices/push/<driver>/<token>', methods=['GET', 'POST'])
def device_push(driver, token):
    from utils.devices import engine
    try:
        engine.receive_push(get_db_connection(), driver, token, request)
    except Exception as e:                      # الجهازُ يعيد الإرسال إن لم يأخذ ردًّا — فلا 500
        import logging
        logging.getLogger(__name__).warning(f'device push {driver}: {e}')
    return 'OK'


@device_engine_bp.route('/fingerprint/drivers', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def set_drivers():
    """«الماركات المدعومة» — الجديدة تجريبيّة ومُطفأة حتى تُفعَّل هنا."""
    from utils.devices import registry
    registry.set_enabled(request.form.getlist('drivers'))
    flash('اتحفظت الماركات المدعومة', 'success')
    return redirect(url_for('attendance.devices'))


@device_engine_bp.route('/fingerprint/devices/<int:device_id>/push_url')
@login_required
@require_permission('attendance.devices')
def push_url(device_id):
    """العنوان الذي يُضبط في الجهاز ليرسل الحضور لحظيًّا (Hikvision: httpHosts)."""
    from utils.devices import engine, registry, base
    conn = get_db_connection()
    d = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    if d is None or registry.is_legacy(d):
        return jsonify({'success': False, 'message': 'الجهاز ده ZKTeco — بيستخدم ADMS أو الاتصال المباشر'})
    if base.PUSH_EVENTS not in registry.capabilities(d):
        return jsonify({'success': False, 'message': 'الماركة دي مبتبعتش الحضور لحظيًا — المزامنة بتسحبه'})
    token = engine.ensure_push_token(conn, device_id)
    url = _reachable_host() + url_for('device_engine.device_push', driver=registry.driver_key(d), token=token)
    return jsonify({'success': True, 'url': url,
                    'hint': 'في إعدادات الجهاز: Network → Advanced → HTTP Listening (أو Event → HTTP Host) '
                            'حط العنوان ده، والبروتوكول HTTP/HTTPS زي ما هو في العنوان'})


def _reachable_host():
    """عنوانٌ يصل إليه الجهاز: من يفتح البرنامج على نفس الكمبيوتر يراه 127.0.0.1 — والجهازُ
    لا يصل إليه. فيُستبدل بعنوان الكمبيوتر على الشبكة المحلّيّة."""
    base = request.host_url.rstrip('/')
    host = request.host.split(':')[0]
    if host in ('127.0.0.1', 'localhost', '::1'):
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('10.255.255.255', 1))           # لا يُرسل شيئًا — يختار الواجهة فقط
            lan = s.getsockname()[0]
            s.close()
            port = request.host.split(':')[1] if ':' in request.host else ''
            base = f"{request.scheme}://{lan}" + (f':{port}' if port else '')
        except OSError:
            pass
    return base


@device_engine_bp.route('/fingerprint/devices/<int:device_id>/driver_sync', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def driver_sync(device_id):
    """«مزامنة الآن» لجهازٍ واحد من الماركات الجديدة: الحضور ثمّ الموظّفون."""
    from utils.devices import engine, registry
    conn = get_db_connection()
    d = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    if d is None or registry.is_legacy(d):
        return jsonify({'success': False, 'message': 'استخدم مزامنة ZKTeco العادية للجهاز ده'})
    try:
        a = engine.sync_attendance(conn, d)
        u = engine.sync_users(conn, conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?',
                                                 (device_id,)).fetchone())
        msg = []
        if 'skipped' not in a:
            msg.append(f"الحضور: اتسحب {a['pulled']}، جديد {a['saved']}"
                       + (f"، مستني موظف يتضاف {a['pending']}" if a.get('pending') else '')
                       + (f"، موقوفين اتجاهلوا {a['ignored']}" if a.get('ignored') else ''))
        if 'skipped' not in u:
            msg.append(f"الموظفين: {u['users']} على الجهاز، اتضاف {u['employees_added']}")
        return jsonify({'success': True, 'message': ' — '.join(msg) or 'تم'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)[:200]})
