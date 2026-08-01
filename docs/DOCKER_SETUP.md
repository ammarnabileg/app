# التشغيل بـ Docker — خطوة بخطوة

## قبل البدء

على الخادم يلزم Docker وملحق compose:

```bash
docker --version            # 20.10 أو أحدث
docker compose version      # v2 — بمسافة لا شرطة
```

> `docker-compose` بشرطة هو الإصدار القديم ولا يدعم
> `depends_on: condition: service_healthy` المستخدم هنا. إن كان لديك القديم
> فحدّثه، أو احذف كتل `depends_on` وابدأ الخدمات يدويًّا بالترتيب.

---

## الخطوة ١: تجهيز المجلد

```bash
mkdir -p /opt/hr && cd /opt/hr
git clone https://github.com/ammarnabileg/app.git .
```

للتحديث لاحقًا: `git pull`.

---

## الخطوة ٢: إعداد العميل

```bash
cp .env.example .env
nano .env
```

```env
COMPANY=alsaqr
WEB_PORT=5001
ADMS_PORT=8091
TZ=Asia/Kuwait
```

| المتغيّر | القاعدة |
|---|---|
| `COMPANY` | حروف لاتينية صغيرة وأرقام وشرطة سفلية. منه تُشتقّ أسماء الحاويات |
| `WEB_PORT` | **فريد لكل عميل** على الخادم |
| `ADMS_PORT` | **فريد لكل عميل** |
| `TZ` | `Asia/Kuwait` — الكويت بلا توقيت صيفي |

**قبل اختيار المنفذ**، تحقّق أنه غير مستخدم:

```bash
ss -tlnp | grep -E '5001|8091'    # لا مخرجات = متاح
```

---

## الخطوة ٣: التشغيل

```bash
docker compose -p alsaqr up -d --build
```

**`-p alsaqr` ليست اختيارية.** هي ما يفصل تخزين هذا العميل عن غيره. بدونها
يأخذ compose اسم المشروع من اسم المجلد، فيتشارك عميلان **التخزين نفسه — أي
قاعدة بيانات واحدة لعميلين**، وهو أخطر خطأ ممكن هنا.

البناء الأول يستغرق دقائق (تنزيل وترجمة الحزم). التاليات أسرع بفضل الذاكرة
المؤقتة.

---

## الخطوة ٤: التحقق

```bash
docker compose -p alsaqr ps
```

المتوقّع:

```
NAME            STATUS
alsaqr_web      Up 2 minutes (healthy)
alsaqr_sync     Up 1 minute
alsaqr_adms     Up 1 minute
```

**`(healthy)` مهمّة:** تعني أن التطبيق يستجيب فعلًا، لا أن الحاوية مرفوعة
وحدها. حاوية تعمل وخدمتها معطّلة تبدو `Up` بلا هذا الفحص.

إن ظهرت `(unhealthy)` أو `(starting)` أطول من دقيقة:

```bash
docker compose -p alsaqr logs hr_web --tail 50
```

ثم افتح `http://<ip-الخادم>:5001`.

---

## الخطوة ٥: التأكّد من عزل البيانات

**هذه الخطوة لا تُتخطّى عند وجود أكثر من عميل.**

```bash
docker volume ls | grep hr_data
```

يجب أن يظهر **صفّ لكل عميل**:

```
alsaqr_hr_data
almursal_hr_data
```

**صفّ واحد مع عميلين يعني أنهما على قاعدة البيانات نفسها.** أوقف فورًا
وأعد التشغيل بـ`-p` صحيحة.

وللتأكّد القاطع:

```bash
docker exec alsaqr_web   ls -l /app/data/hr_system.db
docker exec almursal_web ls -l /app/data/hr_system.db
```

الحجم والتاريخ يجب أن يختلفا.

---

## الخطوة ٦: ضبط جهاز البصمة

في إعدادات الجهاز (ADMS / Cloud Server):

| الحقل | القيمة |
|---|---|
| Server Address | `<ip-الخادم>` |
| Server Port | **`8091`** — المنفذ الخارجي لهذا العميل، لا 8081 |

ثم راقب وصوله:

```bash
docker compose -p alsaqr logs -f hr_adms
```

عند نجاح الاتصال يظهر `CDATA Received | SN: ...`.

**إن ظهر `ADMS rejected | reason=unregistered`** فالجهاز غير مسجَّل في
النظام — أضِفه من شاشة الأجهزة **بالرقم التسلسلي نفسه** ثم أعد المحاولة.
هذا رفض مقصود: قبول أي رقم تسلسلي يعني قبول بصمات ملفّقة.

**وإن ظهر `reason=over_license_limit`** فقد بلغت حدّ الأجهزة في رخصتك.

---

## الخطوة ٧: عميل ثانٍ على الخادم نفسه

```bash
mkdir -p /opt/hr-clients && cd /opt/hr-clients
cp /opt/hr/.env.example almursal.env
nano almursal.env        # COMPANY=almursal, WEB_PORT=5002, ADMS_PORT=8092

cd /opt/hr
docker compose --env-file /opt/hr-clients/almursal.env -p almursal up -d --build
```

