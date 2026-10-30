from flask import Flask
from routes.adms_routes import adms_bp
from utils.db import init_db, close_db_connection
import os
import sys

app = Flask(__name__)
app.secret_key = os.environ.get('ADMS_SECRET_KEY', os.urandom(24))
app.register_blueprint(adms_bp, url_prefix='/iclock')
# والمسارُ نفسُه بلا /iclock: Coolify يُعلن hr_adms بدومين فيه المسار
# (`http://<الدومين>/iclock`) ويقصّ البادئة افتراضًا قبل أن يُمرّر الطلب —
# فيصل `/cdata` لا `/iclock/cdata`، ويردّ هذا الخادم 404 فيسكت الجهاز.
# بالتسجيلين يعمل الخادم قُصّت البادئة أم لم تُقصّ، في Coolify وفي Apache.
app.register_blueprint(adms_bp, url_prefix='', name='adms_stripped')
# اتصالات القاعدة بتتقفل آخر كل طلب — زي البرنامج الرئيسي (app.py). سيرفر ADMS كان ناسيها،
# فكل طلب من جهاز بيسيب اتصال مفتوح: تحت ضغط أجهزة كتير الملفات المفتوحة بتعدّي الألف في
# ثواني والسيرفر يقع («filedescriptor out of range»).
app.teardown_appcontext(close_db_connection)


@app.before_request
def _resolve_tenant():
    """Multi-tenant: الجهاز بيتعرف من الـ SN (متسجّل لشركة واحدة في hr_platform.device_routes).
    جهاز مش متسجّل = 404 — بصماته مابتدخلش شركة غلط."""
    from utils import tenancy
    if not tenancy.enabled():
        return None
    from flask import request, g, abort
    slug = tenancy.resolve_device(request.args.get('SN') or request.args.get('sn'))
    if not slug:
        abort(404)
    g._tenant_token = tenancy.set_current(slug)
    return None


@app.teardown_request
def _reset_tenant(exc=None):
    from flask import g
    tok = g.pop('_tenant_token', None)
    if tok is not None:
        from utils import tenancy
        tenancy.reset(tok)


if __name__ == '__main__':
    from utils import tenancy as _tenancy
    # Initialize DB (Validation) — لكل شركة في وضع multi-tenant
    _tenancy.for_each(init_db)
    try:
        from utils.oracle_db import start_oracle_heartbeat
        start_oracle_heartbeat()
    except ImportError:
        pass
    
    # Get Port from Env or Default
    port = int(os.environ.get('ADMS_PORT', 8081))
    host = '0.0.0.0'
    
    print(f"🚀 Starting ADMS Server on {host}:{port} using Waitress...")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
