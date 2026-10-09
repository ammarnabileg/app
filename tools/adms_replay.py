# -*- coding: utf-8 -*-
"""إعادة تشغيل `adms_protocol.log` حقيقيّ على نسخةٍ معزولة — حارسُ مسار ADMS (خارطة الطريق، المرحلة ٠).

كلُّ ما يرسله جهازٌ ADMS يُكتب في `adms_protocol.log` (routes/adms_routes.py). هذه الأداة
تقرؤه وتعيد إرساله بالترتيب لنسخةٍ جديدة من البرنامج في مجلّدٍ مؤقّت، ثمّ تطبع «لقطة»
ثابتة لما انتهى إليه (الحضور، المنتظر، المتجاهَل، الموظّفون، البصمات، المستخدمون).

    python tools/adms_replay.py adms_protocol.log                 # اطبع اللقطة
    python tools/adms_replay.py adms_protocol.log --save golden.json
    python tools/adms_replay.py adms_protocol.log --check golden.json   # يفشل لو اتغيّرت
    python tools/adms_replay.py log --db نسخة.db                  # ابدأ من نسخة قاعدة (لا تُعدَّل)

الاختبار `tests/test_adms_replay.py` يشغّل كلَّ `*.log` بجوار `.json` لها في:
- `tests/fixtures/adms/`         — سجلّاتٌ مصنوعة (في git)؛
- `tests/fixtures/adms_private/` — سجلّاتٌ حقيقيّة من عملاء، **خارج git** (.gitignore): فيها أسماءٌ
  وحضورُ ناسٍ حقيقيّين. ضع السجلّ هناك و`--save` مرّةً واحدة، والاختبار يحرسه على جهازك.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HEAD = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - (Table|QUERYDATA): ([^|]*)\| SN: ([^|]*)\|.*$')


def parse(text):
    """[(kind 'cdata'|'querydata', table, sn, body)] بالترتيب."""
    out, cur = [], None
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m:
            if cur:
                out.append(cur)
            kind = 'cdata' if m.group(1) == 'Table' else 'querydata'
            cur = [kind, m.group(2).strip(), m.group(3).strip(), []]
        elif cur is not None:
            cur[3].append(line)
    if cur:
        out.append(cur)
    return [(k, t, s, '\n'.join(b).strip('\n') + '\n') for k, t, s, b in out if s and s != 'None']


def _rows(con, sql):
    return [list(r) for r in con.execute(sql).fetchall()]


def snapshot(con):
    """ما انتهت إليه القاعدة — مرتّبًا ومستقلًّا عن الأرقام التلقائيّة والأوقات الحاليّة."""
    def has(t):
        return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone())
    snap = {
        'attendance': _rows(con, '''SELECT e.employee_number, d.device_name, a.check_time, CAST(a.check_type AS TEXT)
                                    FROM attendance_records a JOIN employees e ON e.id = a.employee_id
                                    LEFT JOIN fingerprint_devices d ON d.id = a.device_id ORDER BY 1, 3, 2'''),
        'pending': _rows(con, 'SELECT pin, check_time FROM pending_punches ORDER BY 1, 2') if has('pending_punches') else [],
        'ignored': _rows(con, '''SELECT e.employee_number, i.check_time FROM ignored_punches i
                                 JOIN employees e ON e.id = i.employee_id ORDER BY 1, 2''') if has('ignored_punches') else [],
        'employees': _rows(con, 'SELECT employee_number, name, COALESCE(is_active, 1) FROM employees ORDER BY 1'),
        'device_users': _rows(con, '''SELECT d.device_name, u.user_id, u.name, u.privilege, u.card_number
                                      FROM fingerprint_users u JOIN fingerprint_devices d ON d.id = u.device_id
                                      ORDER BY 1, 2'''),
        'templates': [[r[0], r[1], r[2], r[3], hashlib.sha256(str(r[4]).encode()).hexdigest()[:12]] for r in
                      con.execute('''SELECT pin, finger_id, template_type, COALESCE(major_ver, ''), template_data
                                     FROM fingerprint_templates ORDER BY 1, 2, 3, 4''')] if has('fingerprint_templates') else [],
    }
    return snap


def replay(log_text, db_copy=None, devices=None):
    """يشغّل السجلّ على نسخةٍ معزولة ويُرجع اللقطة. `devices`: أرقام تسلسليّة تُسجَّل (افتراضيًّا كلّ ما في السجلّ)."""
    entries = parse(log_text)
    tmp = tempfile.mkdtemp(prefix='adms_replay_')
    old_env = os.environ.get('HR_DATA_DIR')
    os.environ['HR_DATA_DIR'] = tmp
    try:
        if db_copy:
            shutil.copy2(db_copy, os.path.join(tmp, 'hr_system.db'))
        sys.path.insert(0, ROOT)
        import importlib
        import sqlite3
        import utils.db as db
        importlib.reload(db)
        db.init_db()
        con = sqlite3.connect(db.DB_PATH)
        for sn in sorted(set(devices or [s for _k, _t, s, _b in entries])):
            if not con.execute('SELECT 1 FROM fingerprint_devices WHERE device_name = ?', (sn,)).fetchone():
                con.execute("INSERT INTO fingerprint_devices (device_name, device_ip, is_active, is_adms) VALUES (?, ?, 1, 1)",
                            (sn, 'replay-' + sn))
        con.commit()
        import routes.adms_routes as R
        importlib.reload(R)
        import adms_server
        importlib.reload(adms_server)
        c = adms_server.app.test_client()
        statuses = []
        for sn in sorted({s for _k, _t, s, _b in entries}):
            c.get(f'/iclock/cdata?SN={sn}&options=all')
        for kind, table, sn, body in entries:
            if kind == 'cdata':
                r = c.post(f'/iclock/cdata?SN={sn}&table={table}&Stamp=1', data=body.encode('utf-8'),
                           content_type='text/plain')
            else:
                r = c.post(f'/iclock/querydata?SN={sn}&type=tabledata&tablename={table}&count=1&packcnt=1&packidx=1',
                           data=body.encode('utf-8'), content_type='text/plain')
            statuses.append(r.status_code)
        con2 = sqlite3.connect(db.DB_PATH)
        snap = snapshot(con2)
        con2.close()
        con.close()
        snap['requests'] = len(entries)
        snap['http_errors'] = sum(1 for s in statuses if s >= 500)
        return snap
    finally:
        if old_env is None:
            os.environ.pop('HR_DATA_DIR', None)
        else:
            os.environ['HR_DATA_DIR'] = old_env
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('log')
    ap.add_argument('--db')
    ap.add_argument('--save')
    ap.add_argument('--check')
    a = ap.parse_args(argv)
    with open(a.log, encoding='utf-8', errors='replace') as f:
        snap = replay(f.read(), db_copy=a.db)
    text = json.dumps(snap, ensure_ascii=False, indent=1)
    if a.save:
        with open(a.save, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print(f'اتحفظت اللقطة: {a.save}')
    elif a.check:
        with open(a.check, encoding='utf-8') as f:
            want = json.load(f)
        if want != json.loads(text):
            for k in want:
                if want[k] != snap.get(k):
                    print(f'✗ اتغيّر: {k}')
            return 1
        print('✓ نفس النتيجة')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
