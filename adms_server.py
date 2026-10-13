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

if __name__ == '__main__':
    # Initialize DB (Validation)
    init_db()
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
