"""مُشغِّل الوكيل المحلّيّ لأجهزة البصمة (النسخة الأونلاين). المنطق في utils/connector_agent.py.

    connector_agent --setup --server https://company.onz.one --key onzc_…
    connector_agent            # يعمل في الخلفية
    connector_agent --once     # دورة واحدة للتجربة
"""
import sys

from utils.connector_agent import main

if __name__ == '__main__':
    sys.exit(main())
