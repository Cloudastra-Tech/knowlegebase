"""
Abuse and cost monitoring (platform.md PL-36, kb/services/abuse.py) and the daily
spend caps (PL-02, kb/services/spend.py): the checks on fixed data, alerts raised
once and emailed to the operators (and to the tenant's admins for a spend cap),
shown and acknowledged in the operator console, and a hard cap answered with a
429 and a plain sentence on every question.
"""

import time
import uuid

import pytest

import abuse_store
import db
import mailer
import question_log_store as log_store
from kb.services import abuse, spend
from test_insights import at
from test_operator import client, idp, login, new_workspace, operator_sign_in  # noqa: F401

NOW = at("2026-10-25", 15)
LIMITS = {"spike_factor": 3.0, "spike_min": 20, "user_per_hour": 10, "failed_sign_ins": 5,
          "signups_per_ip": 3}


def ws_():
    return f"ab-{uuid.uuid4().hex[:8]}"


def ask_times(ws, times, user="u1", model="gpt-4o-mini", tokens=(100, 20)):
    for t in times:
        log_store.add(ws, user, "web", "answered", text_mode="metadata", at=t, model=model,
                      tokens_in=tokens[0], tokens_out=tokens[1])


def failed_sign_ins(ws, times):
    conn = db.connect()
    with conn:
        conn.executemany("INSERT INTO sso_events (workspace, connection_id, email, outcome, "
                         "reason, at) VALUES (?, 'c1', 'x@y.example', 'failed', 'bad', ?)",
                         [(ws, t) for t in times])
    conn.close()


def signups(ip, n, when):
    conn = db.connect()
    with conn:
        conn.executemany("INSERT INTO audit_events (workspace, at, actor, actor_type, action, "
                         "target_type, target_id, ip, details, prev_hash, hash) VALUES "
                         "(?, ?, 'f@x.example', 'user', 'workspace.create', 'workspace', ?, ?, "
                         "'{}', '', '')", [(f"su-{uuid.uuid4().hex[:6]}", when, "w", ip)
                                           for _ in range(n)])
    conn.close()


def found(ws, now=NOW):
    return [(kind, subject) for w, kind, _, subject, _ in abuse.checks(now, LIMITS) if w == ws]


# ---- The checks, on fixed data --------------------------------------------------------------
def test_a_spike_against_the_weeks_median():
    ws = ws_()
    for d in range(1, 8):                                     # 5 a day the week before
        ask_times(ws, [NOW - d * 86400] * 5, user=f"p{d}")
    ask_times(ws, [NOW - 3 * 3600 - i for i in range(16)], user="a")
    assert found(ws) == []                                    # 16 > 15, but under the minimum 20
    ask_times(ws, [NOW - 3 * 3600 - i for i in range(4)], user="b")
    assert ("questions_spike", None) in found(ws)             # 20 >= 20 and 20 > 3 x 5
    calm = ws_()
    for d in range(1, 8):                                     # 10 a day: 30 is the bar
        ask_times(calm, [NOW - d * 86400] * 10, user=f"p{d}")
    ask_times(calm, [NOW - 3 * 3600 - i for i in range(25)], user="a")
    assert found(calm) == []


def test_one_member_asking_too_much_in_an_hour():
    ws = ws_()
    ask_times(ws, [NOW - 60 * i for i in range(10)], user="steady")        # exactly the limit
    ask_times(ws, [NOW - 30 * i for i in range(11)], user="burst")
    ask_times(ws, [NOW - 7200 - i for i in range(30)], user="earlier")      # not this hour
    assert [f for f in found(ws) if f[0] == "user_burst"] == [("user_burst", "burst")]


def test_failed_sign_ins_spike_against_the_hourly_average():
    ws = ws_()
    failed_sign_ins(ws, [NOW - 60 * i for i in range(5)])
    assert ("failed_sign_ins", None) in found(ws)
    noisy = ws_()                                              # 2 an hour all week: bar is 6
    failed_sign_ins(noisy, [NOW - 3601 - 1800 * i for i in range(7 * 48)])
    failed_sign_ins(noisy, [NOW - 60 * i for i in range(6)])
    assert ("failed_sign_ins", None) not in found(noisy)


