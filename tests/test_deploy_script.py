"""
scripts/deploy.sh, run for real against a throwaway git repo and fake `docker` and
`curl` commands (never Docker, never a server): a deploy builds, starts and
records the new version; one that isn't ready goes back to the previous image and,
if its migrations ran, puts the database back; a dry run changes nothing.

The fakes keep their state in a JSON file: the images there are, the one running,
and every docker command run. FAKE_UNHEALTHY lists image tags that never pass
/api/health/ready; FAKE_MIGRATES those that move the database to revision 9999
when they start.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = "cloudastra-knowledgebase"

FAKE_DOCKER = r'''
import json, os, sqlite3, sys
state_file = os.environ["FAKE_STATE"]
with open(state_file) as f:
    st = json.load(f)
args = sys.argv[1:]
st["log"].append({"args": args, "IMAGE_TAG": os.environ.get("IMAGE_TAG"),
                  "GIT_SHA": os.environ.get("GIT_SHA")})
code, out = 0, ""
img = "cloudastra-knowledgebase:"
tag = os.environ.get("IMAGE_TAG")
if args[:2] == ["image", "inspect"]:
    code = 0 if args[2] in st["images"] else 1
elif args[:2] == ["image", "ls"]:
    out = "\n".join(r.split(":", 1)[1] for r in reversed(list(st["images"]))
                    if r.startswith(args[2] + ":"))
elif args[0] == "inspect":
    out = st["images"].get(img + st["running"], "") if st["running"] else ""
elif args[0] == "tag":
    src = args[1]
    st["images"][args[2]] = st["images"].get(src, src)
elif args[0] == "pull":
    st["images"][args[1]] = "sha256:pulled-" + args[1]
elif args[0] == "rmi":
    st["images"].pop(args[1], None)
elif args[:2] == ["volume", "inspect"]:
    code = 1
elif args[0] == "compose":
    sub = args[1]
    if sub == "ps":
        out = "cid-1" if st["running"] else ""
    elif sub == "build":
        if tag in os.environ.get("FAKE_BUILD_FAILS", "").split(","):
            code = 1
        else:
            st["images"][img + tag] = "sha256:built-" + tag
    elif sub == "up":
        if img + tag not in st["images"]:
            code = 1
        else:
            st["running"] = tag
            if tag in os.environ.get("FAKE_MIGRATES", "").split(","):
                conn = sqlite3.connect(os.path.join(os.environ["APP_DIR"], "config", "app.db"))
                conn.execute("UPDATE alembic_version SET version_num = '9999'")
                conn.commit()
                conn.close()
    elif sub == "stop":
        st["running"] = None
else:
    code = 1
with open(state_file, "w") as f:
    json.dump(st, f)
if out:
    print(out)
sys.exit(code)
'''

FAKE_CURL = r'''
import json, os, sys
with open(os.environ["FAKE_STATE"]) as f:
    st = json.load(f)
running = st["running"]
if not running or running in os.environ.get("FAKE_UNHEALTHY", "").split(","):
    if "-f" in " ".join(sys.argv[1:]):
        sys.exit(22)
    print('{"ok": false}')
    sys.exit(0)
if "-o" not in sys.argv:
    print('{"ok": true}')
'''


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def host(tmp_path):
    """An app folder like the server's: a clone of an origin with two commits, the
    real scripts, a database at revision 0010, an old image running."""
    if not shutil.which("flock"):
        pytest.skip("needs flock (util-linux)")
    origin, app = tmp_path / "origin.git", tmp_path / "app"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(app)], check=True,
                   capture_output=True)
    for name, value in (("user.name", "t"), ("user.email", "t@example.com")):
        git(app, "config", name, value)
    (app / "scripts").mkdir()
    for f in ("deploy.sh", "backup.sh", "sqlite_tool.py"):
        shutil.copy2(os.path.join(ROOT, "scripts", f), app / "scripts" / f)
    shutil.copy2(os.path.join(ROOT, "docker-compose.yml"), app / "docker-compose.yml")
    (app / ".gitignore").write_text("config/\n.deploy/\n")
    (app / "docs").mkdir()
    (app / "docs" / "a.txt").write_text("hello\n")
    (app / "VERSION").write_text("1\n")
    git(app, "add", "-A")
    git(app, "commit", "-q", "-m", "one")
    old = git(app, "rev-parse", "HEAD")
    (app / "VERSION").write_text("2\n")
    git(app, "commit", "-q", "-am", "two")
    git(app, "tag", "v2.0.0")
    new = git(app, "rev-parse", "HEAD")
    git(app, "push", "-q", "origin", "main", "--tags")
    git(app, "checkout", "-q", old)

    (app / "config").mkdir()
    conn = sqlite3.connect(app / "config" / "app.db")
    conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
    conn.execute("INSERT INTO alembic_version VALUES ('0010')")
    conn.execute("CREATE TABLE members (username TEXT)")
    conn.execute("INSERT INTO members VALUES ('ada')")
    conn.commit()
    conn.close()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
    state = tmp_path / "docker.json"
    state.write_text(json.dumps({"images": {f"{IMAGE}:latest": "sha256:old"},
                                 "running": "latest", "log": []}))
    chroma = tmp_path / "chroma"
    chroma.mkdir()
    (chroma / "chroma.sqlite3").write_text("index")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STATE": str(state),
           "APP_DIR": str(app), "HOME": str(tmp_path), "BACKUP_DIR": str(tmp_path / "backups"),
           "KEY_BACKUP_DIR": str(tmp_path / "keys"), "CHROMA_DIR": str(chroma),
           "POLL_SECONDS": "0.05"}
    for name in ("COMPOSE_FILE", "IMAGE_TAG", "BACKUP_ENV_FILE", "FAKE_UNHEALTHY",
                 "FAKE_MIGRATES", "FAKE_BUILD_FAILS"):
        env.pop(name, None)

    class Host:
        pass
    h = Host()
    h.app, h.old, h.new, h.env, h.tmp = app, old, new, env, tmp_path

    def deploy(*args, **extra):
        return subprocess.run(["bash", str(app / "scripts" / "deploy.sh"), *args],
                              env={**env, **extra}, capture_output=True, text=True, timeout=120)

    def docker():
        return json.loads(state.read_text())

    def revision():
        conn = sqlite3.connect(app / "config" / "app.db")
        try:
            return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        finally:
            conn.close()

    h.deploy, h.docker, h.revision = deploy, docker, revision
    h.head = lambda: git(app, "rev-parse", "HEAD")
    h.state = lambda name: (app / ".deploy" / name).read_text().split()
    return h


def mutating(log):
    """The docker commands that would change something on a real host."""
    reads = (["image", "inspect"], ["image", "ls"], ["compose", "ps"], ["volume", "inspect"])
    return [e["args"] for e in log if e["args"][:2] not in reads and e["args"][0] != "inspect"]


def test_a_dry_run_prints_every_step_and_changes_nothing(host):
    r = host.deploy("--dry-run", "main")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for step in ("DRY RUN", "fetch", "backup.sh", "checkout -q --detach " + host.new,
                 "docker compose build", "docker compose up -d --no-build",
                 "/api/health/ready", f"docker tag {IMAGE}:{host.new[:12]} {IMAGE}:latest"):
        assert step in out, step
    assert mutating(host.docker()["log"]) == []
    assert host.head() == host.old
    assert not (host.app / ".deploy" / "current").exists()
    assert not (host.tmp / "backups").exists()


def test_a_deploy_backs_up_builds_the_commit_and_records_it(host):
    r = host.deploy("main")
    assert r.returncode == 0, r.stdout + r.stderr
    tag = host.new[:12]
    assert host.head() == host.new
    docker = host.docker()
    assert docker["running"] == tag
    build = next(e for e in docker["log"] if e["args"][:2] == ["compose", "build"])
    assert build["IMAGE_TAG"] == tag and build["GIT_SHA"] == host.new
    # The image that ran before is kept under its commit, for going back.
    assert docker["images"][f"{IMAGE}:{host.old[:12]}"] == "sha256:old"
    assert docker["images"][f"{IMAGE}:latest"] == f"sha256:built-{tag}"
    assert host.state("current")[:2] == [tag, host.new]
    assert host.state("previous")[:2] == [host.old[:12], host.old]
    assert len(list((host.tmp / "backups").glob("kb-backup-*.tar.gz"))) == 1
    assert (host.app / ".deploy" / "db" / f"{host.old[:12]}.db").exists()
    assert "deployed" in (host.app / ".deploy" / "history").read_text()


def test_a_version_that_isnt_ready_is_replaced_by_the_previous_one(host):
    r = host.deploy("--timeout", "1", "main", FAKE_UNHEALTHY=host.new[:12])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "isn't ready" in r.stderr
    assert host.docker()["running"] == host.old[:12]
    assert host.head() == host.old
    assert host.revision() == "0010"
    assert not (host.app / ".deploy" / "current").exists()
    assert "FAILED" in (host.app / ".deploy" / "history").read_text()


def test_a_failed_version_that_migrated_gets_the_database_back(host):
    tag = host.new[:12]
    r = host.deploy("--timeout", "1", "main", FAKE_UNHEALTHY=tag, FAKE_MIGRATES=tag)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "migrations changed the database" in r.stderr
    assert host.revision() == "0010"
    assert host.docker()["running"] == host.old[:12]


def test_a_failed_build_leaves_the_old_version_running(host):
    r = host.deploy("main", FAKE_BUILD_FAILS=host.new[:12])
    assert r.returncode == 1
    assert "the build failed" in r.stderr
    assert host.docker()["running"] == "latest"           # never stopped
    assert host.head() == host.old
    assert not any(e["args"][:2] in (["compose", "up"], ["compose", "stop"])
                   for e in host.docker()["log"])


def test_local_changes_in_the_way_stop_it_before_anything_changes(host):
    (host.app / "VERSION").write_text("hand edit\n")
    r = host.deploy("main")
    assert r.returncode == 1
    assert "couldn't check out" in r.stderr
    assert host.docker()["running"] == "latest"
    assert (host.app / "VERSION").read_text() == "hand edit\n"


def test_rollback_after_a_migration_needs_restore_db(host):
    tag = host.new[:12]
    assert host.deploy("main", FAKE_MIGRATES=tag).returncode == 0
    assert host.revision() == "9999"
    refused = host.deploy("--rollback")
    assert refused.returncode == 1 and "--restore-db" in refused.stderr
    assert host.docker()["running"] == tag

    r = host.deploy("--rollback", "--restore-db")
    assert r.returncode == 0, r.stdout + r.stderr
    assert host.revision() == "0010"
    assert host.docker()["running"] == host.old[:12]
    assert host.head() == host.old
    assert host.state("current")[0] == host.old[:12]
    assert host.state("previous")[0] == tag


def test_an_image_from_the_registry_is_pulled_and_its_tag_checked_out(host):
    r = host.deploy("--image", "2.0.0")
    assert r.returncode == 0, r.stdout + r.stderr
    docker = host.docker()
    assert ["pull", "ghcr.io/cloudastra-tech/knowlegebase:2.0.0"] in [e["args"] for e in docker["log"]]
    assert not any(e["args"][:2] == ["compose", "build"] for e in docker["log"])
    assert docker["running"] == "2.0.0"
    assert host.head() == host.new                        # v2.0.0's commit
    assert "no toxicity validator" in r.stderr


def test_usage_errors(host):
    assert host.deploy().returncode == 2
    assert host.deploy("--image", "1.0", "main").returncode == 2
    assert host.deploy("--restore-db", "main").returncode == 2
    assert host.deploy("--rollback").returncode == 1        # nothing to go back to
