"""
RADIUS Standard: comparison and the reads it depends on.

    docker compose exec backend python scripts/test_radius_standard.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from r1api.services.radius_attributes import RadiusAttributeService
from services.radius_standard import analyze_consistency, compare_tenant, read_attribute_catalogue, read_tenant_groups

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'' if ok else f'  {detail}'}")
    failures += 0 if ok else 1


def attr(name, value, operator="ADD"):
    return {"attributeName": name, "operator": operator, "attributeValue": value, "dataType": "INTEGER"}


DOWN, UP = "WISPr-Bandwidth-Max-Down", "WISPr-Bandwidth-Max-Up"
STANDARD = [
    {"name": "fast", "attributes": [attr(DOWN, "100"), attr(UP, "50")]},
    {"name": "slow", "attributes": [attr(DOWN, "10")]},
    {"name": "gone", "attributes": [attr(DOWN, "1")]},
    {"name": "Cased", "attributes": [attr(DOWN, "1")]},
]


def tenant(name, *attributes, in_use=0):
    return {"id": f"id-{name}", "name": name, "attributeAssignments": list(attributes), "externalAssignmentsCount": in_use}


print("compare_tenant")
out = compare_tenant(STANDARD, [
    tenant("fast", attr(UP, "50"), attr(DOWN, "100"), in_use=2),      # same, other order
    tenant("slow", attr(DOWN, "10"), attr(UP, "99")),                 # an attribute the standard lacks
    tenant("cased", attr(DOWN, "1")),                                 # only the case differs
    tenant("other", attr(DOWN, "5")),
])
by = {g["name"]: g for g in out["groups"]}
check("attribute order does not matter", by["fast"]["status"] == "match")
check("in-use count carried", by["fast"]["in_use"] == 2)
check("an extra attribute is a difference", by["slow"]["status"] == "differs")
check("the extra attribute is shown", any(r["attributeName"] == UP and not r["expected"] for r in by["slow"]["attributes"]))
check("absent group is missing", by["gone"]["status"] == "missing" and by["gone"]["group_id"] is None)
check("case variant is missing, with a hint", by["Cased"]["status"] == "missing" and by["Cased"]["similar_name"] == "cased")
check("groups outside the standard are extras", sorted(x["name"] for x in out["extras"]) == ["cased", "other"])
check("operator is compared",
      compare_tenant(STANDARD[:1], [tenant("fast", attr(DOWN, "100", "ADD_REPLACE"), attr(UP, "50"))])["groups"][0]["status"] == "differs")
check("a duplicated value is a difference",
      compare_tenant(STANDARD[1:2], [tenant("slow", attr(DOWN, "10"), attr(DOWN, "10"))])["groups"][0]["status"] == "differs")


print("analyze_consistency")
FAST, SLOW = attr(DOWN, "100"), attr(DOWN, "10")


def t(tid, *groups):
    return {"id": tid, "name": tid, "groups": list(groups)}


fleet = [
    t("a", tenant("fast", FAST), tenant("tier", SLOW)),
    t("b", tenant("fast", FAST), tenant("tier", SLOW), tenant("stray", SLOW)),
    t("c", tenant("fast", FAST), tenant("tier", FAST)),
    t("d", tenant("fast", SLOW)),
]
out = analyze_consistency(fleet)
rank = {r["id"]: r for r in out["tenants"]}
check("full agreement scores 1", rank["a"]["score"] == 1.0, str(rank["a"]))
check("clean tenant outranks one with a stray group", [r["id"] for r in out["tenants"]][:2] == ["a", "b"])
check("stray group doesn't count against anyone", rank["a"]["lacks"] == [] and rank["b"]["other_groups"] == ["stray"])
check("minority value is a difference", rank["c"]["differs"] == ["tier"] and rank["c"]["score"] == 0.5)
check("absent common group is lacking", rank["d"]["lacks"] == ["tier"] and rank["d"]["differs"] == ["fast"])
check("least consistent ranks last", out["tenants"][-1]["id"] == "d")
variants = {g["name"]: g for g in out["groups"]}["fast"]["variants"]
check("variants name their tenants", variants[0]["majority"] and sorted(variants[0]["tenant_ids"]) == ["a", "b", "c"]
      and variants[1]["tenant_ids"] == ["d"] and not variants[1]["majority"])
check("attribute order doesn't split a variant",
      len({g["name"]: g for g in analyze_consistency([
          t("a", tenant("x", FAST, SLOW)), t("b", tenant("x", SLOW, FAST)),
      ])["groups"]}["x"]["variants"]) == 1)
tied = analyze_consistency([t("a", tenant("x", FAST)), t("b", tenant("x", SLOW))])
check("a tie has no consensus, nor a score", tied["scored_groups"] == 0 and tied["tenants"][0]["score"] is None
      and tied["groups"][0]["tied"] and not tied["groups"][0]["variants"][0]["majority"])
check("one tenant alone has nothing to agree with", analyze_consistency([t("a", tenant("x", FAST))])["scored_groups"] == 0)
check("no tenants is not an error", analyze_consistency([])["tenants"] == [])


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.ok, self.text = body, status, status < 400, str(body)

    def json(self):
        return self._body


class Fake:
    """Pages like R1: 0-indexed request, page+1 echoed back."""
    ec_type = "MSP"

    def __init__(self):
        self.calls = []
        self.status = 200

    def post(self, path, payload=None, override_tenant_id=None):
        self.calls.append((path, payload, override_tenant_id))
        if self.status != 200:
            return Resp({"message": "nope"}, self.status)
        if path == "/radiusAttributeGroups/query":
            size = payload["pageSize"]
            rows = [{"name": f"g{i}"} for i in range(230)][payload["page"] * size:(payload["page"] + 1) * size]
            return Resp({"data": rows, "totalCount": 230, "page": payload["page"] + 1})
        vendor = payload["filters"]["vendorName"]
        assert isinstance(vendor, str), "a list 500s"
        return Resp({"data": [{"name": f"{vendor}-A", "vendorName": vendor, "dataType": "INTEGER"}], "totalCount": 1})

    def get(self, path, override_tenant_id=None):
        self.calls.append((path, None, override_tenant_id))
        return Resp({"supportedVendors": ["WISPr", "Ruckus"]})


print("reads")
fake = Fake()
check("every page is read", len(read_tenant_groups(fake, "ec1")) == 230)
check("reads are scoped to the EC", all(c[2] == "ec1" for c in fake.calls))
check("first page is 0", fake.calls[0][1]["page"] == 0)
fake.status = 403
try:
    read_tenant_groups(fake, "ec1")
    check("an unreadable tenant raises", False, "returned a list")
except Exception as e:
    check("an unreadable tenant raises", "403" in str(e))
fake.status = 200
fake.ec_type = "EC"
fake.calls = []
read_tenant_groups(fake, "ec1")
check("a direct key sends no override", fake.calls[0][2] is None)
check("catalogue asks per vendor", sorted(a["name"] for a in read_attribute_catalogue(fake, None)) == ["Ruckus-A", "WISPr-A"])

print("\nshared group listing")
fake = Fake()
service = RadiusAttributeService(fake)
listed = asyncio.run(service.get_radius_attribute_groups(tenant_id="ec1"))
check("every page is listed, not GET's first 10", len(listed) == 230, str(len(listed)))
check("listing uses /query", all(c[0] == "/radiusAttributeGroups/query" for c in fake.calls))
check("listing is scoped to the EC", all(c[2] == "ec1" for c in fake.calls))
check("listing starts at page 0", fake.calls[0][1]["page"] == 0)
fake.status = 403
try:
    asyncio.run(service.get_radius_attribute_groups(tenant_id="ec1"))
    check("a refused listing raises", False, "returned a list")
except Exception as e:
    check("a refused listing raises", "403" in str(e))
fake.status = 200
fake.ec_type = "EC"
fake.calls = []
asyncio.run(service.get_radius_attribute_groups(tenant_id="ec1"))
check("listing on a direct key sends no override", fake.calls[0][2] is None)

print(f"\n{'FAILED: ' + str(failures) if failures else 'All passed'}")
sys.exit(1 if failures else 0)
