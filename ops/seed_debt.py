"""Seed synthetic metadata debt into a local DataHub for demos and evals.

Creates datasets for a fictional company (Brightledger, Inc. — no real brands)
with deliberate defects: no description, schema present. Run with the datahub
CLI venv's python: DATAHUB_GMS_URL=http://localhost:8080 python ops/seed_debt.py
"""

import os

from datahub.emitter.mce_builder import make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    DatasetPropertiesClass,
    NumberTypeClass,
    OtherSchemaClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
)

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")

DEBT_DATASETS = {
    "brightledger.billing.invoice_lines": [
        ("invoice_id", StringTypeClass()),
        ("line_no", NumberTypeClass()),
        ("sku", StringTypeClass()),
        ("quantity", NumberTypeClass()),
        ("unit_price_cents", NumberTypeClass()),
        ("tax_cents", NumberTypeClass()),
    ],
    "brightledger.crm.account_touchpoints": [
        ("account_id", StringTypeClass()),
        ("touchpoint_ts", StringTypeClass()),
        ("channel", StringTypeClass()),
        ("rep_id", StringTypeClass()),
        ("outcome", StringTypeClass()),
    ],
}


def main() -> None:
    emitter = DatahubRestEmitter(gms_server=GMS)
    for name, fields in DEBT_DATASETS.items():
        urn = make_dataset_urn(platform="postgres", name=name, env="PROD")
        schema = SchemaMetadataClass(
            schemaName=name,
            platform="urn:li:dataPlatform:postgres",
            version=0,
            hash="",
            platformSchema=OtherSchemaClass(rawSchema=""),
            fields=[
                SchemaFieldClass(
                    fieldPath=fname,
                    type=SchemaFieldDataTypeClass(type=ftype),
                    nativeDataType="",
                )
                for fname, ftype in fields
            ],
        )
        emitter.emit(MetadataChangeProposalWrapper(entityUrn=urn, aspect=schema))
        # Without datasetProperties (which carries the display name) the entity
        # may never surface in search — emit it WITHOUT a description so the
        # dataset is findable but still carries the debt we seeded.
        emitter.emit(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=DatasetPropertiesClass(name=name.rsplit(".", 1)[-1]),
            )
        )
        print(f"seeded (no description): {urn}")


if __name__ == "__main__":
    main()