def test_many_workspaces_from_one_address():
    signups("203.0.113.9", 3, NOW - 3600)
    signups("198.51.100.1", 2, NOW - 3600)
    signups("192.0.2.7", 5, NOW - 3 * 86400)                   # not in the last day
    seeded = ("203.0.113.9", "198.51.100.1", "192.0.2.7")      # other tests sign up too
    ips = [s for w, kind, _, s, _ in abuse.checks(NOW, LIMITS)
           if kind == "signups_per_ip" and s in seeded]
    assert ips == ["203.0.113.9"]


# ---- Alerts: raised once, mailed, in the console --------------------------------------------
@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, template, **v: sent.append((to, v)))
    monkeypatch.setenv("OPERATOR_EMAILS", "op@cloudastra.example")
    return sent


def test_an_alert_is_raised_and_mailed_once(mails, monkeypatch):
    monkeypatch.setattr(abuse, "thresholds", lambda: LIMITS)
    ws = new_workspace()
    ask_times(ws, [NOW - 30 * i for i in range(11)], user="burst@x.example")
    new = abuse.monitor(NOW)
    mine = [a for a in new if a["workspace"] == ws]
    assert [(a["kind"], a["subject"]) for a in mine] == [("user_burst", "burst@x.example")]
    about = [(to, v) for to, v in mails if v["title"].endswith(f" in {ws}")]
    assert [to for to, _ in about] == ["op@cloudastra.example"]          # not the tenant's admin
    assert "burst@x.example asked 11 questions" in about[0][1]["body"]
    assert not [a for a in abuse.monitor(NOW + 60) if a["workspace"] == ws]  # once
    # Told to the tenant: its admins, and its webhooks that want abuse.alert.
    from kb.services import webhooks
    events = []
    monkeypatch.setattr(webhooks, "emit", lambda w, event, data: events.append((w, event, data)))
    monkeypatch.setenv("ABUSE_NOTIFY_TENANT_ADMINS", "on")
    assert f"admin@{ws}.example" in abuse.notify(mine[0])
    assert events == [(ws, "abuse.alert", {"alert_id": mine[0]["id"], "kind": "user_burst",
                                           "subject": "burst@x.example",
                                           "details": mine[0]["details"]})]


def test_operators_see_and_acknowledge_alerts(client, idp, mails):  # noqa: F811
    ws = new_workspace()
    alert = abuse_store.raise_alert(ws, "questions_spike", "2026-10-25",
                                    {"questions_today": 90, "median_7_days": 10, "factor": 3})
    member = login(client, f"admin@{ws}.example")
    assert client.get("/api/operator/alerts", headers=member).status_code == 401
    op = operator_sign_in(client)
    shown = client.get("/api/operator/alerts?open=true", headers=op).json()
    mine = next(a for a in shown["alerts"] if a["id"] == alert["id"])
    assert mine["title"] == "Questions spiked" and "90 questions today" in mine["summary"]
    assert shown["thresholds"]["spike_factor"] == 3.0
    overview = {w["slug"]: w for w in client.get("/api/operator/workspaces",
                                                 headers=op).json()["workspaces"]}
    assert overview[ws]["open_alerts"] == 1
    r = client.post(f"/api/operator/alerts/{alert['id']}/acknowledge", headers=op)
    assert r.status_code == 200 and r.json()["acknowledged_by"] == "op@cloudastra.example"
    assert alert["id"] not in [a["id"] for a in client.get(
        "/api/operator/alerts?open=true", headers=op).json()["alerts"]]
    assert client.post("/api/operator/alerts/999999/acknowledge", headers=op).status_code == 404


