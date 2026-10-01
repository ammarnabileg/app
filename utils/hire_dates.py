# -*- coding: utf-8 -*-
"""تاريخُ تعيين الموظّف المضاف من جهاز البصمة.

## العطب

«مزامنة المستخدمين» تُنشئ الموظّفَ الجديد بتاريخ تعيينٍ = يومِ المزامنة،
ثم «مزامنة الآن» تسحب بصماته القديمة من الجهاز (الجهازُ يحفظها كلَّها).
فموظّفٌ أُضيف اليوم له بصماتُ سبتمبر، لكنّ كشفَ سبتمبر لا يراه: الكشفُ
يعدّ من عُيّن قبل نهاية الفترة. وإن كان كلُّ الموظّفين كذلك (تركيبٌ جديد
أُخذ موظّفوه من الجهاز) صار الكشفُ بلا موظّفين — وكان يسقط بـ
«min() arg is an empty sequence».

## ما يُصحَّح وحده، وما لا يُصحَّح

يُصحَّح وحده **ما لم يمسّه أحد** فقط: قسمُه ما زال «غير محدد» وراتبُه صفر
وتاريخُ تعيينه هو يومُ إنشائه. هذا تاريخٌ مؤقّت لا معلومة، وأوّلُ بصمةٍ
أدقُّ منه. ويُقيَّد في سجلّ الموظّف.

وما سواه لا يُمسّ: موظّفٌ عدّل المسؤولُ قسمَه أو راتبه قد يكون تاريخُه
صحيحًا والبصماتُ القديمة لموظّفٍ سابقٍ بالرقم نفسه — وتاريخُ التعيين
يحكم الإجازاتِ ومكافأةَ نهاية الخدمة. فيُذكر بالاسم ليُصحَّح بيد صاحبه.
"""
PLACEHOLDER_DEPT = 'غير محدد'


def fix_device_placeholder_hire_dates(conn):
    """تاريخُ التعيين المؤقّت ← أوّلُ بصمة. يُرجع [(id, الاسم, القديم, الجديد)]."""
    rows = conn.execute("""
        SELECT e.id, e.name, e.hire_date,
               (SELECT MIN(DATE(a.check_time)) FROM attendance_records a
                 WHERE a.employee_id = e.id) AS first_punch
        FROM employees e
        WHERE COALESCE(e.department, '') = ?
          AND COALESCE(e.salary, 0) = 0
          AND e.created_at IS NOT NULL
          AND DATE(e.hire_date) = DATE(e.created_at)""", (PLACEHOLDER_DEPT,)).fetchall()
    changed = []
    for r in rows:
        first, hire = r['first_punch'], r['hire_date']
        if not first or not hire or str(first) >= str(hire)[:10]:
            continue
        conn.execute('UPDATE employees SET hire_date = ? WHERE id = ?', (first, r['id']))
        try:
            from utils.payroll_engine import log_employee_event
            log_employee_event(conn, r['id'], 'hire_date', str(hire)[:10], first, 'status',
                               source='device_sync',
                               note='أُضيف من جهاز البصمة بتاريخ المزامنة — صُحّح إلى أوّل بصمة له')
        except Exception:
            pass
        changed.append((r['id'], r['name'], str(hire)[:10], first))
    if changed:
        conn.commit()
    return changed


def hired_after_period_with_punches(conn, p_start, p_end):
    """موظّفون لهم بصماتٌ في الفترة وتاريخُ تعيينهم بعدها (أو فارغ) — فليسوا في كشفها."""
    return conn.execute("""
        SELECT e.id, e.name, e.employee_number, e.hire_date
        FROM employees e
        WHERE (e.hire_date IS NULL OR DATE(e.hire_date) IS NULL OR DATE(e.hire_date) > ?)
          AND EXISTS (SELECT 1 FROM attendance_records a WHERE a.employee_id = e.id
                      AND DATE(a.check_time) BETWEEN ? AND ?)
        ORDER BY CAST(e.employee_number AS INTEGER), e.employee_number""",
        (p_end.isoformat(), p_start.isoformat(), p_end.isoformat())).fetchall()
