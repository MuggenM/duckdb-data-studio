import os
from superset.security import SupersetSecurityManager
from flask_appbuilder.security.views import AuthDBView, expose
from flask_login import login_user
from flask import redirect, request

# Custom Authentication View to support auto_login parameter
class CustomAuthDBView(AuthDBView):
    @expose("/login/", methods=["GET", "POST"])
    def login(self):
        if request.args.get("auto_login") == "true":
            sm = self.appbuilder.sm
            user = sm.find_user(username="admin")
            if user:
                login_user(user, remember=True)
                # Redirect to the main Superset homepage
                return redirect(self.appbuilder.get_url_for_index)
        return super().login()

# Custom Security Manager to register our custom view
class CustomSecurityManager(SupersetSecurityManager):
    authdbview = CustomAuthDBView

# Configuration variables
CUSTOM_SECURITY_MANAGER = CustomSecurityManager

# Allow iframe embedding globally
HTTP_HEADERS = {
    "X-Frame-Options": "ALLOWALL"
}

# Change cookie name to avoid collision with NiceGUI's "session" cookie
SESSION_COOKIE_NAME = "superset_session"
SESSION_COOKIE_HTTPONLY = True
