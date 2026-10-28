"""
auth.py  —  LOGIN: only people with an account can open the app.

Accounts live in the database (members_store.py, config/app.db). Passwords are
never stored as plain text — only a salted PBKDF2 hash.
Each account belongs to one WORKSPACE (company); it only sees that workspace's data.
Each account has a ROLE: "public" (the default), "hr" (HR / Admin: restricted
documents, the Documents and Integrations screens) or "admin" (hr, plus who may
sign in).
People who sign in with Google / Microsoft / Okta get an account the first time
(sso.py), on a domain their workspace has verified.
PASSWORD_LOGIN=off (in .env) turns password sign-in off.

Manage accounts and domains from a terminal:
    python auth.py add <username> [--workspace <slug>] [--role hr]   # asks for the password
    python auth.py role <username or email> <public|hr|admin|groups> [--workspace <slug>]
    python auth.py disable <username or email>        # no access until enabled again
    python auth.py enable <username or email>
    python auth.py remove <username or email>
    python auth.py list
    python auth.py domain add <domain> [--workspace <slug>]          # single sign-on for @domain
    python auth.py domain remove <domain>
    python auth.py domains
    python auth.py joining <auto|request|invite_only> [--workspace <slug>]  # see join_store.py
    python auth.py claim-code <domain> --workspace <slug>  # a one-time code to claim it
`role` also works for someone who hasn't signed in yet: they're added to the
workspace (default: "default") and get the role from their first sign-in.
A role set here overrides the one their provider's groups give them; `groups`
goes back to the groups.
"""

import getpass
import hashlib
import hmac
import os
import secrets
import sys

import lifecycle_store
import members_store
import workspaces

ITERATIONS = 200_000


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


def password_login_on():
    """PASSWORD_LOGIN=off leaves single sign-on as the only way in."""
    return os.getenv("PASSWORD_LOGIN", "on").strip().lower() not in ("off", "false", "0", "no")


def sso_required(member):
    """Must this account sign in with single sign-on? Its workspace says so, and
    it isn't an admin (admins keep their password, in case the provider breaks)."""
    return member["role"] != "admin" and members_store.requires_sso(member["workspace"])


def access_for(username):
    """{"workspace", "role"} for a password account, or None if it's gone, turned
    off, or may no longer use a password. Checked on every request, not only at
    sign-in."""
    member = members_store.check_group_admin(members_store.get(username))
    if (not member or not member["password_hash"] or member["disabled"]
            or not password_login_on() or sso_required(member)
            or not lifecycle_store.signs_in(member["workspace"], member["role"])):
        return None
    return {"workspace": member["workspace"], "role": member["role"]}


def check_password(username, password):
    """The member if the password is right and the account is on, else None.
    Says nothing about whether they may use it (see sso_required)."""
    member = members_store.get(username)
    if (member and not member["disabled"]
            and verify_password(password or "", member["password_hash"] or "")):
        return member
    return None


def authenticate(username, password):
    """Return the user record (with 'username') if the login is right, else None."""
    member = check_password(username, password)
    access = access_for(member["username"]) if member else None
    if access:
        return {"username": member["username"], "name": member["name"], **access}
    return None


# ---- Command line: manage users ----------------------------------------------
def _option(args, flag, default):
    """Pull "--flag value" out of args. Returns (value, remaining args)."""
    if flag not in args:
        return default, args
    i = args.index(flag)
    return (args[i + 1:i + 2] or [""])[0].strip().lower(), args[:i] + args[i + 2:]


def _their_workspace(member, workspace, workspace_given):
    """The workspace a change to an existing account applies in: its own. A
    --workspace naming another one is refused: an account is never moved, or
    changed, from a workspace it isn't in."""
    if not member:
        return workspace
    if workspace_given and workspace != member["workspace"]:
        sys.exit(f"{member['username']} belongs to the workspace {member['workspace']}, "
                 f"not {workspace}. Remove it there first to move it.")
    return member["workspace"]


def _audit(workspace, action, target, details=None, target_type="member"):
    """Changes made here go in the workspace's audit log, as the operator's."""
    import audit_store
    audit_store.record(workspace, action, actor="cli", actor_type="operator",
                       target_type=target_type, target_id=target, details=details)


