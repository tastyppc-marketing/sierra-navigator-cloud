# tests/sierra_core/test_session.py
from sierra_core.session import SessionBroker, Session


class FakeClock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


def make_login(counter):
    def login_fn():
        counter["n"] += 1
        return Session(
            cookies={"k": f"v{counter['n']}"},
            site_id=4989,
            base_url="https://client7.sierrainteractivedev.com",
        )
    return login_fn


def test_first_get_logs_in_once():
    c = {"n": 0}
    b = SessionBroker(login_fn=make_login(c), clock=FakeClock(), ttl_seconds=1800)
    s = b.get_session()
    assert s.site_id == 4989 and c["n"] == 1


def test_second_get_uses_cache():
    c = {"n": 0}
    b = SessionBroker(login_fn=make_login(c), clock=FakeClock(), ttl_seconds=1800)
    b.get_session(); b.get_session()
    assert c["n"] == 1  # cached, no re-login


def test_ttl_expiry_triggers_relogin():
    c = {"n": 0}; clk = FakeClock()
    b = SessionBroker(login_fn=make_login(c), clock=clk, ttl_seconds=1800)
    b.get_session(); clk.advance(1801); b.get_session()
    assert c["n"] == 2


def test_invalidate_forces_relogin():
    c = {"n": 0}
    b = SessionBroker(login_fn=make_login(c), clock=FakeClock(), ttl_seconds=1800)
    b.get_session(); b.invalidate(); b.get_session()
    assert c["n"] == 2


def test_session_carries_base_url():
    c = {"n": 0}
    b = SessionBroker(login_fn=make_login(c), clock=FakeClock(), ttl_seconds=1800)
    s = b.get_session()
    assert s.base_url == "https://client7.sierrainteractivedev.com"
