# -*- coding: utf-8 -*-
"""أرصدة الخصومات اللي ماتنفّذتش: الجزء من بند اتحسب في الكشف وماتخصمش.

## ليه
الصافي مابينزلش تحت الصفر، وجزاءات الحضور ليها سقف، وأقساط السلف ليها حد. قبل كده الجزء اللي
ماتخصمش كان بيتشال من الكشف في صمت — والبند اللي ماتخصمش منه ولا فلس كان بيختفي من السطر خالص.
دلوقتي المحرك بيحفظه مع السطر (`unexecuted`)، والملف ده بيسجّله **رصيد** عند اعتماد الكشف.

## إمتى بيتسجل
- **عند اعتماد الكشف** (`lock_run`) — مش في المعاينة ولا الحفظ.
- **فتح الشهر** (`unlock_run`) بيعمل عكس واضح: أرصدة الكشف ده بتبقى «اتعكست» (مابتتمسحش)،
  واعتماده تاني بيسجل أرصدة جديدة. فإعادة الحساب أو الفتح والقفل مابيخصموش مرتين.

## حالة كل رصيد حسب البند (POLICY)
| البند | الحالة | معناها |
|---|---|---|
| السلفة | `in_loan_balance` | للعلم: الباقي فاضل في رصيد السلفة وبيتحصل حسب جدولها — مفيش حاجة تتعمل هنا |
| استرداد صرف زائد، سلفة على الراتب | `pending_review` | مستحق — يتجدول بعد المراجعة |
| حجز قضائي | `court_review` | سجل مستقل — التحصيل حسب أمر المحكمة والمراجعة المختصة |
| جزاء إداري | `review_only` | للمراجعة — مابيتحصلش غير بقرار صريح |
| جزاءات الحضور (تأخير/انصراف/بصمة ناقصة/تواجد) | `not_executed` | غير منفّذ — مابيتحولش دين على الموظف |
| أي بند تاني | `pending_review` | |

## الإجراءات
- **جدولة** (`schedule`): بند في شهر بتختاره بمرجع `BAL-<رقم الرصيد>` — مرة واحدة بس (الرصيد يبقى
  `scheduled`). مسموحة لـ pending_review و court_review، ولـ review_only بملاحظة إلزامية (قرار مستقل).
- **إسقاط** (`waive`) بملاحظة إلزامية — أي حالة مفتوحة.
- `not_executed` و `in_loan_balance` مابيتجدولوش.
"""
import json

POLICY = {
    'loan_installment': 'in_loan_balance',
    'overpayment_recovery': 'pending_review',
    'salary_advance': 'pending_review',
    'advance_installment': 'pending_review',
    'court_garnishment': 'court_review',
    'admin_penalty': 'review_only',
    'lateness_deduction': 'not_executed',
    'early_leave_deduction': 'not_executed',
    'missing_punch_penalty': 'not_executed',
    'presence_penalty': 'not_executed',
}
STATUS_LABELS = {
    'in_loan_balance': 'في رصيد السلفة',
    'pending_review': 'بانتظار المراجعة',
    'court_review': 'حجز قضائي — مراجعة',
    'review_only': 'للمراجعة فقط',
    'not_executed': 'غير منفّذ',
    'scheduled': 'اتجدول',
    'waived': 'اتسقط',
    'reversed': 'اتعكس (الشهر اتفتح)',
}
REASON_LABELS = {'net_floor': 'الصافي مايكفيش', 'penalty_cap': 'سقف الجزاءات', 'loan_cap': 'حد خصم السلف'}
OPEN = ('pending_review', 'court_review', 'review_only')
SCHEDULABLE = ('pending_review', 'court_review', 'review_only')


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS payroll_deduction_balances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        employee_id INTEGER NOT NULL,
        month INTEGER NOT NULL,
        year INTEGER NOT NULL,
        code TEXT,
        name TEXT,
        source TEXT,
        tx_id INTEGER,
        loan_id INTEGER,
        original REAL NOT NULL,
        deducted REAL NOT NULL,
        remaining REAL NOT NULL,
        reason TEXT,
        status TEXT NOT NULL,
        note TEXT,
        scheduled_tx_id INTEGER,
        created_by INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_by INTEGER,
        decided_at DATETIME)''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_ded_bal_emp ON payroll_deduction_balances (employee_id, status)')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_ded_bal_run ON payroll_deduction_balances (run_id)')


