# -*- coding: utf-8 -*-
"""بصماتُ الموظّف بكلّ إصداراتها — ولكلّ جهازٍ النسخةُ التي يقرؤها.

## المشكلة (حتى ٢.٣٣.٠)

الجهازُ يحفظ البصمةَ بخوارزميّة إصدارٍ معيّن (ZKFinger 10 في K40، وقد يكون غيرَه في
SpeedFace)، والبصمةُ بإصدارٍ لا تُقرأ على جهازٍ بإصدارٍ آخر. وكان الجدول يحفظ
**نسخةً واحدة لكلّ إصبع** (`UNIQUE(pin, finger_id)`):
- سحبُ إصبعٍ من K40 بعد سحبه من SpeedFace يمحو نسخةَ SpeedFace (والعكس)؛
- ووجهُ SpeedFace (BIODATA Type=9، No=0) يمحو بصمةَ الإصبع 0؛
- والرفعُ لـADMS يرسل أيَّ نسخةٍ دون نظرٍ لإصدار الجهاز.

الحضورُ لم يتأثّر قطّ: الجهازُ يطابق البصمةَ عنده ويرسل رقمَ الموظّف فقط.

## الآن

- مفتاحُ الصفّ: (الموظّف، الإصبع، النوع، الإصدار) — نسخةُ K40 ونسخةُ SpeedFace
  ووجهُه جنبًا إلى جنب.
- الرفعُ يختار لكلّ إصبع النسخةَ التي بإصدار الجهاز (`pick`). الجهازُ المباشر يُسأل
  عن إصداره، وجهازُ ADMS يُعرف إصدارُه ممّا يرسله (INFO في getrequest، أو MajorVer
  في BIODATA) ويُحفظ في `fingerprint_devices.bio_versions`.
- لا نسخةَ بإصداره → لا تُرسل (لا تنفع)، ويُقال للمستخدم أن يبصم عليه مرّةً.

أنواعُ BIODATA عند ZKTeco: 1 إصبع، 2 وجه (أشعّة تحت حمراء)، 8 كفّ، 9 وجه (ضوء مرئي).
"""
import json

FINGER = 1
TYPE_LABELS = {1: 'بصمة', 2: 'وجه', 8: 'كف', 9: 'وجه'}


def norm_ver(v):
    """'10' و10 و'10.0' إصدارٌ واحد. فارغٌ → ''."""
    if v is None:
        return ''
    s = str(v).strip()
    if not s or s.lower() == 'none':
        return ''
    return s.split('.')[0]


