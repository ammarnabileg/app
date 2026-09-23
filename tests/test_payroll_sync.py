"""رفعُ كشف الرواتب المحسوب — لا حسابٌ ثانٍ له في السحابة.

## لماذا يُرفع المحسوب لا المكوّنات

البوّابة كانت تعرض وقائعَ البصمات وحدها، لأن قواعدَ التأخير
والإضافي لا تُرفع. فالكشفُ المعتمَد لا يُبنى هناك.

والصوابُ ألّا يُنسخ المحرّك: نسختان من قاعدةٍ واحدة تفترقان يومًا،
ولا يُعرف أيُّهما الصحيحة. فيُرفع ما حسبه المحرّكُ نفسُه — الصفُّ
في السحابة **هو** الصفُّ في المقرّ.

## وثلاثةُ شروطٍ تُحرَس هنا

**(١) المسودّةُ لا تغادر المقرّ.** كشفٌ لم يُعتمد شغلٌ جارٍ: أرقامُه
تتبدّل وقد يُلغى. وعرضُه على أنه راتبُ الشهر يجعل العميل يبني عليه
ثم يجده تغيّر.

واسمُها في المخطَّط `saved` لا `draft` — والقيدُ
`status IN ('saved','approved')` يرفض ما عداهما.

**(٢) وما فُكّ قفلُه يُحذف من السحابة.** أخطرُ الثلاثة: كشفٌ اعتُمد
فرُفع، ثم فُكّ قفلُه للتعديل — فلو بقي في السحابة معتمَدًا لظلّ
العميل يقرأ رقمًا سُحب.

**(٣) وأرقامُ المستخدمين لا تخرج.** `created_by` وأخواتُها تشير إلى
`users`، وهو جدولٌ لا يُرفع أصلًا (فيه كلماتُ المرور) — فالأرقامُ
هناك بلا معنى، و`unlock_reason` ملاحظةٌ إداريّة.

يُشغَّل:  python -m pytest tests/test_payroll_sync.py -v
"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def live(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.cloud_outbox as ob
    import utils.cloud_sync as cs
    importlib.reload(ob)
    importlib.reload(cs)

    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    yield conn, ob, cs, db
    conn.close()


def _run(conn, run_id, status='saved', month=3, year=2026):
    conn.execute(
        'INSERT INTO payroll_runs (id, month, year, status, employees_count,'
        ' total_basic, total_allowances, total_deductions, total_net,'
        ' period_start, period_end, created_by, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (run_id, month, year, status, 2, 1000.0, 100.0, 50.0, 1050.0,
         f'{year}-{month:02d}-01', f'{year}-{month:02d}-28', 7, 'x'))
    conn.execute(
        'INSERT INTO payroll_run_lines (run_id, employee_id, basic,'
        ' required_hours, actual_hours, total_allowances, total_deductions,'
        ' net, details_json) VALUES (?,?,?,?,?,?,?,?,?)',
        (run_id, 1, 500.0, 200.0, 195.0, 50.0, 25.0, 525.0,
         json.dumps({'allowances': [{'name': 'بدل سكن', 'amount': 50}],
                     'deductions': [{'name': 'تأخير', 'amount': 25}]},
                    ensure_ascii=False)))
    conn.commit()


def _drain(conn, cs, ob):
    """يُنهي المشي الأوّل فتُعزل آثارُ الدفتر وحده."""
    for _ in range(300):
        batch = cs.build_batch(conn)
        if not batch['baseline_marks']:
            break
        cs.commit_batch(conn, batch)
    ob.ack(conn, ob.high_water(conn, limit=100000))


def _collect(conn, cs, ob, table):
    """ما يصل السحابةَ لهذا الجدول: (صفوفٌ بلا تكرار، محذوفات).

    الصفُّ قد يخرج مرّتين — مرّةً في المشي الأوّل ومرّةً من الدفتر —
    والخادمُ يُدمج لا يُضاعف. فالمقيسُ **أيُّ الصفوف وصلت** لا كم
    مرّةً أُرسلت؛ وعدُّ الإرسالات يجعل الاختبارَ يرصد تفصيلَ نقلٍ
    لا أثرًا يراه أحد.
    """
    seen, dels = {}, []
    for _ in range(50):
        batch = cs.build_batch(conn)
        for row in batch['data'].get(table, []):
            seen[row['id']] = row
        dels.extend(batch['deletes'].get(table, []))
        if not batch['baseline_marks'] and not batch['up_to_seq']:
            break
        cs.commit_batch(conn, batch)
    return list(seen.values()), dels


# ------------------------------------------------ (١) المسودّة لا تخرج

def test_draft_payroll_never_leaves(live):
    """القلب. أرقامُ المسودّة تتبدّل، وقد تُلغى."""
    conn, ob, cs, _db = live
    _run(conn, 1, status='saved')

    runs, _ = _collect(conn, cs, ob, 'payroll_runs')
    lines, _ = _collect(conn, cs, ob, 'payroll_run_lines')
    assert runs == [], runs
    assert lines == [], lines


def test_approved_payroll_is_uploaded(live):
    conn, ob, cs, _db = live
    _run(conn, 1, status='approved')

    runs, _ = _collect(conn, cs, ob, 'payroll_runs')
    assert len(runs) == 1, runs
    assert runs[0]['status'] == 'approved'
    assert runs[0]['total_net'] == 1050.0


def test_lines_follow_their_run(live):
    """السطرُ لا حالةَ له — يتبع حالَ كشفه."""
    conn, ob, cs, _db = live
    # شهران: المخطَّط يفرض كشفًا واحدًا لكلّ شهر
    # (UNIQUE على month, year).
    _run(conn, 1, status='saved', month=3)
    _run(conn, 2, status='approved', month=4)

    lines, _ = _collect(conn, cs, ob, 'payroll_run_lines')
    assert len(lines) == 1, lines
    assert lines[0]['run_id'] == 2


def test_approving_a_draft_sends_it(live):
    """الاعتمادُ تحديثٌ، والمشغّلُ يلتقطه."""
    conn, ob, cs, _db = live
    _run(conn, 1, status='saved')
    _drain(conn, cs, ob)

    conn.execute("UPDATE payroll_runs SET status = 'approved' WHERE id = 1")
    conn.commit()

    runs, _ = _collect(conn, cs, ob, 'payroll_runs')
    assert len(runs) == 1 and runs[0]['status'] == 'approved'


# ------------------------------------------------ (٢) فكُّ القفل يحذف

def test_unlocking_deletes_it_from_the_cloud(live):
    """أخطرُ ما هنا.

    كشفٌ اعتُمد فرُفع ثم فُكّ قفلُه: لولا الحذف لظلّ العميل يقرأ
    رقمًا سُحب — ويظنّه نهائيًّا لأنه كان كذلك.
    """
    conn, ob, cs, _db = live
    _run(conn, 1, status='approved')
    runs, _ = _collect(conn, cs, ob, 'payroll_runs')
    assert len(runs) == 1, 'لم يُرفع أصلًا'

    conn.execute("UPDATE payroll_runs SET status = 'saved' WHERE id = 1")
    conn.commit()

    runs2, dels = _collect(conn, cs, ob, 'payroll_runs')
    assert runs2 == [], 'المسودّة خرجت بعد فكّ القفل'
    assert 1 in dels, f'لم يُرسَل حذفٌ للكشف — المحذوفات: {dels}'


def test_deleting_lines_sends_deletes(live):
    """إعادةُ الحساب تمحو السطور وتُعيدها — والمحو يصل."""
    conn, ob, cs, _db = live
    _run(conn, 1, status='approved')
    lines, _ = _collect(conn, cs, ob, 'payroll_run_lines')
    assert len(lines) == 1
    line_id = lines[0]['id']

    conn.execute('DELETE FROM payroll_run_lines WHERE run_id = 1')
    conn.commit()
    _, dels = _collect(conn, cs, ob, 'payroll_run_lines')
    assert line_id in dels, dels


# ------------------------------------------------ (٣) ما لا يخرج

def test_user_ids_and_admin_notes_are_withheld(live):
    conn, ob, cs, _db = live
    _run(conn, 1, status='approved')
    conn.execute("UPDATE payroll_runs SET unlock_reason = 'خطأ داخليّ فادح',"
                 " approved_by = 9, unlocked_by = 9 WHERE id = 1")
    conn.commit()

    runs, _ = _collect(conn, cs, ob, 'payroll_runs')
    assert runs, 'لم يُرفع'
    row = runs[-1]
    for col in ('created_by', 'approved_by', 'unlocked_by', 'unlock_reason'):
        assert col not in row, f'{col} غادر المقرّ'
    flat = json.dumps(row, ensure_ascii=False)
    assert 'خطأ داخليّ فادح' not in flat


def test_the_breakdown_does_travel(live):
    """التفصيلُ هو الفائدة: «خُصم كذا ولماذا»."""
    conn, ob, cs, _db = live
    _run(conn, 1, status='approved')
    lines, _ = _collect(conn, cs, ob, 'payroll_run_lines')
    d = json.loads(lines[0]['details_json'])
    assert d['allowances'][0]['name'] == 'بدل سكن'
    assert d['deductions'][0]['name'] == 'تأخير'
    assert lines[0]['required_hours'] == 200.0
    assert lines[0]['actual_hours'] == 195.0


# ------------------------------------------------ المشي الأوّل

def test_baseline_batch_itself_skips_filtered_rows(live):
    """`baseline_batch` لا تُرجع ما يسقط من الترشيح.

    والأثرُ لا يظهر في الحمولة: `read_rows` ترشّح ثانيةً، فالمسودّة
    تُحجب في الحالين. فلولا قياسُ الدالّة نفسِها لبقي هذا الترشيح
    حارسًا لا يُمكن أن يفشل — أي شيفرةً تُطمئن قارئَها بلا سبب.
    وأثرُه الحقيقيّ في الميزانيّة: `build_batch` تخصم `len(ids)` من
    سقف الدفعة، فصفوفٌ تُقرأ ثم تُرمى تلتهم سقفَ أوّل رفع.
    """
    conn, ob, cs, _db = live
    _run(conn, 1, status='saved', month=3)
    _run(conn, 2, status='approved', month=4)

    ids = ob.baseline_batch(conn, 'payroll_runs', size=100)
    assert ids == [2], f'المشيُ أعاد مسودّة: {ids}'

    line_ids = ob.baseline_batch(conn, 'payroll_run_lines', size=100)
    rows = ob.read_rows(conn, 'payroll_run_lines', line_ids)
    assert [r['run_id'] for r in rows] == [2], rows


def test_baseline_walk_is_filtered_too(live):
    """تركيبٌ فيه مسودّاتٌ قديمة: لولا ترشيحُ المشي لخرجت كلُّها
    في أوّل رفعٍ ولم يمنعها شيء."""
    conn, ob, cs, _db = live
    # صفوفٌ سابقةٌ لتثبيت الدفتر: تُكتب ثم يُمحى ما سجّله المشغّل.
    _run(conn, 1, status='saved', month=3)
    _run(conn, 2, status='approved', month=4)
    ob.ack(conn, ob.high_water(conn, limit=100000))

    seen = []
    for _ in range(300):
        batch = cs.build_batch(conn)
        seen.extend(batch['data'].get('payroll_runs', []))
        if not batch['baseline_marks']:
            break
        cs.commit_batch(conn, batch)

    ids = sorted(r['id'] for r in seen)
    assert ids == [2], f'خرجت مسودّة في المشي الأوّل: {ids}'


# ------------------------------------------------ التسجيل

def test_tables_are_registered_and_filtered(live):
    _conn, ob, _cs, _db = live
    assert 'payroll_runs' in ob.SYNC_TABLES
    assert 'payroll_run_lines' in ob.SYNC_TABLES
    assert 'payroll_runs' in ob.SYNC_FILTERS
    assert 'payroll_run_lines' in ob.SYNC_FILTERS


def test_triggers_exist_for_both(live):
    """بلا مشغّلات لا يُسجَّل تغيير، فلا يُرفع شيءٌ بعد الأوّل."""
    conn, _ob, _cs, _db = live
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    for table in ('payroll_runs', 'payroll_run_lines'):
        got = [n for n in names if table in n]
        assert len(got) >= 3, f'{table}: {got}'


def test_settings_tables_still_do_not_sync(live):
    """القواعدُ تبقى في المقرّ — وهي علّةُ رفعِ المحسوب لا المكوّنات."""
    _conn, ob, _cs, _db = live
    assert 'salary_settings_v2' not in ob.SYNC_TABLES
    assert 'system_settings' not in ob.SYNC_TABLES
    assert 'users' not in ob.SYNC_TABLES