def record_for_run(conn, run_id, user_id=None):
    """عند اعتماد الكشف: كل `unexecuted` في سطوره ← رصيد. يُرجع عدد الأرصدة."""
    ensure_schema(conn)
    run = conn.execute('SELECT month, year FROM payroll_runs WHERE id = ?', (run_id,)).fetchone()
    if not run:
        return 0
    if conn.execute("SELECT 1 FROM payroll_deduction_balances WHERE run_id = ? AND status != 'reversed'",
                    (run_id,)).fetchone():
        return 0                                    # اتسجلت قبل كده للاعتماد ده
    n = 0
    for line in conn.execute('SELECT employee_id, details_json FROM payroll_run_lines WHERE run_id = ?',
                             (run_id,)).fetchall():
        try:
            items = (json.loads(line['details_json'] or '{}') or {}).get('unexecuted') or []
        except ValueError:
            items = []
        for u in items:
            if float(u.get('remaining') or 0) <= 0.0005:
                continue
            code = u.get('code') or ''
            status = POLICY.get(code, 'pending_review')
            conn.execute('''INSERT INTO payroll_deduction_balances (run_id, employee_id, month, year, code, name,
                            source, tx_id, loan_id, original, deducted, remaining, reason, status, created_by)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (run_id, line['employee_id'], run['month'], run['year'], code, u.get('name_ar') or code,
                          u.get('source'), u.get('tx_id'), u.get('loan_id'), u['original'], u['deducted'],
                          u['remaining'], u.get('reason'), status, user_id))
            n += 1
    return n


def reverse_for_run(conn, run_id, user_id=None, reason=''):
    """فتح الشهر: أرصدة الاعتماد ده بتتعكس (سجل واضح، مش مسح). المجدول بيتلغي بنده لو لسه في شهر مفتوح."""
    ensure_schema(conn)
    from utils.payroll_engine import is_period_locked
    rows = conn.execute("SELECT * FROM payroll_deduction_balances WHERE run_id = ? AND status != 'reversed'",
                        (run_id,)).fetchall()
    for b in rows:
        if b['scheduled_tx_id']:
            t = conn.execute('SELECT month, year FROM payroll_transactions WHERE id = ?', (b['scheduled_tx_id'],)).fetchone()
            if t and not is_period_locked(conn, t['month'], t['year']):
                conn.execute("UPDATE payroll_transactions SET status = 'cancelled', cancelled_by = ?, "
                             "cancelled_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id, b['scheduled_tx_id']))
        conn.execute("""UPDATE payroll_deduction_balances SET status = 'reversed', decided_by = ?,
                        decided_at = CURRENT_TIMESTAMP, note = TRIM(COALESCE(note, '') || ' ' || ?) WHERE id = ?""",
                     (user_id, f'[اتعكس: {reason}]'.strip(), b['id']))
    return len(rows)


def schedule(conn, balance_id, month, year, user_id, amount=None, note=''):
    """يجدول الرصيد (أو جزء منه) بند خصم في شهر مفتوح — مرة واحدة."""
    from utils.payroll_engine import is_period_locked
    ensure_schema(conn)
    b = conn.execute('SELECT * FROM payroll_deduction_balances WHERE id = ?', (balance_id,)).fetchone()
    if not b or b['status'] not in SCHEDULABLE:
        raise ValueError('الرصيد ده مايتجدولش')
    if b['status'] == 'review_only' and len((note or '').strip()) < 3:
        raise ValueError('الجزاء الإداري مايتجدولش غير بقرار مكتوب — اكتب السبب')
    if is_period_locked(conn, int(month), int(year)):
        raise ValueError('الشهر ده مقفول')
    amt = round(float(amount if amount not in (None, '') else b['remaining']), 3)
    if amt <= 0 or amt > float(b['remaining']) + 0.0005:
        raise ValueError(f"المبلغ لازم يكون بين 0 و {b['remaining']}")
    it = conn.execute('SELECT id FROM payroll_item_types WHERE code = ?', (b['code'],)).fetchone() \
        or conn.execute("SELECT id FROM payroll_item_types WHERE code = 'overpayment_recovery'").fetchone()
    cur = conn.execute('''INSERT INTO payroll_transactions (employee_id, item_type_id, month, year, amount, reason,
                          reference, entered_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                       (b['employee_id'], it[0], int(month), int(year), amt,
                        f"باقي {b['name']} عن {b['month']}/{b['year']}" + (f' — {note}' if note else ''),
                        f'BAL-{balance_id}', user_id))
    conn.execute('''UPDATE payroll_deduction_balances SET status = 'scheduled', scheduled_tx_id = ?, decided_by = ?,
                    decided_at = CURRENT_TIMESTAMP, note = ? WHERE id = ?''', (cur.lastrowid, user_id, note or None, balance_id))
    rest = round(float(b['remaining']) - amt, 3)
    if rest > 0.0005:                              # جدولة جزء: الباقي رصيد جديد بنفس الحالة
        conn.execute('''INSERT INTO payroll_deduction_balances (run_id, employee_id, month, year, code, name, source,
                        tx_id, loan_id, original, deducted, remaining, reason, status, note, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (b['run_id'], b['employee_id'], b['month'], b['year'], b['code'], b['name'], b['source'],
                      b['tx_id'], b['loan_id'], b['original'], b['deducted'], rest, b['reason'], b['status'],
                      f'باقي بعد جدولة {amt} (رصيد {balance_id})', user_id))
    return cur.lastrowid


def waive(conn, balance_id, user_id, note):
    ensure_schema(conn)
    if len((note or '').strip()) < 3:
        raise ValueError('اكتب سبب الإسقاط')
    b = conn.execute('SELECT status FROM payroll_deduction_balances WHERE id = ?', (balance_id,)).fetchone()
    if not b or b['status'] not in OPEN + ('not_executed',):
        raise ValueError('الرصيد ده مش مفتوح')
    conn.execute('''UPDATE payroll_deduction_balances SET status = 'waived', decided_by = ?, decided_at = CURRENT_TIMESTAMP,
                    note = ? WHERE id = ?''', (user_id, note.strip(), balance_id))


def listing(conn, status=None, employee_id=None):
    ensure_schema(conn)
    q = '''SELECT b.*, e.employee_number, COALESCE(e.arabic_name, e.name) AS emp_name
           FROM payroll_deduction_balances b JOIN employees e ON e.id = b.employee_id WHERE 1 = 1'''
    p = []
    if status == 'open':
        q += f" AND b.status IN ({','.join('?' * len(OPEN))})"
        p += list(OPEN)
    elif status:
        q += ' AND b.status = ?'
        p.append(status)
    if employee_id:
        q += ' AND b.employee_id = ?'
        p.append(int(employee_id))
    q += ' ORDER BY b.id DESC LIMIT 500'
    return [dict(r) for r in conn.execute(q, p).fetchall()]
