"""مُشغِّل وكيل الرفع السحابي.

المنطق كلُّه في `utils/cloud_sync.py` و`utils/cloud_outbox.py`، وهذا
الملف مدخلُه ليس إلا — كي يبقى الأمر المعتاد
`python cloud_sync_agent.py` عاملًا.

## ما كان هنا قبلًا

نسختان لوكيلٍ واحد ببروتوكولين لا يلتقيان: هذا الملف كان يكلّم
`open.onz.one/public/api/v1/sync` بمفتاحٍ وهميّ مكتوبٍ في الشيفرة
ونقطةِ نهايةٍ لا وجود لها، و`launcher/sync_agent.py` كان يكلّم
`/api/sync` الحقيقيّة. فمن شغّل الأوّل لم يرفع شيئًا ولم يعرف لماذا.

وكلاهما كان يرشّح التغييرات بالطابع الزمني، فيفوتهما التعديلُ
والحذفُ والبصمةُ المتأخّرة — وهو ما يشرحه `utils/cloud_outbox`.
"""
import sys

from utils.cloud_sync import run_forever, run_once

if __name__ == '__main__':
    if '--once' in sys.argv:
        print(run_once())
    else:
        run_forever()