def _cli(args):
    workspace_given = "--workspace" in args
    workspace, args = _option(args, "--workspace", workspaces.DEFAULT)  # add bob --workspace acme
    role, args = _option(args, "--role", None)                          # add bob --role hr
    if not workspaces.valid(workspace):
        sys.exit("Workspace must be lowercase letters, digits or '-', e.g. acme-corp.")
    if role not in (None, *members_store.ROLES):
        sys.exit("Role must be public, hr or admin.")
    if len(args) == 2 and args[0] == "add":
        username = args[1].strip().lower()
        password = getpass.getpass(f"Password for {username}: ")
        if len(password) < 8:
            sys.exit("Password must be at least 8 characters.")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords don't match.")
        existed = members_store.get(username)
        workspace = _their_workspace(existed, workspace, workspace_given)
        members_store.set_password(username, args[1], hash_password(password), workspace, role)
        _audit(workspace, "member.password_set" if existed else "member.add", username,
               {"role": role} if role else None)
        print(f"{'Updated' if existed else 'Added'} {username} (workspace: {workspace}, "
              f"role: {members_store.get(username)['role']}).")
    elif len(args) == 3 and args[0] == "role":
        username, role = args[1].strip().lower(), args[2].strip().lower()
        if role not in (*members_store.ROLES, "groups"):
            sys.exit("Role must be public, hr, admin or groups.")
        workspace = _their_workspace(members_store.get(username), workspace, workspace_given)
        members_store.set_role(username, None if role == "groups" else role, workspace)
        _audit(workspace, "member.role", username, {"role": None if role == "groups" else role})
        member = members_store.get(username)
        print(f"{username} is now {member['role']}"
              f"{' (from their groups)' if role == 'groups' else ''} "
              f"(workspace: {member['workspace']}).")
    elif len(args) == 2 and args[0] in ("disable", "enable"):
        member = members_store.get(args[1])
        if not member or not members_store.set_disabled(args[1], args[0] == "disable",
                                                        member["workspace"]):
            sys.exit(f"No account named {args[1]}.")
        _audit(member["workspace"], f"member.{args[0]}", member["username"])
        print(f"{args[1]} is {args[0]}d.")
    elif len(args) == 2 and args[0] == "remove":
        member = members_store.get(args[1])
        if not member or not members_store.remove(args[1], member["workspace"]):
            sys.exit(f"No account named {args[1]}.")
        _audit(member["workspace"], "member.remove", member["username"])
        print(f"Removed {args[1]}.")
    elif args == ["list"]:
        members = members_store.list_members()
        for m in members:
            source = ("set by hand" if m["override_role"] else
                      "from groups" if m["group_role"] else "default")
            print(f"{m['username']:<32} workspace: {m['workspace']:<16} role: {m['role']:<7} "
                  f"({source}) {'password' if m['has_password'] else 'single sign-on'}"
                  f"{'  DISABLED' if m['disabled'] else ''}")
        if not members:
            print("(no accounts)")
    elif len(args) == 3 and args[:2] == ["domain", "add"]:
        try:
            members_store.add_verified_domain(workspace, args[2])
        except ValueError as e:
            sys.exit(str(e))
        _audit(workspace, "domain.verify", args[2].strip().lower(), target_type="domain")
        print(f"People with an @{args[2].strip().lower()} address can now sign in to "
              f"{workspace} with single sign-on.")
    elif len(args) == 3 and args[:2] == ["domain", "remove"]:
        domain = args[2].strip().lower()
        owners = {d["workspace"] for d in members_store.list_domains() if d["domain"] == domain}
        if not members_store.remove_domain(args[2]):
            sys.exit(f"No workspace has {args[2]}.")
        for owner in sorted(owners):
            _audit(owner, "domain.remove", domain, target_type="domain")
        print(f"Removed {args[2]}. People on it are signed out on their next request.")
    elif len(args) == 2 and args[0] == "joining":
        import join_store
        if args[1] not in join_store.POLICIES:
            sys.exit(f"Joining policy must be one of: {', '.join(join_store.POLICIES)}.")
        join_store.set_workspace_policy(workspace, args[1])
        _audit(workspace, "sign_in.joining", workspace, {"join_policy": args[1]},
               target_type="workspace")
        print(f"Joining policy of {workspace}: {args[1]}.")
    elif len(args) == 2 and args[0] == "claim-code":
        import join_store
        domain = args[1].strip().lower()
        try:
            row, code = join_store.new_claim_code(workspace, domain, "cli")
        except join_store.ClaimRefused as e:
            sys.exit(f"{workspace} can't be claimed now ({e.reason}): it has a verified "
                     "domain or an admin who can sign in, or it's suspended.")
        _audit(workspace, "operator.claim_code", workspace,
               {"domain": domain, "claim_code": row["id"]}, target_type="workspace")
        print(f"Claim code for {workspace} (Google Workspace on {domain}), works once, "
              f"for {join_store.CLAIM_CODE_HOURS} hours:\n{code}\n"
              "They open /claim on the site, enter it and sign in with Google.")
    elif args == ["domains"]:
        domains = members_store.list_domains()
        for d in domains:
            print(f"{d['domain']:<32} workspace: {d['workspace']:<16} "
                  f"{'verified' if d['verified_at'] else 'not verified'}")
        if not domains:
            print("(no domains: nobody can use single sign-on yet)")
    else:
        sys.exit(__doc__.split("Manage accounts and domains from a terminal:")[1])


if __name__ == "__main__":
    _cli(sys.argv[1:])
