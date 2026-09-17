"""مسح بصمات التواجد — يُشغَّل من cron.

النظام يمسح من تلقاء نفسه حين يفتح أحدهم البوابة (مخنوقًا بعشر
دقائق)، وهذا يكفي مكتبًا فيه من يفتحها. لكنه ليس مضمونًا: مكتبٌ
صغير قد لا يفتحها أحد طوال نافذة التواجد، فلا يُذكَّر أحد.

وهذا السكربت يجعله مضمونًا. يُشغَّل كل عشر دقائق:

    */10 * * * *  cd /path/to/app && python tools/presence_sweep.py

ولا يحتاج مفتاحًا ولا يفتح مسارًا على الشبكة: يعمل على القاعدة
مباشرةً كما يعمل الخادم. فلا سطح هجومٍ جديد.

ويُخرج سطرًا واحدًا: كم تذكيرًا قُيّد. صفرٌ هو الحال الطبيعي —
معناه أن الجميع بصم أو لا أحد في نافذته.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    # `--force` يتخطّى الخانق: للتجربة اليدوية، أو لمن يضبط cron
    # أبطأ من المهلة ولا يريد أن يُردّ.
    force = '--force' in argv

    from utils.db import get_db_connection
    from utils import notifications as notif

    conn = get_db_connection()
    sent = notif.sweep_presence(conn, force=force)
    print(f'تذكيرات بصمة التواجد المُقيَّدة: {sent}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