def migrate(conn):
    """الجدولُ القديم بقيد (pin, finger_id) يُعاد بناؤه بلا القيد، وفهرسٌ فريدٌ بالإصدار.

    يُنادى من init_db عند كلّ إقلاع — ولا يفعل شيئًا بعد أوّل مرّة.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                       "AND name = 'fingerprint_templates'").fetchone()
    if not row:
        return False
    sql = ' '.join(str(row[0]).split()).lower().replace(' ,', ',')
    rebuilt = False
    if 'unique(pin, finger_id)' in sql or 'unique (pin, finger_id)' in sql:
        conn.execute('DROP TABLE IF EXISTS fingerprint_templates_v2')
        conn.execute('''CREATE TABLE fingerprint_templates_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_sn TEXT,
            pin TEXT NOT NULL,
            finger_id INTEGER NOT NULL,
            valid INTEGER DEFAULT 1,
            duress INTEGER DEFAULT 0,
            template_type INTEGER DEFAULT 1,
            major_ver TEXT,
            minor_ver TEXT,
            format TEXT,
            template_data TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.execute('''INSERT INTO fingerprint_templates_v2
            (id, device_sn, pin, finger_id, valid, duress, template_type, major_ver, minor_ver, format,
             template_data, created_at)
            SELECT id, device_sn, pin, finger_id, valid, duress, COALESCE(template_type, 1), major_ver,
                   minor_ver, format, template_data, created_at FROM fingerprint_templates''')
        conn.execute('DROP TABLE fingerprint_templates')
        conn.execute('ALTER TABLE fingerprint_templates_v2 RENAME TO fingerprint_templates')
        rebuilt = True
    # الإصدارُ بصيغةٍ واحدة قبل الفهرس ('10.0' و'10' نسخةٌ واحدة).
    for r in conn.execute("SELECT id, major_ver FROM fingerprint_templates WHERE major_ver IS NOT NULL").fetchall():
        nv = norm_ver(r[1])
        if nv != str(r[1]):
            conn.execute('UPDATE fingerprint_templates SET major_ver = ? WHERE id = ?', (nv or None, r[0]))
    # تكرارٌ بنفس المفتاح (من قبل الفهرس): يبقى الأحدث.
    conn.execute('''DELETE FROM fingerprint_templates WHERE id NOT IN (
        SELECT MAX(id) FROM fingerprint_templates
        GROUP BY pin, finger_id, COALESCE(template_type, 1), COALESCE(major_ver, ''))''')
    conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS ux_fp_templates_version ON fingerprint_templates
        (pin, finger_id, COALESCE(template_type, 1), COALESCE(major_ver, ''))''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_fp_templates_pin ON fingerprint_templates (pin)')
    return rebuilt


def save(conn, pin, finger_id, template_b64, template_type=FINGER, major_ver=None, minor_ver=None,
         fmt=None, valid=1, duress=0, device_sn=None, replace=True):
    """يحفظ نسخةً — تستبدل نسخةَ الإصبع نفسِه بالإصدار نفسِه وحدها."""
    verb = 'INSERT OR REPLACE' if replace else 'INSERT OR IGNORE'
    cur = conn.execute(f'''{verb} INTO fingerprint_templates
        (device_sn, pin, finger_id, valid, duress, template_type, major_ver, minor_ver, format, template_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                       (device_sn, str(pin).strip(), int(finger_id), int(valid or 1), int(duress or 0),
                        int(template_type or FINGER), norm_ver(major_ver) or None, minor_ver, fmt,
                        template_b64))
    return cur.rowcount


# ------------------------------------------------------------ إصدارُ الجهاز

def device_versions(device):
    """{نوع: إصدار} المعروفة لجهاز ADMS (من bio_versions)."""
    try:
        raw = device['bio_versions']
    except (IndexError, KeyError, TypeError):
        raw = None
    try:
        return {int(k): norm_ver(v) for k, v in json.loads(raw or '{}').items() if norm_ver(v)}
    except Exception:
        return {}


def remember_version(conn, device_id, template_type, version):
    """إصدارُ نوعٍ على جهاز ADMS — كما أبلغ به الجهازُ نفسُه."""
    v = norm_ver(version)
    if not device_id or not v:
        return
    row = conn.execute('SELECT bio_versions FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
    if row is None:
        return
    try:
        cur = json.loads(row[0] or '{}')
    except Exception:
        cur = {}
    if cur.get(str(int(template_type))) == v:
        return
    cur[str(int(template_type))] = v
    conn.execute('UPDATE fingerprint_devices SET bio_versions = ? WHERE id = ?',
                 (json.dumps(cur, sort_keys=True), device_id))


def parse_info(info):
    """INFO في getrequest: «إصدار البرنامج، مستخدمين، بصمات، حركات، IP، إصدار البصمة،
    إصدار الوجه، ...» → {1: إصدار البصمة، 2: إصدار الوجه}."""
    parts = [p.strip() for p in str(info or '').split(',')]
    out = {}
    if len(parts) > 5 and norm_ver(parts[5]).isdigit():
        out[FINGER] = norm_ver(parts[5])
    if len(parts) > 6 and norm_ver(parts[6]).isdigit() and norm_ver(parts[6]) != '0':
        out[2] = norm_ver(parts[6])
    return out


# ------------------------------------------------------------ أيُّ نسخةٍ لأيّ جهاز

def pick(conn, pin, versions=None, device_sn=None, types=None):
    """لكلّ (نوع، إصبع) النسخةُ التي تنفع على جهازٍ إصداراتُه `versions` ({نوع: إصدار}).

    - إصدارُ الجهاز لهذا النوع معروف: نسختُه بالضبط، وإلّا نسخةٌ بلا إصدارٍ مسجَّل
      (بياناتٌ قديمة — تُجرَّب)، وإلّا لا شيء (لا تنفع).
    - غيرُ معروف: النسخةُ التي جاءت من هذا الجهاز نفسِه، وإلّا الأحدث.
    يُرجع (الصفوف المختارة، عدد الأصابع التي لا نسخةَ لها بإصداره).
    """
    versions = versions or {}
    rows = conn.execute('SELECT * FROM fingerprint_templates WHERE pin = ? ORDER BY id DESC',
                        (str(pin).strip(),)).fetchall()
    groups = {}
    for r in rows:
        t = int(r['template_type'] or FINGER)
        if types is not None and t not in types:
            continue
        groups.setdefault((t, int(r['finger_id'])), []).append(r)
    chosen, skipped = [], 0
    for (t, _fid), cands in sorted(groups.items()):
        want = versions.get(t)
        best = None
        if want:
            best = next((c for c in cands if norm_ver(c['major_ver']) == want), None)
            if best is None:
                best = next((c for c in cands if not norm_ver(c['major_ver'])), None)
            if best is None:
                skipped += 1
                continue
        else:
            if device_sn:
                best = next((c for c in cands if str(c['device_sn'] or '') == str(device_sn)), None)
            best = best or cands[0]
        chosen.append(best)
    return chosen, skipped


def summary(conn, pins=None):
    """{pin: [(نوع، إصدار، عدد)]} — لعرض ما عند كلّ موظّف في صفحة مستخدمي الأجهزة."""
    out = {}
    for r in conn.execute('''SELECT pin, COALESCE(template_type, 1) t, COALESCE(major_ver, '') v, COUNT(*) n
                             FROM fingerprint_templates GROUP BY 1, 2, 3 ORDER BY 1, 2, 3''').fetchall():
        if pins is not None and r[0] not in pins:
            continue
        out.setdefault(str(r[0]), []).append((int(r[1]), r[2], int(r[3])))
    return out
