"""
auth.py  —  LOGIN: only people with an account can open the app.

Users live in config/users.json (gitignored — never commit it):
    {"swastik": {"name": "Swastik", "password": "pbkdf2$...$...$..."}}

Passwords are never stored as plain text — only a salted PBKDF2 hash.
Every account gets the same access; login just keeps strangers out.

Manage users from a terminal:
    python auth.py add <username>     # asks for the password
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


def authenticate(username, password):
    """Return the user record (with 'username') if the login is right, else None."""
    user = load_users().get((username or "").strip().lower())
    if user and verify_password(password or "", user.get("password", "")):
        return {"username": username.strip().lower(), "name": user.get("name", username)}
    return None


# ---- Streamlit login screen --------------------------------------------------
def require_login():
    """Show the login form until someone signs in. Returns the signed-in user."""
    if st.session_state.get("user"):
        return st.session_state.user

    st.title("🔐 Sign in")
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
def _cli(args):
    users = load_users()
    if len(args) == 2 and args[0] == "add":
        username = args[1].strip().lower()
        password = getpass.getpass(f"Password for {username}: ")
        if len(password) < 8:
            sys.exit("Password must be at least 8 characters.")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords don't match.")
        existed = username in users
        users[username] = {"name": users.get(username, {}).get("name", args[1]),
                           "password": hash_password(password)}
        save_users(users)
        print(f"{'Updated' if existed else 'Added'} {username}.")
    elif len(args) == 2 and args[0] == "remove":
        if users.pop(args[1].strip().lower(), None) is None:
            sys.exit(f"No user named {args[1]}.")
        save_users(users)
        print(f"Removed {args[1]}.")
    elif args == ["list"]:
        for name in sorted(users):
            print(name)
        if not users:
            print("(no users)")
    else:
        sys.exit(__doc__.split("Manage users from a terminal:")[1])


if __name__ == "__main__":
    _cli(sys.argv[1:])
