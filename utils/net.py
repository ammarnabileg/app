"""عنوان العميل الحقيقي — لا الذي يدّعيه.

قيد «يجب أن تكون على شبكة الفرع» يقوم كلّه على عنوان IP، وكان يُقرأ هكذا:

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

وX-Forwarded-For ترويسة يكتبها من يرسل الطلب. جرّبتُه على نظام يعمل:
موظف خارج الشبكة رُدّ بـ403، وبإضافة سطر واحد

    X-Forwarded-For: <عنوان الفرع>

سُجّلت بصمة حضوره، ودُوّن في ملاحظتها عنوان الفرع نفسه. فالقيد الذي
اشترته الشركة ليمنع البصمة من البيت كان يُتجاوَز من البيت.

الترويسة ليست بلا فائدة: خلف وسيط (Traefik في نشر Coolify) هي المصدر
الوحيد لعنوان العميل، وrequest.remote_addr يكون عنوان الوسيط نفسه.
فالسؤال ليس «أنصدّقها؟» بل «كم وسيطًا أمامنا؟» — وهذا يعرفه من نصّب
النظام وحده:

    HR_TRUSTED_PROXY_HOPS=1     خلف وسيط واحد (النشر السحابي)
    HR_TRUSTED_PROXY_HOPS=0     لا وسيط (التركيب المحلي) — وهو الافتراضي

والافتراض صفر عن قصد: التركيب المحلي في مكتب العميل هو الحال الذي
يُستعمل فيه قيد الشبكة أصلًا، وفيه لا وسيط، فتُهمَل الترويسة تمامًا.
"""

import os

_SETTING = 'portal_trusted_proxy_hops'


def trusted_proxy_hops(conn=None):
    """عدد الوسطاء الموثوقين أمام النظام. صفر = لا تُقرأ الترويسة."""
    raw = os.environ.get('HR_TRUSTED_PROXY_HOPS')

    if raw is None and conn is not None:
        try:
            row = conn.execute(
                'SELECT setting_value FROM salary_settings_v2 WHERE setting_name = ?',
                (_SETTING,)).fetchone()
            if row:
                raw = row[0] if not hasattr(row, 'keys') else row['setting_value']
        except Exception:
            raw = None

    try:
        hops = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    # حدٌّ أعلى معقول: رقم كبير يعني الوصول إلى أول عنوان في السلسلة،
    # وهو بالضبط ما يتحكّم فيه المهاجم.
    return max(0, min(hops, 8))


def client_ip(request, conn=None):
    """عنوان من أرسل الطلب فعلًا.

    كل وسيط يُلحق بالسلسلة عنوانَ من كلّمه، فآخرها هو ما أضافه أقرب
    وسيط إلينا. فإن كان أمامنا n وسيطًا موثوقًا، فعنوان العميل هو
    العنصر رقم n من الآخر — وما قبله كتبه العميل ولا يُعتدّ به.
    """
    remote = (request.remote_addr or '').strip()
    hops = trusted_proxy_hops(conn)
    if hops <= 0:
        return remote

    chain = [p.strip() for p in
             (request.headers.get('X-Forwarded-For') or '').split(',') if p.strip()]
    if not chain:
        return remote

    idx = len(chain) - hops
    if idx < 0:
        # السلسلة أقصر مما أُعلن: لا نأخذ أول عنصر فيها لأنه من العميل.
        return remote
    return chain[idx]


def ip_allowed(ip, patterns):
    """أينتمي العنوان إلى إحدى الصيغ المسموح بها؟

    الصيغة إمّا عنوانًا كاملًا أو بادئةً منتهيةً بـ'*'. والمطابقة على
    حدود النقاط لا على النصّ: 'startswith' وحدها تجعل 10.0.0.1 تقبل
    10.0.0.100 — عنوانًا آخر على شبكة أخرى.
    """
    ip = (ip or '').strip()
    if not ip:
        return False

    for raw in patterns:
        pat = (raw or '').strip()
        if not pat:
            continue
        if pat.endswith('*'):
            prefix = pat[:-1].rstrip('.')
            if ip == prefix or ip.startswith(prefix + '.'):
                return True
        elif ip == pat:
            return True
    return False
