#!/usr/bin/env python3
"""
Passphrase-onto-an-existing-identity regression test.

WHAT WAS REPORTED

After a Cloudpath import onto a production property: 341 identities, and 149
passphrases sitting in the DPSK service under names like
"DPSK_User_2Uj85Sj1xH_1" instead of on the residents' identities.

WHY

When validate recognised a resident's identity, create_passphrases sent

    POST /dpskServices/{pool}/passphrases  {"identityId": ..., no username}

`identityId` is readOnly on that request. R1 ignores it, sees a passphrase
with no name, mints an identity to hold it and invents the name. The
resident's own identity is never touched.

Measured on SuperSandbox, 2026-09-30, in a throwaway group with its pool
correctly attached:

    POST {identityId}                 -> new identity "DPSK_User_xxxx_1"
    POST {identityId, username}       -> 409 DPSK-020, username already exists
    POST {username}, identity has no passphrase -> GENERAL-010
    PATCH /identityGroups/{g}/identities/{id} {dpskPassphrase}
                                      -> passphrase on THAT identity, value kept
    same PATCH, value under pool minimum length
                                      -> "success", value replaced by R1

WHAT THIS GUARDS

  1. A resident whose identity exists gets the passphrase written onto that
     identity, and create_passphrase is never called for them.
  2. A new resident still goes through create_passphrase, by username.
  3. Both R1 rejections of a taken name (GENERAL-010 and DPSK-020) recover by
     writing onto the identity found by name -- DPSK-020 used to be reported
     as "skipped, already exists".
  4. Two file entries resolving to ONE identity do not overwrite each other.
  5. A failed write is reported as failed, not counted as created.
  6. set_identity_passphrase reads the write back, and raises when R1 kept a
     different value.

Usage:
    docker compose exec backend python scripts/test_attach_existing_identity.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from r1api.services import identity as identity_module
from r1api.services.identity import IdentityService
from workflow.phases.cloudpath.passphrases import CreatePassphrasesPhase


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


# ==================== the phase ====================


class FakeDpsk:
    def __init__(self, world):
        self.w = world

    async def create_passphrase(self, pool_id, passphrase=None, tenant_id=None,
                                user_name=None, **kw):
        if "identity_id" in kw:
            raise AssertionError("create_passphrase was handed an identity id")
        self.w["created"].append(user_name)
        error = self.w.get("create_error", {}).get(user_name)
        if error:
            raise RuntimeError(error)
        return {"id": f"pp-new-{user_name}", "identityId": f"id-new-{user_name}"}

    async def update_passphrase(self, **kw):
        self.w.setdefault("expirations", []).append(kw.get("passphrase_id"))
        return {}


class FakeIdentity:
    def __init__(self, world):
        self.w = world

    async def get_identities_in_group(self, group_id, tenant_id=None, page=0, size=100):
        if page:
            return {"content": []}
        return {"content": [
            {"id": ident, "name": name} for name, ident in self.w["group"].items()
        ]}

    async def set_identity_passphrase(self, group_id, identity_id, passphrase,
                                      tenant_id=None, vlan=None):
        self.w["attached"].append((group_id, identity_id, passphrase, vlan))
        if identity_id in self.w.get("attach_fails", ()):
            raise Exception("RuckusONE did not keep the passphrase written")
        return {"id": identity_id, "dpskGuid": f"pp-on-{identity_id}"}


class FakeR1:
    def __init__(self, world):
        self.dpsk = FakeDpsk(world)
        self.identity = FakeIdentity(world)


async def run_phase(world, passphrases, options=None):
    world.setdefault("created", [])
    world.setdefault("attached", [])
    world.setdefault("group", {})
    messages = []

    phase = CreatePassphrasesPhase.__new__(CreatePassphrasesPhase)
    phase.r1_client = FakeR1(world)
    phase.tenant_id = "t-1"
    phase.venue_id = "v-1"

    async def emit(msg, level="info", details=None):
        messages.append(msg)

    async def track_resource(kind, data):
        return None

    async def parallel_map(items, fn, **kw):
        results = [await fn(i) for i in items]
        return type("R", (), {"succeeded": results, "failed": []})()

    phase.emit = emit
    phase.track_resource = track_resource
    phase.parallel_map = parallel_map

    out = await phase.execute(CreatePassphrasesPhase.Inputs(
        dpsk_pool_id="pool-1",
        identity_group_id="ig-1",
        passphrases=passphrases,
        options=options or {},
    ))
    return out, " ".join(messages)


async def phase_checks() -> int:
    failures = 0

    # 1 + 2. known identity is written to; new resident is created
    w = {"group": {"4021": "id-4021"}}
    out, text = await run_phase(w, [
        {"name": "4021_gigabit", "guid": "g-1", "passphrase": "pw-one",
         "vlan_id": 40, "existing_identity_id": "id-4021",
         "existing_identity_passphrase_id": "pp-old",
         "needs_description_update": True},
        {"name": "4022_gigabit", "guid": "g-2", "passphrase": "pw-two"},
    ])
    failures += check(
        "an existing identity has the passphrase written onto it",
        w["attached"] == [("ig-1", "id-4021", "pw-one", 40)],
        f"attached={[(a[1], a[3]) for a in w['attached']]}",
    )
    failures += check(
        "and create_passphrase is never called for that resident",
        w["created"] == ["4022_gigabit"], f"created={w['created']}",
    )
    by_name = {r.username: r for r in out.created_passphrases}
    attached = by_name["4021_gigabit"]
    failures += check(
        "the result carries the identity and the passphrase id R1 reports",
        attached.identity_id == "id-4021"
        and attached.passphrase_id == "pp-on-id-4021"
        and attached.needs_description_update is True,
        f"identity={attached.identity_id}, passphrase={attached.passphrase_id}",
    )
    failures += check(
        "the summary says how many were written onto existing identities",
        out.created_count == 2 and out.failed_count == 0
        and "1 of those written onto identities that already existed" in text
        and "1 replacing" in text,
        text[-160:],
    )

    # 3. both rejections of a taken name recover by name
    for code, error in (
        ("GENERAL-010", "GENERAL-010: Invalid Identity: An identity with this "
                        "name already exists in the group."),
        ("DPSK-020", "R1 API error (409): {'code': 'DPSK-020', 'message': "
                     "\"Username '4030' already exists. Please choose a "
                     "different name.\"}"),
    ):
        w = {"group": {"4030": "id-4030"}, "create_error": {"4030": error}}
        out, _ = await run_phase(w, [
            {"name": "4030", "guid": "g-30", "passphrase": "pw-30"},
        ])
        row = out.created_passphrases[0]
        failures += check(
            f"{code} recovers onto the identity found by name",
            w["attached"] == [("ig-1", "id-4030", "pw-30", None)]
            and row.success and not row.skipped and row.identity_id == "id-4030"
            and out.failed_count == 0,
            f"attached={len(w['attached'])}, success={row.success}, "
            f"skipped={row.skipped}",
        )

    # 3b. a taken name with no identity in this group is a failure, not a skip
    w = {"group": {}, "create_error": {"4031": "R1 API error (409): DPSK-020 "
                                               "Username '4031' already exists."}}
    out, _ = await run_phase(w, [
        {"name": "4031", "guid": "g-31", "passphrase": "pw-31"},
    ])
    failures += check(
        "a taken name we cannot find is reported as failed, not skipped",
        out.failed_count == 1 and out.skipped_count == 0 and out.created_count == 0,
        f"failed={out.failed_count} skipped={out.skipped_count} "
        f"created={out.created_count}",
    )

    # 4. two file entries, one identity
    w = {"group": {"4040": "id-4040"}}
    out, _ = await run_phase(w, [
        {"name": "4040_gigabit", "guid": "g-a", "passphrase": "pw-a",
         "existing_identity_id": "id-4040"},
        {"name": "4040_ultrafast", "guid": "g-b", "passphrase": "pw-b",
         "existing_identity_id": "id-4040"},
    ])
    failures += check(
        "a second file entry cannot overwrite the first on the same identity",
        [a[2] for a in w["attached"]] == ["pw-a"]
        and out.created_count == 1 and out.failed_count == 1
        and "one passphrase per identity" in (out.failed_passphrases[0].error or ""),
        f"writes={len(w['attached'])}, created={out.created_count}, "
        f"failed={out.failed_count}",
    )

    # 5. a failed write is a failure
    w = {"group": {"4050": "id-4050"}, "attach_fails": {"id-4050"}}
    out, text = await run_phase(w, [
        {"name": "4050", "guid": "g-50", "passphrase": "short",
         "existing_identity_id": "id-4050"},
    ])
    failures += check(
        "a write R1 did not keep is reported as failed, not created",
        out.created_count == 0 and out.failed_count == 1 and "1 failed" in text,
        f"created={out.created_count} failed={out.failed_count}",
    )

    # expiry renewal reaches the passphrase the write produced
    w = {"group": {"4060": "id-4060"}}
    await run_phase(
        w,
        [{"name": "4060", "guid": "g-60", "passphrase": "pw-60",
          "existing_identity_id": "id-4060"}],
        options={"renew_expired_dpsks": True},
    )
    failures += check(
        "a renewed expiry is set on the passphrase the write produced",
        w.get("expirations") == ["pp-on-id-4060"], f"{w.get('expirations')}",
    )
    return failures


# ==================== the service ====================


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


class FakeHttp:
    """R1 as measured: the PATCH always 'succeeds'; what it kept varies."""

    ec_type = "MSP"

    def __init__(self, kept):
        self.kept = kept          # what the identity reads back with
        self.patches = []
        self.gets = 0

    def patch(self, path, payload=None, override_tenant_id=None):
        self.patches.append((path, payload, override_tenant_id))
        return FakeResponse(202, {"requestId": "req-1"})

    def get(self, path, params=None, override_tenant_id=None):
        self.gets += 1
        return FakeResponse(200, self.kept)

    def safe_json(self, response):
        return response.json()

    async def await_task_completion(self, **kw):
        return {"status": "SUCCESS"}


async def service_checks() -> int:
    failures = 0

    async def no_sleep(_seconds):
        return None

    real_sleep = identity_module.asyncio.sleep
    identity_module.asyncio.sleep = no_sleep
    try:
        http = FakeHttp({"id": "i-1", "dpskGuid": "pp-1", "dpskPassphrase": "pw-good"})
        svc = IdentityService(http)
        got = await svc.set_identity_passphrase(
            "g-1", "i-1", "pw-good", tenant_id="ec-9", vlan=40,
        )
        path, payload, tenant = http.patches[0]
        failures += check(
            "the passphrase is PATCHed onto the identity, in the EC's tenant",
            path == "/identityGroups/g-1/identities/i-1"
            and payload == {"dpskPassphrase": "pw-good", "vlan": 40}
            and tenant == "ec-9" and got.get("dpskGuid") == "pp-1",
            f"path={path}, keys={sorted(payload)}, tenant={tenant}",
        )

        # R1 substituted a generated value (sent one was under the minimum)
        http = FakeHttp({"id": "i-1", "dpskGuid": "pp-1", "dpskPassphrase": "Xk2m9Qw7Lp4z"})
        try:
            await IdentityService(http).set_identity_passphrase("g-1", "i-1", "short")
            failures += check("a substituted passphrase raises", False, "no raise")
        except Exception as e:
            failures += check(
                "a substituted passphrase raises, and names the likely cause",
                "minimum length" in str(e) and "short" not in str(e).split("shorter")[0]
                and "Xk2m9Qw7Lp4z" not in str(e),
                str(e)[:80],
            )

        # nothing landed at all
        http = FakeHttp({"id": "i-1", "dpskGuid": None, "dpskPassphrase": None})
        try:
            await IdentityService(http).set_identity_passphrase("g-1", "i-1", "pw-good")
            failures += check("an identity left empty raises", False, "no raise")
        except Exception as e:
            failures += check(
                "an identity left with no passphrase raises",
                "still has no passphrase" in str(e), str(e)[:80],
            )
    finally:
        identity_module.asyncio.sleep = real_sleep
    return failures


async def main() -> int:
    print("Passphrase onto an existing identity\n")
    failures = await phase_checks()
    failures += await service_checks()
    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
