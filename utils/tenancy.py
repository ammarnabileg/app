# -*- coding: utf-8 -*-
"""Multi-tenant: كل شركة على السيرفر المشترك ليها schema PostgreSQL لوحدها، وفولدر ملفات لوحده.

بيتفعّل بـ `HR_MULTI_TENANT=1` (ومعاه `HR_DB_URL`). من غيره البرنامج شركة واحدة زي ما هو — وده وضع
المكاتب وكل تركيب قديم.

## الشركة بتتحدد إزاي
- **صفحات البرنامج:** من الدومين (`nour.onz.one`، أو دومين خاص متسجّل للشركة). دومين مش معروف = 404،
  مفيش شركة «افتراضية» تتفتح.
- **أجهزة ADMS:** من الرقم التسلسلي (SN) — الجهاز متسجّل لشركة واحدة بس.
- **الشغل اللي في الخلفية:** بيلف على الشركات واحدة واحدة (`for_each`).

## العزل بيقفل على الغلط (fail closed)
- أي اتصال بالقاعدة من غير شركة محددة بيرمي خطأ — مابيفتحش schema حد تاني ولا «الافتراضي».
- الجلسة مربوطة بالشركة: كوكي اتعمل في شركة بيتمسح لو اتبعت لشركة تانية.

## سجل الشركات
schema اسمها `hr_platform` فيها `tenants` (الشركة، الـ schema، الدومينات، الحالة) و`device_routes`
(SN ← الشركة). بتتدار من `tools/tenant.py`.
"""
import contextlib
import contextvars
import os
import re
import threading
import time

PLATFORM = 'hr_platform'
_current = contextvars.ContextVar('hr_tenant', default=None)
_SLUG = re.compile(r'^[a-z][a-z0-9_]{1,40}$')
_cache = {'at': 0.0, 'tenants': {}, 'hosts': {}}
_dev_cache = {}
_lock = threading.Lock()
CACHE_SECONDS = 30


class NoTenant(RuntimeError):
    """اتصال بالقاعدة من غير شركة محددة في وضع multi-tenant."""


def enabled():
    from utils import dbcompat
    return os.environ.get('HR_MULTI_TENANT', '').strip() == '1' and bool(dbcompat.pg_url())


def current():
    return _current.get()


def cache_key():
    """مفتاح للكاش اللي على مستوى العملية: الشركة الحالية ('' في وضع الشركة الواحدة)."""
    return _current.get() or ''


def schema_of(slug):
    return 'c_' + slug


def require():
    """الشركة الحالية — أو خطأ (مفيش شركة افتراضية)."""
    t = _current.get()
    if not t:
        raise NoTenant('مفيش شركة محددة للطلب ده (multi-tenant)')
    return t


@contextlib.contextmanager
def use(slug):
    """`with use('nour'):` كل اتصال جوه البلوك بيروح لشركة nour."""
    if not _SLUG.match(str(slug or '')):
        raise ValueError(f'اسم شركة غلط: {slug!r}')
    tok = _current.set(slug)
    try:
        yield slug
    finally:
        _current.reset(tok)


def set_current(slug):
    """للطلب: بيرجّع token يترجع بيه في teardown."""
    return _current.set(slug)


def reset(token):
    try:
        _current.reset(token)
    except Exception:
        _current.set(None)


# ============================================================ السجل

def _pg():
    import psycopg
    from utils import dbcompat
    return psycopg.connect(dbcompat.pg_url(), autocommit=True)


