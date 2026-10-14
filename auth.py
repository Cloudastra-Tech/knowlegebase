"""
auth.py  —  LOGIN: only people with an account can open the app.

Users live in config/users.json (gitignored — never commit it):
    {"swastik": {"name": "Swastik", "password": "pbkdf2$...$...$..."}}

Passwords are never stored as plain text — only a salted PBKDF2 hash.
Each account belongs to one WORKSPACE (company); it only sees that workspace's data.
Each account has a ROLE: "public" (the default) or "hr" (HR / Admin: restricted
documents, the Documents and Integrations screens).
People who sign in with Google / Microsoft don't need an account here — see sso.py.
PASSWORD_LOGIN=off (in .env) turns password sign-in off in both the API and Streamlit.

Manage users from a terminal:
    python auth.py add <username> [--workspace <slug>] [--role hr]   # asks for the password
    python auth.py role <username> <public|hr>
    python auth.py remove <username>
    python auth.py list
"""

import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

import streamlit as st

USERS_FILE = os.getenv("USERS_FILE", os.path.join("config", "users.json"))
ITERATIONS = 200_000
MAX_FAILED_ATTEMPTS = 5          # per browser session, then a cool-down
LOCKOUT_SECONDS = 60


# ---- Password hashing --------------------------------------------------------
def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, iterations, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                                    int(iterations))
        return hmac.compare_digest(check.hex(), digest)
    except (ValueError, AttributeError):
        return False


# ---- User store --------------------------------------------------------------
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_users(users):
    os.makedirs(os.path.dirname(USERS_FILE) or ".", exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def password_login_on():
    """PASSWORD_LOGIN=off leaves single sign-on as the only way in."""
    return os.getenv("PASSWORD_LOGIN", "on").strip().lower() not in ("off", "false", "0", "no")


def access_for(username):
    """{"workspace", "role"} for a password account, or None if it's gone or
    password sign-in is off. Checked on every request, not only at sign-in."""
    user = load_users().get(username)
    if not user or not password_login_on():
        return None
    return {"workspace": user.get("workspace", "default"),
            "role": "hr" if user.get("role") == "hr" else "public"}


def authenticate(username, password):
    """Return the user record (with 'username') if the login is right, else None."""
    key = (username or "").strip().lower()
    user = load_users().get(key)
    if user and verify_password(password or "", user.get("password", "")):
        access = access_for(key)
        if access:
            return {"username": key, "name": user.get("name", username), **access}
    return None


# ---- Streamlit login screen --------------------------------------------------
def require_login():
    """Show the login form until someone signs in. Returns the signed-in user."""
    if st.session_state.get("user"):
        return st.session_state.user

    st.title("🔐 Sign in")
    if not password_login_on():
        st.error("Password sign-in is turned off (PASSWORD_LOGIN=off). "
                 "Use the main site to sign in with Google or Microsoft.")
        st.stop()
    if not load_users():
        st.error("No user accounts exist yet. An admin must create one first:\n\n"
                 "`python auth.py add <username>`")
        st.stop()

    locked_until = st.session_state.get("locked_until", 0)
    if time.time() < locked_until:
        st.error(f"Too many failed attempts. Try again in "
                 f"{int(locked_until - time.time())} seconds.")
        st.stop()

    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")

    if submitted:
        user = authenticate(username, password)
        if user:
            st.session_state.user = user
            st.session_state.failed_logins = 0
            st.rerun()
        time.sleep(1)                     # slow down password guessing
        failed = st.session_state.get("failed_logins", 0) + 1
        st.session_state.failed_logins = failed
        if failed >= MAX_FAILED_ATTEMPTS:
            st.session_state.locked_until = time.time() + LOCKOUT_SECONDS
            st.session_state.failed_logins = 0
        st.error("Wrong username or password.")
    st.stop()


def logout():
    for key in ("user", "messages"):
        st.session_state.pop(key, None)
    st.rerun()


# ---- Command line: manage users ----------------------------------------------
def _option(args, flag, default):
    """Pull "--flag value" out of args. Returns (value, remaining args)."""
    if flag not in args:
        return default, args
    i = args.index(flag)
    return (args[i + 1:i + 2] or [""])[0].strip().lower(), args[:i] + args[i + 2:]


def _cli(args):
    users = load_users()
    workspace, args = _option(args, "--workspace", "default")   # add bob --workspace acme
    role, args = _option(args, "--role", None)                   # add bob --role hr
    import workspaces
    if not workspaces.valid(workspace):
        sys.exit("Workspace must be lowercase letters, digits or '-', e.g. acme-corp.")
    if role not in (None, "public", "hr"):
        sys.exit("Role must be public or hr.")
    if len(args) == 2 and args[0] == "add":
        username = args[1].strip().lower()
        password = getpass.getpass(f"Password for {username}: ")
        if len(password) < 8:
            sys.exit("Password must be at least 8 characters.")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords don't match.")
        existed = username in users
        role = role or users.get(username, {}).get("role", "public")   # keep it on a reset
        users[username] = {"name": users.get(username, {}).get("name", args[1]),
                           "password": hash_password(password), "workspace": workspace,
                           "role": role}
        save_users(users)
        print(f"{'Updated' if existed else 'Added'} {username} "
              f"(workspace: {workspace}, role: {role}).")
    elif len(args) == 3 and args[0] == "role":
        username, role = args[1].strip().lower(), args[2].strip().lower()
        if username not in users:
            sys.exit(f"No user named {args[1]}.")
        if role not in ("public", "hr"):
            sys.exit("Role must be public or hr.")
        users[username]["role"] = role
        save_users(users)
        print(f"{username} is now {role}.")
    elif len(args) == 2 and args[0] == "remove":
        if users.pop(args[1].strip().lower(), None) is None:
            sys.exit(f"No user named {args[1]}.")
        save_users(users)
        print(f"Removed {args[1]}.")
    elif args == ["list"]:
        for name in sorted(users):
            print(f"{name:<20} workspace: {users[name].get('workspace', 'default'):<16} "
                  f"role: {users[name].get('role', 'public')}")
        if not users:
            print("(no users)")
    else:
        sys.exit(__doc__.split("Manage users from a terminal:")[1])


if __name__ == "__main__":
    _cli(sys.argv[1:])
