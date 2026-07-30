from datetime import datetime, date, timedelta
from utils.db import get_db_connection

def is_official_holiday(conn, check_date):
    """
    Check if a given date is an official holiday.
    Returns: (is_holiday, holiday_name)
    """
    date_str = check_date.strftime('%Y-%m-%d')
    res = conn.execute("SELECT name FROM official_holidays WHERE date = ?", (date_str,)).fetchone()
    if res:
        return True, res['name']
    return False, None

def get_cumulative_leave_days(conn, employee_id, leave_type_id, year):
    """
    Get total days used for a specific leave type in a specific year.
    Strictly year-isolated.
    """
    # Sum days from approved requests in this year
    # Note: This simple sum assumes start/end dates are within the year.
    # For robust logic crossing years, strict date intersection is better.
    # But for simplicity, we look at start_date year.
    
    start_of_year = f"{year}-01-01"
    end_of_year = f"{year}-12-31"
    
    res = conn.execute('''
        SELECT SUM(days_count) as total 
        FROM leave_requests 
        WHERE employee_id = ? 
        AND leave_type_id = ? 
        AND status = 'approved'
        AND start_date BETWEEN ? AND ?
    ''', (employee_id, leave_type_id, start_of_year, end_of_year)).fetchone()
    
    return res['total'] if res and res['total'] else 0

def calculate_tiered_leave_pay(conn, leave_type_id, prior_used_days, requesting_days):
    """
    Calculate the total pay percentage for a batch of days based on tiers.
    Example: 
      Tiers: 1-15 (100%), 16-25 (75%)
      Prior Used: 14 days.
      Requesting: 3 days (Day 15, 16, 17).
      Result: 
        Day 15 -> 100%
        Day 16 -> 75%
        Day 17 -> 75%
        Avg Pct = (1 + 0.75 + 0.75) / 3 = 0.833
        
    Returns: Average Payment Percentage for the requested batch (0.0 to 1.0)
    """
    tiers = conn.execute('''
        SELECT start_day, end_day, payment_percentage 
        FROM leave_tiers 
        WHERE leave_type_id = ? 
        ORDER BY start_day
    ''', (leave_type_id,)).fetchall()
    
    if not tiers:
        # If no tiers defined, assume 100% (or check if paid flag is used elsewhere, 
        # but tiers imply specific logic. Default to 1.0 if not found?)
        # Let's check `is_paid` from leave_types if no tiers.
        lt = conn.execute("SELECT is_paid FROM leave_types WHERE id = ?", (leave_type_id,)).fetchone()
        return 1.0 if lt and lt['is_paid'] else 0.0
        
    total_pct_sum = 0.0
    
    # Simulate each day
    for day_offset in range(1, requesting_days + 1):
        current_day_number = prior_used_days + day_offset
        
        # Find applicable tier
        day_pct = 0.0 # Default to 0 if beyond all tiers
        for t in tiers:
            if t['start_day'] <= current_day_number <= t['end_day']:
                day_pct = t['payment_percentage']
                break
        
        # If beyond last tier, it stays 0 (or should it take the last one? 
        # Kuwait law says 0 after 45 days, so 0 is correct default if tiers cover up to 45).
        # However, our seed data covers up to 365.
        
        total_pct_sum += day_pct
        
    return total_pct_sum / requesting_days

def check_annual_leave_eligibility(conn, employee_id, hire_date_str):
    """
    Check if employee is eligible for Annual Leave (6 months service).
    """
    if not hire_date_str:
        return False, "تاريخ التعيين غير مسجل"
        
    hire_date = datetime.strptime(hire_date_str, '%Y-%m-%d').date()
    today = date.today()
    
    # Calculate months of service
    months_service = (today.year - hire_date.year) * 12 + (today.month - hire_date.month)
    
    if months_service < 6:
        return False, f"غير مستحق (مدة الخدمة {months_service} شهر - المطلوب 6 أشهر)"
        
    return True, "مستحق"

def process_holiday_work(conn, employee_id, date_str):
    """
    Credit a compensatory day for working on a holiday.
    """
    conn.execute('''
        INSERT INTO employee_comp_days (employee_id, date_earned, reason, is_used)
        VALUES (?, ?, 'Holiday Work', 0)
    ''', (employee_id, date_str))
