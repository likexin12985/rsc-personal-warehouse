"""SMS configuration binding against a caller-owned disposable PostgreSQL 16.

The caller supplies the migration owner and restricted API engine. No external
provider, credentials, host selection or database cleanup is performed here.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import time
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.formal_services import authentication_challenge as challenge_service
from app.foundation_models import AuditChainHead, LoginChallenge, SmsChallengeDispatch


PROFILE = "f" * 64
PROTECTED = (
    "inventory_transactions", "inventory_movements", "stock_balances",
    "stock_accounts", "stock_locations", "inventory_ledger_heads",
    "daily_reconciliation_cutoffs",
)


def _facts(owner):
    with owner.connect() as connection:
        return {name: hashlib.sha256(json.dumps(connection.scalar(text(
            "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) "
            "FROM public." + name + " t"
        )), sort_keys=True, default=str).encode()).hexdigest() for name in PROTECTED}


def run(owner, api, observer):
    assert owner.dialect.name == api.dialect.name == "postgresql"
    with api.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == "star_oam_api"
        assert 160000 <= int(connection.scalar(text("SHOW server_version_num"))) < 170000
    before = _facts(owner)
    with Session(owner) as db:
        if db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")) is None:
            db.add(AuditChainHead(stream_key="authentication", version=0))
            db.commit()
    cases = []
    seeded = []

    def seed(status="accepted"):
        stamp = datetime.now(timezone.utc)
        identity = uuid4()
        mobile_hash = hashlib.sha256(identity.bytes).hexdigest()
        with Session(owner) as db:
            db.add(LoginChallenge(
                id=identity, mobile_hash=mobile_hash, code_hash=None,
                verification_mode="provider_managed", provider="aliyun_pnvs",
                provider_reference=None, client_type="web", purpose="login",
                attempts=0, max_attempts=5, expires_at=stamp + timedelta(minutes=5),
                status="pending", idempotency_key=f"pg16-sms-profile-{identity}",
                requested_ip_hash="b" * 64, created_at=stamp,
            ))
            db.flush()
            db.add(SmsChallengeDispatch(
                challenge_id=identity, provider="aliyun_pnvs", mobile_hash=mobile_hash,
                status="prepared", request_sha256=hashlib.sha256(
                    f"formal-sms-dispatch-request-v1|{identity}|{PROFILE}".encode()
                ).hexdigest(), created_at=stamp,
            ))
            db.commit()
        with Session(api) as db:
            claim = challenge_service.claim_dispatch(
                db, challenge_id=identity, request_id=f"pg16-claim-{identity}", lease_seconds=60,
            )
            assert claim.acquired
            if status == "accepted":
                challenge_service.mark_sent(
                    db, challenge_id=identity, owner_token=claim.owner_token,
                    provider_reference=f"synthetic-provider-{identity}",
                    request_id=f"pg16-sent-{identity}",
                )
            elif status == "uncertain":
                challenge_service.mark_send_uncertain(
                    db, challenge_id=identity, owner_token=claim.owner_token,
                    request_id=f"pg16-unknown-{identity}",
                )
            db.commit()
        seeded.append(identity)
        return identity, mobile_hash, claim.owner_token

    def state(identity):
        with Session(owner) as db:
            challenge = db.get(LoginChallenge, identity)
            dispatch = db.get(SmsChallengeDispatch, identity)
            return challenge.status, challenge.attempts, dispatch.status, dispatch.request_sha256

    def verify(db, mobile_hash, profile=PROFILE):
        return challenge_service.begin_verify(
            db, mobile_hash=mobile_hash, provider="aliyun_pnvs", client_type="web",
            request_id=f"pg16-verify-{uuid4()}",
            current_dispatch_request_profile_sha256=profile,
        )

    identity, mobile_hash, _ = seed()
    for profile, label in (("a" * 64, "changed"), (None, "missing")):
        expected = state(identity)
        with Session(api) as db:
            try:
                verify(db, mobile_hash, profile)
            except challenge_service.AuthenticationChallengeError as exc:
                assert exc.code == "provider_configuration_changed" and not exc.mutation_persisted
                assert exc.http_status_code == 503
                db.rollback()
            else:
                raise AssertionError("configuration drift authorized a verification attempt")
        assert state(identity) == expected
        cases.append(label + "-verification-profile-refused-without-consuming-attempt")
    with Session(api) as db:
        attempt = verify(db, mobile_hash)
        challenge_service.finish_verify(db, challenge_id=attempt.challenge_id, verified=True,
                                        request_id=f"pg16-restored-{uuid4()}")
        db.commit()
    assert state(identity)[:3] == ("verified", 1, "accepted")
    cases.append("restored-profile-verifies-original-challenge-once")

    identity, mobile_hash, _ = seed("uncertain")
    expected = state(identity)
    with Session(api) as db:
        try:
            verify(db, mobile_hash, "a" * 64)
        except challenge_service.AuthenticationChallengeError as exc:
            assert exc.code == "provider_configuration_changed"
            db.rollback()
        else:
            raise AssertionError("uncertain challenge used another profile")
    assert state(identity) == expected
    with Session(api) as db:
        attempt = verify(db, mobile_hash)
        challenge_service.finish_verify(db, challenge_id=attempt.challenge_id, verified=True,
                                        request_id=f"pg16-uncertain-verify-{uuid4()}")
        db.commit()
    assert state(identity)[:3] == ("verified", 1, "uncertain")
    cases.append("unknown-send-profile-guard-preserves-exact-verification-recovery")

    for profile, label in (("a" * 64, "changed"), (None, "missing")):
        identity, _, token = seed("sending")
        with Session(api) as db:
            assert not challenge_service.authorize_dispatch_provider_call(
                db, challenge_id=identity, owner_token=token,
                request_id=f"pg16-send-drift-{uuid4()}", minimum_remaining_seconds=1,
                current_dispatch_request_profile_sha256=profile,
            )
            db.commit()
        assert state(identity)[:3] == ("pending", 0, "uncertain")
        with Session(api) as db:
            assert not challenge_service.authorize_dispatch_provider_call(
                db, challenge_id=identity, owner_token=token,
                request_id=f"pg16-no-resend-{uuid4()}", minimum_remaining_seconds=1,
                current_dispatch_request_profile_sha256=PROFILE,
            )
            db.commit()
        cases.append(label + "-send-profile-stops-before-provider-and-does-not-replay")

    identity, mobile_hash, _ = seed()
    locked = threading.Event()
    release = threading.Event()
    contender_pid = []

    def first():
        with Session(api) as db:
            attempt = verify(db, mobile_hash)
            locked.set()
            assert release.wait(15), "test did not release verification owner"
            challenge_service.finish_verify(db, challenge_id=attempt.challenge_id, verified=True,
                                            request_id=f"pg16-owner-finish-{uuid4()}")
            db.commit()

    def contender():
        assert locked.wait(10)
        with Session(api) as db:
            contender_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            try:
                verify(db, mobile_hash, "a" * 64)
            except challenge_service.AuthenticationChallengeError as exc:
                db.rollback()
                assert exc.code == "challenge_unavailable"
            else:
                raise AssertionError("concurrent configuration change reused verified challenge")

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner_future = executor.submit(first)
        contender_future = executor.submit(contender)
        try:
            deadline = time.monotonic() + 10
            observed_lock = False
            while time.monotonic() < deadline:
                if contender_pid:
                    # pg_stat_activity hides another role's wait event from
                    # the migrator. Observe using the disposable cluster's
                    # administrator without granting the API extra rights.
                    with observer.connect() as connection:
                        observed_lock = connection.scalar(text(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"
                        ), {"pid": contender_pid[0]}) == "Lock"
                    if observed_lock:
                        break
                time.sleep(.03)
            assert observed_lock, "contender did not encounter the actual PostgreSQL row lock"
        finally:
            release.set()
        owner_future.result(timeout=15)
        contender_future.result(timeout=15)
    assert state(identity)[:3] == ("verified", 1, "accepted")
    cases.append("concurrent-stale-profile-cannot-overtake-current-verification-owner")

    for mode in ("local_hash", "legacy_unknown"):
        identity = uuid4()
        mobile_hash = hashlib.sha256(identity.bytes).hexdigest()
        stamp = datetime.now(timezone.utc)
        with Session(owner) as db:
            db.add(LoginChallenge(
                id=identity, mobile_hash=mobile_hash,
                code_hash="a" * 64 if mode == "local_hash" else None,
                verification_mode=mode, provider="aliyun_pnvs", client_type="web",
                purpose="login", attempts=0, max_attempts=5,
                expires_at=stamp + timedelta(minutes=5), status="pending",
                idempotency_key=f"pg16-sms-legacy-{identity}",
                requested_ip_hash="b" * 64, created_at=stamp,
            ))
            db.commit()
        with Session(api) as db:
            try:
                verify(db, mobile_hash)
            except challenge_service.AuthenticationChallengeError as exc:
                assert exc.code == "challenge_verification_mode_unsupported"
                assert not exc.mutation_persisted and exc.http_status_code == 503
                db.rollback()
            else:
                raise AssertionError("legacy mode obtained PNVS verification authority")
        with Session(owner) as db:
            row = db.get(LoginChallenge, identity)
            assert (row.status, row.attempts, row.verified_at, row.consumed_at) == ("pending", 0, None, None)
            # A historical verified fact also cannot authorize a new session
            # by entering finish/consume directly or replaying that lifecycle.
            row.status = "verified"
            row.verified_at = stamp
            db.commit()
        for operation in ("finish", "consume"):
            with Session(api) as db:
                try:
                    if operation == "finish":
                        challenge_service.finish_verify(db, challenge_id=identity, verified=True,
                                                        request_id=f"pg16-old-finish-{uuid4()}")
                    else:
                        challenge_service.consume_verified(db, challenge_id=identity, actor_user_id=None,
                                                           request_id=f"pg16-old-consume-{uuid4()}")
                except challenge_service.AuthenticationChallengeError as exc:
                    assert exc.code == "challenge_verification_mode_unsupported"
                    db.rollback()
                else:
                    raise AssertionError("historical mode bypassed the PNVS lifecycle")
        with Session(owner) as db:
            row = db.get(LoginChallenge, identity)
            assert (row.status, row.attempts, row.consumed_at) == ("verified", 0, None)
        seeded.append(identity)
        cases.append(mode + "-history-cannot-authorize-provider-verification-or-new-session")
    assert _facts(owner) == before
    return dict(status="passed", cases=cases, challengeCount=len(seeded),
                stockAndCutoffsUnchanged=True, immutableFactsSha256=before,
                actualSmsSent=False, productionStartup=False)
