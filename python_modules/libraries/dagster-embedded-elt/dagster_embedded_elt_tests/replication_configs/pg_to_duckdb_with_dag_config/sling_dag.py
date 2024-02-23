from dagster import AssetSpec, Definitions, file_relative_path
from dagster_embedded_elt.sling import (
    DagsterSlingTranslator,
    SlingMode,
    build_sling_asset,
    sling_assets,
)
from dagster_embedded_elt.sling.resources import (
    SlingConnectionResource,
    SlingResource,
    SlingSourceConnection,
    SlingTargetConnection,
)

replication_config = file_relative_path(__file__, "sling_replication.yaml")

sling_resource = SlingResource(
    connections=[
        SlingConnectionResource(
            name="MY_POSTGRES",
            type="postgres",
            connection_string="postgres://postgres:postgres@localhost:5432/finance?sslmode=disable",
        ),
        SlingConnectionResource(
            name="MY_DUCKDB",
            type="duckdb",
            connection_string="duckdb:///var/tmp/duckdb.db",
        ),
    ]
)

asset_deprecated = build_sling_asset(
    asset_spec=AssetSpec(key=["main", "dest_tbl"]),
    source_stream="file:///tmp/test.csv",
    target_object="main.dest_table",
    mode=SlingMode.INCREMENTAL,
    primary_key="id",
)


@sling_assets(replication_config=replication_config)
def my_assets(context, sling: SlingResource):
    yield from sling.replicate(
        replication_config=replication_config,
        dagster_sling_translator=DagsterSlingTranslator(),
    )
    for row in sling.stream_raw_logs():
        context.log.info(row)


sling_other_resource = SlingResource(
    source_connection=SlingSourceConnection(
        type="postgres",
        connection_string="postgres://postgres:postgres@localhost:5432/finance?sslmode=disable",
    ),
    target_connection=SlingTargetConnection(
        type="duckdb", connection_string="duckdb:///var/tmp/duckdb.db"
    ),
)

defs = Definitions(
    assets=[my_assets, asset_deprecated],
    resources={"sling": sling_resource, "other": sling_other_resource},
)
