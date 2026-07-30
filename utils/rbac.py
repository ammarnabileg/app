
from functools import wraps
from flask import session, flash, redirect, url_for, request, abort
from utils.db import get_db_connection
from flask_babel import gettext

class RBACService:
    @staticmethod
    def get_user_permissions(user_id):
        """
        Fetch all permission codes for a given user based on their assigned roles.
        """
        conn = get_db_connection()
        try:
            query = '''
                SELECT DISTINCT p.code 
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                JOIN user_roles ur ON rp.role_id = ur.role_id
                WHERE ur.user_id = ?
            '''
            rows = conn.execute(query, (user_id,)).fetchall()
            return {row['code'] for row in rows}
        finally:
            pass # conn.close() removed to prevent leak in Flask g

    @staticmethod
    def has_permission(user_id, permission_code):
        """
        Check if a user has a specific permission.
        """
        # Optimization: cache permissions in session if needed, but for now DB check is safer
        perms = RBACService.get_user_permissions(user_id)
        return permission_code in perms

def require_permission(permission_code):
    """
    Decorator to check for a specific permission.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash(gettext('x.f_login_first'), 'error')
                return redirect(url_for('auth.login'))
            
            user_id = session['user_id']
            
            # Validates permission
            if not RBACService.has_permission(user_id, permission_code):
                # If it's an API request, return 403 JSON without flashing
                if request.is_json or request.path.startswith('/api/') or (request.headers.get('X-Requested-With') == 'XMLHttpRequest'):
                    return {"success": False, "message": "Unauthorized access"}, 403
                
                flash(gettext('x.f_no_permission'), 'error')
                return redirect(request.referrer or url_for('main.index'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