نسخة واحدة من الشيفرة، وملف بيئة لكل عميل.

### جدول العملاء — يُحفظ ويُحدَّث

| العميل | COMPANY | WEB_PORT | ADMS_PORT |
|---|---|---|---|
| | alsaqr | 5001 | 8091 |
| | almursal | 5002 | 8092 |

بلا هذا الجدول ستُعاد تعيين منفذ مستخدَم، فيفشل التشغيل أو — أسوأ — يُنشر
عميل على منفذ عميل آخر.

---

## التحديث إلى نسخة جديدة

```bash
cd /opt/hr
git pull
docker compose -p alsaqr up -d --build
```

**آمن على البيانات:** التخزين لا يُمسّ بإعادة البناء، وترحيل المخطط يعمل
تلقائيًّا عند الإقلاع.

**وآمن على الترخيص:** معرّف التركيب محفوظ داخل التخزين، فلا يتغيّر بإعادة
البناء — وهذا ما أُصلح تحديدًا، إذ كان يُشتقّ من عنوان MAC الذي تولّده
Docker عشوائيًّا عند كل بناء فتُرفض رخصة عميل عامل.

قبل التحديث على خادم إنتاج:

```bash
docker run --rm -v alsaqr_hr_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/alsaqr_$(date +%F).tar.gz -C /data .
```

---

## 🔴 أمران يفقدان البيانات

**١)** `docker compose -p alsaqr down -v`

الراية `-v` **تحذف التخزين**: قاعدة البيانات والترخيص معًا. استخدم `down`
مجرّدة.

**٢)** `docker volume rm alsaqr_hr_data`

حذف مباشر ونهائي.

---

## أوامر يومية

```bash
docker compose -p alsaqr ps                    # الحالة
docker compose -p alsaqr logs -f hr_web        # سجلّ حيّ
docker compose -p alsaqr logs hr_adms --tail 100
docker compose -p alsaqr restart hr_web
docker compose -p alsaqr down                  # إيقاف (البيانات باقية)
docker compose -p alsaqr up -d                 # تشغيل
docker stats --no-stream                       # استهلاك الموارد
```

---

## Portainer

**Stacks → Add stack → Repository**

| الحقل | القيمة |
|---|---|
| Name | `alsaqr` — **هذا هو اسم المشروع، فاجعله مطابقًا لـ`COMPANY`** |
| Repository URL | رابط المستودع |
| Compose path | `docker-compose.yml` |

وفي **Environment variables**:

| Name | Value |
|---|---|
| `COMPANY` | `alsaqr` |
| `WEB_PORT` | `5001` |
| `ADMS_PORT` | `8091` |
| `TZ` | `Asia/Kuwait` |

Portainer يمنع تكرار اسم الـstack، فالعزل مضمون تلقائيًّا — بشرط أن يطابق
الاسمُ `COMPANY`.

---

## حلّ المشكلات

| العَرَض | السبب الأرجح | الإجراء |
|---|---|---|
| `port is already allocated` | المنفذ مستخدم | غيّر `WEB_PORT` أو `ADMS_PORT`، وراجع الجدول |
| `(unhealthy)` | التطبيق لا يستجيب | `logs hr_web --tail 50` |
| الجهاز لا يرسل | منفذ خاطئ أو جدار حماية | تأكّد من المنفذ الخارجي، وافتحه في الجدار |
| `reason=unregistered` | الجهاز غير مسجَّل | أضِفه بالرقم التسلسلي نفسه |
| `reason=over_license_limit` | بلغت حدّ الرخصة | زد الحدّ في الرخصة |
| عميلان يريان البيانات نفسها | `-p` مفقودة | أوقف، وأعد التشغيل بـ`-p` صحيحة |
| توقيت البصمات مزاح | `TZ` خاطئة | `docker exec alsaqr_web date` |

---

## ما تغيّر في هذا الإعداد ولماذا

**بناء على مرحلتين** — أدوات الترجمة (`build-essential`، `gcc`) تلزم لبناء
بعض الحزم ولا تلزم للتشغيل. فصلها يُخرجها من الصورة النهائية بدل شحنها إلى
كل عميل.

**`tzdata`** — بدونها يتجاهل النظام متغيّر `TZ` ويعمل بتوقيت UTC. وساعة
النظام مصدر طوابع الحضور: انزياحها يزيح الأجر، ويظهر كـ«تأخير جماعي» غامض
في الكشف لا كخطأ تقني.

**فحص الصحة** — يميّز «تعمل» عن «تستجيب».

**`depends_on: service_healthy`** — الويب هو من ينشئ المخطط عند أول تشغيل،
فبدء وكيل المزامنة وخادم البصمة قبله يعني القراءة من قاعدة غير موجودة.

**`PYTHONDONTWRITEBYTECODE`** — يمنع كتابة `__pycache__` داخل الحاوية.