def ensure_platform():
    with _pg() as c:
        c.execute(f'CREATE SCHEMA IF NOT EXISTS {PLATFORM}')
        c.execute(f'''CREATE TABLE IF NOT EXISTS {PLATFORM}.tenants (
            slug TEXT PRIMARY KEY,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'active',          -- active | suspended
            hosts TEXT[] NOT NULL DEFAULT '{{}}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        c.execute(f'''CREATE TABLE IF NOT EXISTS {PLATFORM}.device_routes (
            sn TEXT PRIMARY KEY,
            slug TEXT NOT NULL REFERENCES {PLATFORM}.tenants(slug) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')


def _load(force=False):
    now = time.time()
    if not force and now - _cache['at'] < CACHE_SECONDS:
        return
    with _lock:
        if not force and now - _cache['at'] < CACHE_SECONDS:
            return
        try:
            with _pg() as c:
                rows = c.execute(f'SELECT slug, name, status, hosts FROM {PLATFORM}.tenants').fetchall()
        except Exception:
            rows = []
        tenants, hosts = {}, {}
        for slug, name, status, hs in rows:
            tenants[slug] = {'slug': slug, 'name': name, 'status': status, 'hosts': list(hs or [])}
            for h in hs or []:
                hosts[h.lower()] = slug
        _cache.update(at=now, tenants=tenants, hosts=hosts)
        _dev_cache.clear()


def invalidate():
    _cache['at'] = 0.0
    _dev_cache.clear()


def all_active():
    _load()
    return sorted(s for s, t in _cache['tenants'].items() if t['status'] == 'active')


def get(slug):
    _load()
    return _cache['tenants'].get(slug)


def resolve_host(host):
    """الدومين ← الشركة (فعّالة بس)، أو None.

    دومين متسجّل للشركة بالظبط، أو `<slug>.<HR_TENANT_BASE_DOMAIN>` (مثلًا nour.onz.one)."""
    h = str(host or '').strip().lower().split(':')[0].rstrip('.')
    if not h:
        return None
    _load()
    slug = _cache['hosts'].get(h)
    if not slug:
        base = os.environ.get('HR_TENANT_BASE_DOMAIN', '').strip().lower().strip('.')
        if base and h.endswith('.' + base):
            cand = h[: -len(base) - 1]
            if '.' not in cand:
                slug = cand
    t = _cache['tenants'].get(slug) if slug else None
    return slug if t and t['status'] == 'active' else None


def resolve_device(sn):
    """جهاز ADMS ← الشركة: من device_routes (التسجيل الصريح). جهاز مش متسجّل = None (مابيتقبلش)."""
    sn = str(sn or '').strip()
    if not sn:
        return None
    _load()
    if sn in _dev_cache:
        return _dev_cache[sn]
    try:
        with _pg() as c:
            r = c.execute(f'SELECT slug FROM {PLATFORM}.device_routes WHERE sn = %s', (sn,)).fetchone()
    except Exception:
        r = None
    slug = r[0] if r else None
    t = _cache['tenants'].get(slug) if slug else None
    slug = slug if t and t['status'] == 'active' else None
    _dev_cache[sn] = slug
    return slug


def route_device(sn, slug):
    """يسجّل جهاز لشركة. جهاز متسجّل لشركة تانية: خطأ (مايتنقلش في صمت)."""
    ensure_platform()
    with _pg() as c:
        r = c.execute(f'SELECT slug FROM {PLATFORM}.device_routes WHERE sn = %s', (sn,)).fetchone()
        if r and r[0] != slug:
            raise ValueError(f'الجهاز {sn} متسجّل لشركة تانية ({r[0]})')
        c.execute(f'INSERT INTO {PLATFORM}.device_routes (sn, slug) VALUES (%s, %s) ON CONFLICT (sn) DO NOTHING',
                  (sn, slug))
    _dev_cache.pop(sn, None)


def create(slug, name='', hosts=()):
    """شركة جديدة: سجل، و schema بكل الجداول (init_db)، وفولدر ملفات."""
    if not _SLUG.match(slug or ''):
        raise ValueError('اسم الشركة: حروف إنجليزي صغيرة وأرقام و _ (يبدأ بحرف)')
    ensure_platform()
    with _pg() as c:
        c.execute(f'INSERT INTO {PLATFORM}.tenants (slug, name, hosts) VALUES (%s, %s, %s)',
                  (slug, name or slug, [h.lower() for h in hosts]))
    invalidate()
    os.makedirs(data_dir(slug), exist_ok=True)
    with use(slug):
        from utils.db import init_db
        init_db()
    return get(slug)


def set_status(slug, status):
    with _pg() as c:
        c.execute(f'UPDATE {PLATFORM}.tenants SET status = %s WHERE slug = %s', (status, slug))
    invalidate()


def add_host(slug, host):
    with _pg() as c:
        c.execute(f'UPDATE {PLATFORM}.tenants SET hosts = array_append(hosts, %s) '
                  f'WHERE slug = %s AND NOT (%s = ANY(hosts))', (host.lower(), slug, host.lower()))
    invalidate()


# ============================================================ الملفات

def base_data_dir():
    from utils.db import DATA_DIR
    return DATA_DIR


def data_dir(slug=None):
    """فولدر ملفات الشركة (المرفقات، العقود، اللوجات). في وضع الشركة الواحدة: DATA_DIR زي ما هو."""
    if not enabled():
        return base_data_dir()
    slug = slug or require()
    d = os.path.join(base_data_dir(), 'tenants', slug)
    os.makedirs(d, exist_ok=True)
    return d


def upload_folder():
    d = os.path.join(data_dir(), 'uploads')
    os.makedirs(d, exist_ok=True)
    return d


# ============================================================ الشغل اللي في الخلفية

def for_each(fn, *a, **k):
    """`fn` لكل شركة فعّالة (جوه `use`) — أو مرة واحدة في وضع الشركة الواحدة. خطأ شركة مايوقفش الباقي.
    يرجّع {slug: النتيجة أو الخطأ}."""
    if not enabled():
        return {'': fn(*a, **k)}
    out = {}
    for slug in all_active():
        try:
            with use(slug):
                out[slug] = fn(*a, **k)
        except Exception as e:                     # شركة واقعة ماتوقّفش الشركات التانية
            out[slug] = e
    return out


def bind(fn):
    """`threading.Thread(target=bind(fn))`: الخيط الجديد مابيورثش الشركة من الطلب (contextvars) —
    `bind` بيمسكها وقت الإنشاء وبيشغّل `fn` جواها. في وضع الشركة الواحدة: `fn` زي ما هي."""
    slug = _current.get()
    if not slug:
        return fn

    def run(*a, **k):
        with use(slug):
            return fn(*a, **k)
    run.__name__ = getattr(fn, '__name__', 'run')
    return run