def test_usage_is_rolled_up_per_workspace_and_day():
    ws = ws_()
    ask_times(ws, [NOW - 60, NOW - 120], user="a", tokens=(1000, 500))
    ask_times(ws, [NOW - 180], user="b", model="gpt-4o", tokens=(1000, 0))
    log_store.add(ws, "a", "web", "answered", text_mode="metadata", at=NOW - 240,
                  cache_hit="exact")
    ask_times(ws, [NOW - 86400], user="a")
    abuse.rollup_usage(NOW)
    days = abuse_store.usage(ws, "2026-10-24", "2026-10-25")
    today = days["2026-10-25"]
    assert (today["questions"], today["users"], today["cached"], today["tokens_in"],
            today["tokens_out"]) == (4, 2, 1, 3000, 1000)
    # 2 x (1000 x 0.15 + 500 x 0.60) / 1e6 for gpt-4o-mini + 1000 x 2.50 / 1e6 for gpt-4o
    assert today["cost_usd"] == pytest.approx(0.0009 + 0.0025)
    assert days["2026-10-24"]["questions"] == 1


# ---- Spend caps -----------------------------------------------------------------------------
def test_spend_caps_warn_then_refuse_with_a_429(client, idp, mails):  # noqa: F811
    ws = new_workspace()
    staff, admin = login(client, f"staff@{ws}.example"), login(client, f"admin@{ws}.example")
    op = operator_sign_in(client)
    assert client.put(f"/api/operator/workspaces/{ws}/spend-caps", headers=op,
                      json={"soft_usd": 2, "hard_usd": 1}).status_code == 400
    assert client.put(f"/api/operator/workspaces/{ws}/spend-caps", headers=admin,
                      json={"soft_usd": 1}).status_code == 401
    r = client.put(f"/api/operator/workspaces/{ws}/spend-caps", headers=op,
                   json={"soft_usd": 0.5, "hard_usd": 1.0})
    assert r.status_code == 200 and r.json()["hard_usd"] == 1.0

    # $0.60 today: past the soft cap. Questions go on; admins and operators are told once.
    ask_times(ws, [time.time()], tokens=(4_000_000, 0))
    spend.enforce(ws)
    spend.enforce(ws)
    deadline = time.time() + 10                            # the email goes in the background
    while len(mails) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert sorted(to for to, _ in mails) == ["admin@" + ws + ".example", "op@cloudastra.example"]
    assert "the limit is $0.50" in mails[0][1]["body"]

    # $1.20: past the hard cap. Every question is refused until midnight UTC, in words.
    ask_times(ws, [time.time()], tokens=(4_000_000, 0))
    for path, body in (("/api/chat", {"question": "Refund policy?"}),
                       ("/api/chat/stream", {"question": "Refund policy?"})):
        r = client.post(path, headers=staff, json=body)
        assert r.status_code == 429, r.text
        assert r.json()["detail"].startswith("Your workspace has used today's budget")
        assert "$1.20 of $1.00" in r.json()["detail"]
        assert 0 < int(r.headers["Retry-After"]) <= 86400
    kinds = {a["kind"] for a in abuse_store.alerts(workspace=ws)}
    assert kinds == {"spend_soft", "spend_hard"}
    # Another workspace isn't held back.
    assert spend.status(new_workspace())["over_hard"] is False


def test_server_wide_caps_apply_when_a_workspace_has_none(monkeypatch):
    ws = ws_()
    monkeypatch.setenv("SPEND_HARD_USD_PER_DAY", "0.1")
    assert spend.caps(ws) == (None, 0.1)
    ask_times(ws, [time.time()], tokens=(1_000_000, 0))      # $0.15
    with pytest.raises(Exception) as refused:
        spend.enforce(ws)
    assert getattr(refused.value, "status_code", None) == 429
    monkeypatch.setenv("SPEND_HARD_USD_PER_DAY", "")
    assert spend.caps(ws) == (None, None)
    spend.enforce(ws)                                         # no cap: nothing to check
