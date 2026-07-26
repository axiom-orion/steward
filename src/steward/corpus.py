"""The catalog as Steward sees it: one flat, typed view of every dataset.

Detectors that only ever look at one entity can miss the most common kind of
metadata debt, because the debt is *relative*: the same logical table lands in
the catalog three or four times — once per platform — and only one copy gets
governed. Owners, domain and classification tags stop at the platform boundary.

So the corpus is fetched whole, then twinned: datasets that share a logical name
AND substantially the same columns are treated as copies of one table. A twin
that already carries a facet is the evidence for propagating it to the ones that
don't. Nothing is invented — every value Steward proposes is a value some copy of
the same table already has.

Twins are usually on different platforms, but not always: a BI tool models one
table as several entities (Looker's view and its explore), and governance drifts
between those the same way.

Parsing is separated from fetching so the whole model tests offline.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable

# Tags DataHub's own ingestion writes: platform statistics and BI column roles.
# They describe one platform's copy, not the table, so they must never propagate.
_ARTIFACT_TAG_NAMES = {
    "dimension", "measure", "columnfield", "sum", "count", "avg", "min", "max",
    "temporal", "measure_name", "measure_values",
}
_ARTIFACT_TAG_PREFIX = "__default_"

# Twin thresholds. Name equality alone is far too loose — a `customers` table
# exists in every schema — so column agreement carries the claim.
MIN_FIELD_OVERLAP = 0.8
MIN_SHARED_FIELDS = 2


@dataclass(frozen=True)
class Owner:
    urn: str
    display: str
    ownership_type_urn: str | None = None
    ownership_type_name: str | None = None


@dataclass(frozen=True)
class Tag:
    urn: str
    name: str
    description: str = ""

    @property
    def leaf(self) -> str:
        """`urn:li:tag:pack123.PII_Data` -> `PII_Data`"""
        tail = self.urn.split(":")[-1]
        return tail.rsplit(".", 1)[-1] if "." in tail else tail

    @property
    def is_governance(self) -> bool:
        leaf = self.leaf
        if leaf.startswith(_ARTIFACT_TAG_PREFIX):
            return False
        return leaf.strip().lower() not in _ARTIFACT_TAG_NAMES


@dataclass(frozen=True)
class Dataset:
    urn: str
    name: str
    platform: str
    description: str = ""
    owners: tuple[Owner, ...] = ()
    tags: tuple[Tag, ...] = ()
    domain_urn: str | None = None
    domain_name: str | None = None
    fields: tuple[str, ...] = ()

    @property
    def governance_tags(self) -> tuple[Tag, ...]:
        return tuple(t for t in self.tags if t.is_governance)

    @property
    def logical_name(self) -> str:
        return logical_name(self.urn)

    @property
    def normalized_fields(self) -> frozenset[str]:
        return frozenset(f.split(".")[-1].strip().lower() for f in self.fields if f.strip())


@dataclass(frozen=True)
class Twin:
    """Another platform's copy of the same logical table, with the strength of the match."""
    dataset: Dataset
    overlap: float
    shared_fields: int


# --------------------------------------------------------------------------
# parsing (pure)
# --------------------------------------------------------------------------

def platform_from_urn(urn: str) -> str:
    """urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD) -> dbt"""
    marker = "dataPlatform:"
    if marker in urn:
        return urn.split(marker, 1)[1].split(",", 1)[0].rstrip(")")
    return "unknown"


def logical_name(urn: str) -> str:
    """The table's own name, minus database, schema and any datapack prefix.

    urn:li:dataset:(urn:li:dataPlatform:dbt,pack.order_entry_db.order_entry.addresses,PROD)
    -> addresses
    """
    inner = urn.split(",")[1] if "," in urn else urn
    return inner.split(".")[-1].strip().lower()


def _slot(record: dict, *path: str) -> Any:
    """Walk a path of dict keys, tolerating missing keys and explicit nulls.

    The MCP server omits absent facets entirely on some records and returns null
    on others (`tags` is missing when untagged; `domain` comes back as null) —
    both mean the same thing here."""
    cursor: Any = record
    for key in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
        if cursor is None:
            return None
    return cursor


