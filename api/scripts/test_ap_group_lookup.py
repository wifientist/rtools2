#!/usr/bin/env python3
"""
AP Group lookup regression test.

Both the Cloudpath import and AP Regroup created NEW AP Groups on a re-run
when the right group already existed. Two independent causes, both here:

1. find_ap_group_by_name trusted R1 to filter.
   It sent filters:{name:[...]} and took data[0]. If that filter is loose or
   ignored -- and this API does that: searchString on /identityGroups/query
   is silently ignored entirely -- data[0] is just the alphabetically first
   group in the venue. The caller compared it, found it different, and
   created a duplicate. That is the "Found X but need exact Y - creating
   new" path.

2. The venue's groups were listed unpaged.
   query_ap_groups() sends whatever page R1 defaults to unless a caller
   passes one, and almost none did. A venue with more groups than that
   default returned a partial list, so later groups looked absent.

Matching now happens client-side against a fully paged list, so neither
server-side filter semantics nor page size can cause a create.

WHAT THIS GUARDS

  1. An existing group is found even when R1 ignores the name filter.
  2. It is found even when it sits beyond the first page.
  3. A genuinely absent name returns None (so it CAN still be created).
  4. A name differing only by case/whitespace is REUSED, not duplicated.
  5. The paged lister returns every group and stops cleanly.
  6. A blank name never matches anything.

Usage:
    docker compose exec backend python scripts/test_ap_group_lookup.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from r1api.services.venues import VenueService

VENUE = "venue-1"
TENANT = "tenant-1"


class IgnoresFilters:
    """
    R1 as the worst case: the name filter is ignored entirely, results are
    sorted by name ASC, and pages are 100 rows.
    """

    ec_type = "EC"

    def __init__(self, names, page_size=100):
        self.groups = [{"id": f"id-{n}", "name": n, "venueId": VENUE} for n in sorted(names)]
        self.page_size = page_size
        self.pages_requested = []

    def post(self, path, payload=None, **kw):
        page = payload.get("page", 1)
        size = payload.get("pageSize", self.page_size)
        self.pages_requested.append(page)
        start = (page - 1) * size
        rows = self.groups[start:start + size]          # name filter IGNORED

        class R:
            ok = True
            status_code = 200

            def json(self_inner):
                return {"data": rows, "totalCount": len(self.groups)}

        return R()


def service(names, page_size=100):
    svc = VenueService.__new__(VenueService)
    svc.client = IgnoresFilters(names, page_size)
    return svc


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    return 0 if ok else 1


async def main() -> int:
    failures = 0
    print("AP Group lookup\n")

    # 1. found despite the filter being ignored (and not the alphabetical first)
    svc = service(["Almondwood-1A", "Central-225", "Tahitian-9"])
    found = await svc.find_ap_group_by_name(TENANT, VENUE, "Central-225")
    failures += check(
        "an existing group is found even when R1 ignores the name filter",
        found is not None and found["name"] == "Central-225",
        f"got {found}",
    )

    # 2. found beyond the first page
    many = [f"Unit-{i:04d}" for i in range(250)] + ["Central-225"]
    svc = service(many)
    found = await svc.find_ap_group_by_name(TENANT, VENUE, "Central-225")
    failures += check(
        "an existing group beyond page 1 is still found",
        found is not None and found["name"] == "Central-225",
        f"pages={svc.client.pages_requested} got={found and found['name']}",
    )

    # 3. a genuinely new name is still reported absent
    svc = service(["Central-225"])
    failures += check(
        "a genuinely absent name returns None, so it can be created",
        await svc.find_ap_group_by_name(TENANT, VENUE, "Central-999") is None,
    )

    # 4. case/whitespace variant is reused, never duplicated
    svc = service(["central-225"])
    found = await svc.find_ap_group_by_name(TENANT, VENUE, "Central-225")
    failures += check(
        "a case-only variant is reused rather than duplicated",
        found is not None and found["name"] == "central-225",
        f"got {found}",
    )
    svc = service(["Central-225 "])
    found = await svc.find_ap_group_by_name(TENANT, VENUE, "Central-225")
    failures += check(
        "a whitespace-only variant is reused rather than duplicated",
        found is not None, f"got {found}",
    )

    # 5. the pager returns everything
    svc = service([f"G-{i:04d}" for i in range(250)])
    allg = await svc.list_ap_groups_in_venue(TENANT, VENUE)
    failures += check(
        "the paged lister returns every group",
        len(allg) == 250 and svc.client.pages_requested == [1, 2, 3],
        f"count={len(allg)} pages={svc.client.pages_requested}",
    )
    failures += check(
        "and indexes them by name",
        VenueService.index_ap_groups_by_name(allg).get("G-0249") == "id-G-0249",
    )

    # 6. a blank name matches nothing
    svc = service(["Central-225"])
    failures += check(
        "a blank name never matches",
        await svc.find_ap_group_by_name(TENANT, VENUE, "") is None
        and await svc.find_ap_group_by_name(TENANT, VENUE, "   ") is None,
    )

    print(f"\n{'FAILED' if failures else 'All checks passed'}"
          f"{f' ({failures})' if failures else ''}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
