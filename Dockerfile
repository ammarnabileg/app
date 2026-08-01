# ============================================================
#  صورة نظام الموارد البشرية — تُستخدم للخدمات الثلاث جميعًا
#  (الويب، وكيل المزامنة، خادم البصمة) بأمر تشغيل مختلف لكلٍّ.
# ============================================================

# ---------- المرحلة ١: البناء ----------
# أدوات الترجمة تلزم لبناء بعض الحزم (cryptography، pandas) ولا تلزم
# للتشغيل. فصلها يُخرجها من الصورة النهائية بدل أن تُشحن إلى كل عميل.
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# تُبنى العجلات هنا وتُنسخ جاهزةً إلى المرحلة التالية
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# ---------- المرحلة ٢: التشغيل ----------
FROM python:3.11-slim

# tzdata: المنطقة الزمنية تُضبط عبر TZ، وبدون هذه الحزمة يتجاهلها النظام
#         ويعمل بتوقيت UTC — وساعة النظام مصدر طوابع الحضور، فانزياحها
#         يزيح الأجر مباشرةً.
# curl:   لفحص الصحة أدناه.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY . .

ENV FLASK_APP=app.py \
    FLASK_ENV=production \
    HR_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# نقطة ربط التخزين. هنا تُكتب قاعدة البيانات ومعرّف التركيب
# (.instance_id) الذي تُربط به الرخصة — فبقاء هذا التخزين هو بقاء
# البيانات والترخيص معًا.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 5000 8081

# فحص الصحة: يميّز بين حاوية «تعمل» وحاوية «تستجيب». حاوية مرفوعة
# وخدمتها معطّلة تبدو سليمة في `docker ps` بلا هذا الفحص.
# start-period يمنح مهلة لإنشاء قاعدة البيانات عند أول تشغيل.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APP_PORT:-5000}/login" > /dev/null || exit 1

CMD ["python", "app.py"]
