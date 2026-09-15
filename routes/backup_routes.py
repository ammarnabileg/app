# -*- coding: utf-8 -*-
"""شاشة النسخ والاستعادة داخل نظام الموارد البشرية.

للمسؤول وحده. الاستعادة تكتب على القاعدة كلّها، وشاشة يصلها موظف عادي
هي زرّ محو بصلاحيات خاطئة.
"""
import os
import tempfile
from functools import wraps

from flask import (Blueprint, Response, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.utils import secure_filename

from utils.auth import get_current_user, login_required
from utils import backup as bk

backup_bp = Blueprint('backup', __name__, url_prefix='/backup')

# مكان الملف المرفوع أثناء الفحص. خارج مجلّد البيانات عمدًا: لا يُخلَط
# ملفٌ مرفوع من الخارج بملفات النظام العاملة.
_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), 'hr_restore')


def admin_required(f):
    """المسؤول وحده. login_required لا تكفي هنا."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user or user['role'] != 'admin':
            flash('هذه الصفحة لمسؤول النظام وحده.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return wrapper


@backup_bp.route('/')
@login_required
@admin_required
def index():
    att_size, att_count = bk.attachments_size()
    return render_template(
        'backup.html',
        tables=bk.list_tables(),
        db_size=bk.database_size(),
        db_size_h=bk.human_size(bk.database_size()),
        att_size=att_size,
        att_size_h=bk.human_size(att_size),
        att_count=att_count,
        inspected=session.get('backup_inspected'),
        human_size=bk.human_size,
        auto=bk.auto_settings(),
        auto_list=bk.list_auto_backups(),
    )


# ------------------------------------------------------------- التنزيل

@backup_bp.route('/download/db')
@login_required
@admin_required
def download_db():
    """نسخة كاملة بلقطة متّسقة — هذه ما يُحتفظ به.

    تُكتَب اللقطة في ملف مؤقّت ثم تُبثّ ثم تُحذف: واجهة النسخ في SQLite
    تكتب إلى ملف، ولا سبيل لبثّها مباشرةً.
    """
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    tmp = os.path.join(_UPLOAD_DIR, f'snap-{os.getpid()}-{os.urandom(6).hex()}.db')

    try:
        bk.snapshot_file(tmp)
        with open(tmp, 'rb') as fh:
            data = fh.read()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    return Response(
        data,
        mimetype='application/x-sqlite3',
        headers={
            'Content-Disposition': f'attachment; filename="{bk.default_name("db")}"',
            'Content-Length': str(len(data)),
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
        })


@backup_bp.route('/download/archive')
@login_required
@admin_required
def download_archive():
    """القاعدة ومعها الملفات التي تشير إليها.

    صور زيارات المناديب تعيش على القرص لا في القاعدة. فنسخة القاعدة
    وحدها تحفظ صفّ الزيارة — زمنها ومكانها وبصمة صورتها — وتترك
    الصورة. ومن يفتحها بعد سنة يجد سجلًّا يشير إلى دليلٍ غير موجود.
    """
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    tmp = os.path.join(_UPLOAD_DIR, f'arc-{os.getpid()}-{os.urandom(6).hex()}.zip')

    att_bytes, _att_count = bk.attachments_size()
    fits, free = bk.archive_fits(bk.database_size() + att_bytes)
    if not fits:
        flash(f'المساحة لا تكفي للأرشيف (المتاح {bk.human_size(free)}). '
              'نزّل القاعدة وحدها، أو فرّغ مساحة.', 'error')
        return redirect(url_for('backup.index'))

    try:
        bk.archive_zip(tmp)
        with open(tmp, 'rb') as fh:
            data = fh.read()
    except Exception as e:                               # noqa: BLE001
        flash(f'تعذّر بناء الأرشيف: {e}', 'error')
        return redirect(url_for('backup.index'))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    return Response(
        data,
        mimetype='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{bk.default_name("zip")}"',
            'Content-Length': str(len(data)),
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
        })


@backup_bp.route('/download/sql', methods=['POST'])
@login_required
@admin_required
def download_sql():
    """نسخة نصّية، كل الجداول أو ما اختير منها."""
    tables = request.form.getlist('tables') or None
    with_data = request.form.get('mode', 'full') != 'schema'

    def stream():
        try:
            for chunk in bk.dump_sql(tables, with_data):
                yield chunk
        except Exception as e:                       # noqa: BLE001
            # الترويسات أُرسلت، فلا صفحة خطأ. يُكتب في ذيل الملف ليعرف
            # من يفتحه أنه ناقص بدل أن يظنّه تامًّا.
            yield f'\n-- !! انقطع التصدير: {str(e)[:200]}\n'

    name = bk.default_name('sql')
    return Response(stream(), mimetype='application/sql', headers={
        'Content-Disposition': f'attachment; filename="{name}"',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    })


# ------------------------------------------------------------ الاستعادة

@backup_bp.route('/inspect', methods=['POST'])
@login_required
@admin_required
def inspect():
    f = request.files.get('backupfile')
    if not f or not f.filename:
        flash('اختر ملفًا أولًا.', 'error')
        return redirect(url_for('backup.index'))

    os.makedirs(_UPLOAD_DIR, exist_ok=True)

    # اسم مولَّد لا اسم من الطلب: اسم ملف يصل من المتصفّح هو الطريق
    # المعتاد للكتابة خارج المجلّد المقصود.
    path = os.path.join(_UPLOAD_DIR, os.urandom(16).hex() + '.upload')
    f.save(path)

    try:
        info = bk.inspect(path)
        info['path'] = path
        info['name'] = secure_filename(f.filename) or 'backup'
        session['backup_inspected'] = info
        flash('قُرئ الملف. اختر ما تريد استعادته.', 'success')
    except Exception as e:                            # noqa: BLE001
        if os.path.exists(path):
            os.remove(path)
        flash(f'تعذّرت قراءة الملف: {e}', 'error')

    return redirect(url_for('backup.index'))


@backup_bp.route('/restore', methods=['POST'])
@login_required
@admin_required
def restore():
    info = session.get('backup_inspected')
    if not info or not os.path.isfile(info.get('path', '')):
        flash('لا ملف مفحوص. ارفع الملف وافحصه أولًا.', 'error')
        return redirect(url_for('backup.index'))

    # تأكيد مكتوب لا مربّع اختيار: ضغطة واحدة لا تكفي لفعلٍ يمحو.
    if (request.form.get('confirm') or '').strip() != 'استعادة':
        flash('اكتب كلمة «استعادة» في خانة التأكيد لتنفيذ العملية.', 'error')
        return redirect(url_for('backup.index'))

    tables = request.form.getlist('tables')
    if not tables:
        flash('اختر جدولًا واحدًا على الأقل.', 'error')
        return redirect(url_for('backup.index'))

    keep_license = request.form.get('keep_license') == '1'

    try:
        res = bk.restore(info['path'], tables, keep_license=keep_license)
        if res['ok']:
            flash(f"تمّت الاستعادة: {res['ran']} عملية. "
                  f"نسخة ما قبلها محفوظة باسم {res['safety_copy']}.", 'success')
        else:
            flash(f"تمّت الاستعادة مع {len(res['errors'])} خطأ. "
                  f"أوّلها: {res['errors'][0]}", 'error')
    except Exception as e:                            # noqa: BLE001
        flash(f'فشلت الاستعادة: {e}', 'error')
    finally:
        if os.path.exists(info['path']):
            os.remove(info['path'])
        session.pop('backup_inspected', None)

    return redirect(url_for('backup.index'))


@backup_bp.route('/discard', methods=['POST'])
@login_required
@admin_required
def discard():
    info = session.pop('backup_inspected', None)
    if info and os.path.exists(info.get('path', '')):
        os.remove(info['path'])
    flash('حُذف الملف المرفوع.', 'success')
    return redirect(url_for('backup.index'))


# -------------------------------------------------------- النسخ التلقائي

@backup_bp.route('/auto/save', methods=['POST'])
@login_required
@admin_required
def auto_save():
    try:
        bk.save_auto_settings(
            enabled=request.form.get('enabled') == '1',
            keep=int(request.form.get('keep') or 7),
            hours=int(request.form.get('hours') or 24),
        )
        flash('حُفظت إعدادات النسخ التلقائي.', 'success')
    except (TypeError, ValueError):
        flash('قيم غير صحيحة.', 'error')
    return redirect(url_for('backup.index'))


@backup_bp.route('/auto/run', methods=['POST'])
@login_required
@admin_required
def auto_run():
    """نسخة الآن، بلا انتظار الموعد — للتأكّد أن الآلية تعمل أصلًا."""
    ok, msg = bk.run_auto_backup(force=True)
    flash(f'أُخذت نسخة: {msg}' if ok else f'تعذّرت النسخة: {msg}',
          'success' if ok else 'error')
    return redirect(url_for('backup.index'))


def _auto_path(name):
    """مسار نسخة تلقائية بعد التأكّد أن الاسم اسمٌ لا مسار.

    اسم ملف يصل من الطلب هو الطريق المعتاد لقراءة ملفٍ خارج المجلّد
    المقصود، بنقطتين وشرطة مائلة متكرّرتين.
    """
    safe = secure_filename(name or '')
    if not safe or not safe.endswith('.db') or not safe.startswith('auto-'):
        return None
    path = os.path.join(bk.auto_dir(), safe)
    # التحقّق بالمسار المطلق أيضًا: secure_filename وحدها عقدٌ ضمنيّ.
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(bk.auto_dir()):
        return None
    return path if os.path.isfile(path) else None


@backup_bp.route('/auto/get/<name>')
@login_required
@admin_required
def auto_get(name):
    path = _auto_path(name)
    if not path:
        flash('نسخة غير موجودة.', 'error')
        return redirect(url_for('backup.index'))

    with open(path, 'rb') as fh:
        data = fh.read()

    return Response(data, mimetype='application/x-sqlite3', headers={
        'Content-Disposition': f'attachment; filename="{os.path.basename(path)}"',
        'Content-Length': str(len(data)),
        'Cache-Control': 'no-store',
    })


@backup_bp.route('/auto/delete', methods=['POST'])
@login_required
@admin_required
def auto_delete():
    path = _auto_path(request.form.get('name'))
    if not path:
        flash('نسخة غير موجودة.', 'error')
    else:
        os.remove(path)
        flash('حُذفت النسخة.', 'success')
    return redirect(url_for('backup.index'))