def _owner_display(owner: dict) -> str:
    for path in (("properties", "displayName"), ("info", "displayName"),
                 ("properties", "email"), ("username",), ("name",)):
        value = _slot(owner, *path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return owner.get("urn", "unknown")


def parse_dataset(record: dict) -> Dataset:
    """Turn one get_entities record into a Dataset.

    Description is read only from the dataset's own two slots. A deep search for
    any "description" key finds the text attached to an ownership *type* or a
    glossary term and silently concludes the dataset is documented."""
    urn = record.get("urn", "")

    description = ""
    for slot in ("properties", "editableProperties"):
        candidate = _slot(record, slot, "description")
        if isinstance(candidate, str) and candidate.strip():
            description = candidate.strip()
            break

    owners: list[Owner] = []
    seen_owner: set[tuple[str, str | None]] = set()
    for entry in _slot(record, "ownership", "owners") or []:
        owner = entry.get("owner") or {}
        owner_urn = owner.get("urn")
        if not owner_urn:
            continue
        type_urn = _slot(entry, "ownershipType", "urn")
        key = (owner_urn, type_urn)
        if key in seen_owner:   # the same person is listed twice on some records
            continue
        seen_owner.add(key)
        owners.append(Owner(
            urn=owner_urn,
            display=_owner_display(owner),
            ownership_type_urn=type_urn,
            ownership_type_name=_slot(entry, "ownershipType", "info", "name"),
        ))

    tags: list[Tag] = []
    seen_tag: set[str] = set()
    for entry in _slot(record, "tags", "tags") or []:
        tag = entry.get("tag") or {}
        tag_urn = tag.get("urn")
        if not tag_urn or tag_urn in seen_tag:
            continue
        seen_tag.add(tag_urn)
        tags.append(Tag(
            urn=tag_urn,
            name=_slot(tag, "properties", "name") or tag_urn.split(":")[-1],
            description=_slot(tag, "properties", "description") or "",
        ))

    fields = [
        f.get("fieldPath") for f in (_slot(record, "schemaMetadata", "fields") or [])
        if isinstance(f, dict) and f.get("fieldPath")
    ]

    name = record.get("name") or _slot(record, "properties", "name") or logical_name(urn)

    return Dataset(
        urn=urn,
        name=name,
        platform=_slot(record, "platform", "name") or platform_from_urn(urn),
        description=description,
        owners=tuple(owners),
        tags=tuple(tags),
        domain_urn=_slot(record, "domain", "domain", "urn"),
        domain_name=_slot(record, "domain", "domain", "properties", "name"),
        fields=tuple(fields),
    )


def dataset_urns(search_payload: dict) -> list[str]:
    """Dataset URNs from a search page, in order, deduped."""
    out: list[str] = []
    for result in (search_payload or {}).get("searchResults") or []:
        urn = (result.get("entity") or {}).get("urn", "")
        if urn.startswith("urn:li:dataset:") and urn not in out:
            out.append(urn)
    return out


# --------------------------------------------------------------------------
# twinning (pure)
# --------------------------------------------------------------------------

def field_overlap(a: Dataset, b: Dataset) -> tuple[float, int]:
    """Agreement between two column sets, as (ratio, shared count).

    Divided by the *smaller* set so a narrow table still matches the wide view
    built from it, rather than being penalised for the columns it lacks."""
    fa, fb = a.normalized_fields, b.normalized_fields
    if not fa or not fb:
        return 0.0, 0
    shared = len(fa & fb)
    return shared / min(len(fa), len(fb)), shared


def find_twins(target: Dataset, corpus: Iterable[Dataset]) -> list[Twin]:
    """Copies of the same logical table on other platforms, best match first."""
    twins: list[Twin] = []
    for other in corpus:
        if other.urn == target.urn or other.logical_name != target.logical_name:
            continue
        overlap, shared = field_overlap(target, other)
        if overlap >= MIN_FIELD_OVERLAP and shared >= MIN_SHARED_FIELDS:
            twins.append(Twin(dataset=other, overlap=overlap, shared_fields=shared))
    # deterministic: strongest evidence first, URN breaks ties so runs are repeatable
    twins.sort(key=lambda t: (-t.overlap, -t.shared_fields, t.dataset.urn))
    return twins


def twin_evidence(twin: Twin) -> dict:
    return {
        "twin_urn": twin.dataset.urn,
        "twin_platform": twin.dataset.platform,
        "twin_name": twin.dataset.name,
        "field_overlap": round(twin.overlap, 3),
        "shared_fields": twin.shared_fields,
    }


# --------------------------------------------------------------------------
# fetching (the only part that needs a live DataHub)
# --------------------------------------------------------------------------

PAGE_SIZE = 50          # the MCP search tool clamps num_results to 50, silently
BATCH_SIZE = 10         # URNs per get_entities call


async def fetch_corpus(dh, limit: int = 500, progress=None) -> list[Dataset]:
    """Every dataset in the catalog, with its governance facets.

    Enumerated with an entity_type filter and real pagination: an unfiltered
    search spends its 50-result page on data products, containers and users, and
    asking for more does not help — the tool caps the page at 50 and says
    nothing about the ones it dropped."""
    urns: list[str] = []
    offset = 0
    while len(urns) < limit:
        page = await dh.search(
            query="*", filter="entity_type = dataset",
            num_results=min(PAGE_SIZE, limit - len(urns)), offset=offset,
        )
        found = dataset_urns(page)
        if not found:
            break
        urns.extend(u for u in found if u not in urns)
        offset += len(page.get("searchResults") or [])
        if offset >= int(page.get("total") or 0):
            break

    urns = urns[:limit]
    if progress:
        progress(f"enumerated {len(urns)} datasets")

    datasets: list[Dataset] = []
    for start in range(0, len(urns), BATCH_SIZE):
        chunk = urns[start:start + BATCH_SIZE]
        records = await dh.get_entities(chunk)
        if isinstance(records, dict):
            records = [records]
        datasets.extend(parse_dataset(r) for r in records if isinstance(r, dict))
        if progress:
            progress(f"loaded {len(datasets)}/{len(urns)}")
    return datasets
