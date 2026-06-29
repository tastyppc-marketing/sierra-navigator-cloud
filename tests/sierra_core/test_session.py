# tests/sierra_core/test_session.py
import threading
import time as _time

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


# --------------------------------------------------------------------------- #
# W1-T4 (#10): broker lock — single-flight login, TOCTOU-safe invalidate/get
# --------------------------------------------------------------------------- #

def test_concurrent_cold_start_logs_in_exactly_once():
    c = {"n": 0}

    def slow_login():
        c["n"] += 1                       # runs under the broker lock (single-flight)
        _time.sleep(0.05)                 # hold so the other threads pile on the lock
        return Session(cookies={"k": "v"}, site_id=4989,
                       base_url="https://client7.sierrainteractivedev.com")

    b = SessionBroker(login_fn=slow_login, clock=FakeClock(), ttl_seconds=1800)
    results: list[Session] = []
    threads = [threading.Thread(target=lambda: results.append(b.get_session()))
               for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c["n"] == 1                              # one login, no stampede
    assert len({id(r) for r in results}) == 1       # all share the one session
    assert all(r.site_id == 4989 for r in results)


def test_concurrent_force_refresh_with_same_stale_session_logs_in_exactly_once():
    c = {"n": 0}

    def slow_login():
        c["n"] += 1
        _time.sleep(0.05)
        return Session(cookies={"k": f"v{c['n']}"},
                       site_id=4989,
                       base_url="https://client7.sierrainteractivedev.com")

    b = SessionBroker(login_fn=slow_login, clock=FakeClock(), ttl_seconds=1800)
    stale = b.get_session()
    c["n"] = 0

    results: list[Session] = []
    errors: list[Exception] = []

    def refresh():
        try:
            results.append(b.get_session(force_refresh=True, stale=stale))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=refresh) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert c["n"] == 1
    assert len({id(r) for r in results}) == 1
    assert results[0] is not stale


def test_invalidate_get_race_raises_no_attributeerror():
    c = {"n": 0}
    b = SessionBroker(login_fn=make_login(c), clock=FakeClock(), ttl_seconds=1800)
    b.get_session()  # prime the cache
    errors: list[Exception] = []

    def getter():
        try:
            for _ in range(200):
                b.get_session()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def invalidator():
        for _ in range(200):
            b.invalidate()

    ts = ([threading.Thread(target=getter) for _ in range(4)]
          + [threading.Thread(target=invalidator) for _ in range(2)])
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == [], f"invalidate/get race raised: {errors!r}"
