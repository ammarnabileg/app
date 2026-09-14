import sqlite3
import time
from utils.db import get_db_connection

_SETTINGS_CACHE = None
_SETTINGS_CACHE_TIME = 0
_SETTINGS_CACHE_TTL = 60 # 1 minute

def get_system_settings():
    """جلب إعدادات النظام من قاعدة البيانات"""
    global _SETTINGS_CACHE, _SETTINGS_CACHE_TIME
    
    # Check cache
    now = time.time()
    if _SETTINGS_CACHE and (now - _SETTINGS_CACHE_TIME < _SETTINGS_CACHE_TTL):
        return _SETTINGS_CACHE

    try:
        conn = get_db_connection()
        settings = conn.execute('SELECT * FROM system_settings WHERE id = 1').fetchone()
        pass # conn.close() removed to prevent leak in Flask g
        
        if settings:
            res = dict(settings)
            _SETTINGS_CACHE = res
            _SETTINGS_CACHE_TIME = now
            return res
        return {}
    except Exception as e:
        print(f"Error getting system settings: {e}")
        return {}

def get_currency_settings(conn):
    """جلب إعدادات العملة من قاعدة البيانات مع الضبط الافتراضي للكويت"""
    try:
        settings = conn.execute('''
            SELECT currency_symbol, currency_name 
            FROM system_settings 
            LIMIT 1
        ''').fetchone()
        
        if settings:
            return {
                'symbol': settings['currency_symbol'] or 'د.ك',
                'name': settings['currency_name'] or 'دينار كويتي'
            }
        else:
            return {
                'symbol': 'د.ك',
                'name': 'دينار كويتي'
            }
    except:
        return {
            'symbol': 'د.ك',
            'name': 'دينار كويتي'
        }

def get_salary_settings_v2(conn):
    """جلب إعدادات الراتب المحسنة من قاعدة البيانات"""
    try:
        settings = conn.execute('''
            SELECT setting_name, setting_value 
            FROM salary_settings_v2
        ''').fetchall()
        
        settings_dict = {row['setting_name']: row['setting_value'] for row in settings}
        
        # إعدادات افتراضية
        default_settings = {
            'rounding_display_decimals': '2',
            'daily_rate_basis': '26',
            'grace_early_minutes': '5',
            'grace_late_minutes': '10',
            'overtime_round_to_minutes': '15',
            'overtime_cap_monthly_hours': '60',
            'weekday_ot_multiplier': '1.25',
            'weekend_ot_multiplier': '1.5',
            'holiday_ot_multiplier': '2.0',
            'early_arrival_bonus': '2.5',
            'late_departure_bonus': '30.0',
            'leave_earned_per_month': '2.5',
            'leave_migration_cutoff_date': '',
            'late_arrival_policy': 'actual_time',
            'early_departure_policy': 'actual_time',
            'missing_punch_policy': 'invalid',
            'missing_punch_penalty_1': '0',
            'missing_punch_penalty_2': '0.25',
            'missing_punch_penalty_3': '1.0',
            'early_departure_grace_minutes': '5',
            'sick_tier_year_basis': 'calendar',
            'presence_missing_policy': 'warning',
            'presence_penalty_day_fraction': '0.25',
            'presence_penalty_1': '0',
            'presence_penalty_2': '0.25',
            'presence_penalty_3': '0.5',
            'presence_penalty_monthly_cap_days': '5',
            'hourly_perm_max_hours_per_day': '2',
            'hourly_perm_max_days_per_month': '4',
        }
        
        # دمج الإعدادات المحفوظة مع الافتراضية
        for key, default_value in default_settings.items():
            if key not in settings_dict:
                settings_dict[key] = default_value
        
        return settings_dict
        
    except Exception as e:
        print(f"خطأ في جلب إعدادات الراتب: {e}")
        return {}

def get_default_salary_data(days_worked, total_work_days, total_work_hours):
    """إرجاع بيانات راتب افتراضية في حالة الخطأ"""
    return {
        'base_salary': 0,
        'daily_salary': 0,
        'hourly_salary': 0,
        'basic_salary': 0,
        'attendance_allowance': {
            'early_arrival_days': 0,
            'early_arrival_amount': 0,
            'late_departure_days': 0,
            'late_departure_amount': 0,
            'overtime_hours': {'weekday': 0, 'weekend': 0, 'holiday': 0},
            'overtime_amount': 0,
            'total': 0
        },
        'leave_deductions': {
            'total': 0,
            'details': []
        },
        'leave_balance': {
            'opening_balance': 0,
            'earned_days': 0,
            'used_days': 0,
            'closing_balance': 0,
            'is_negative': False
        },
        'net_salary': 0,
        'days_worked': days_worked,
        'weekly_leave_days': 0,
        'total_working_days': days_worked,
        'total_work_days': total_work_days,
        'total_work_hours': total_work_hours,
        'currency': {'symbol': 'د.ك', 'name': 'دينار كويتي'},
        'salary_settings': {}
    }

def get_portal_attendance_settings(conn=None):
    """جلب إعدادات البصمة الذاتية عبر البوابة وتحديد النطاق الجغرافي"""
    if conn is None:
        conn = get_db_connection()
    try:
        rows = conn.execute('''
            SELECT setting_name, setting_value FROM salary_settings_v2
            WHERE setting_name IN (
                'portal_attendance_enabled', 'portal_attendance_geofence_enabled',
                'company_latitude', 'company_longitude', 'geofence_radius_meters',
                'portal_attendance_cooldown_minutes', 'portal_attendance_allowed_ips'
            )
        ''').fetchall()
        d = {r['setting_name']: r['setting_value'] for r in rows}
        
        lat_val = d.get('company_latitude')
        lon_val = d.get('company_longitude')
        return {
            'enabled': d.get('portal_attendance_enabled', '0') == '1',
            'geofence_enabled': d.get('portal_attendance_geofence_enabled', '1') == '1',
            'latitude': float(lat_val) if lat_val and lat_val.strip() else None,
            'longitude': float(lon_val) if lon_val and lon_val.strip() else None,
            'radius': float(d.get('geofence_radius_meters') or 150),
            'cooldown_minutes': int(d.get('portal_attendance_cooldown_minutes') or 5),
            'allowed_ips': (d.get('portal_attendance_allowed_ips') or '').strip()
        }
    except Exception as e:
        print(f"Error reading portal attendance settings: {e}")
        return {
            'enabled': False,
            'geofence_enabled': True,
            'latitude': None,
            'longitude': None,
            'radius': 150,
            'cooldown_minutes': 5,
            'allowed_ips': ''
        }

def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """
    حساب المسافة الجغرافية بالكيلومترات/الأمتار بدقة عالية باستخدام معادلة Haversine
    Returns distance in meters.
    """
    import math
    R = 6371000  # نصف قطر الأرض بالمتر
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c
