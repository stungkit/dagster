import re
import sys
import time
from unittest import mock

import dagster as dg
import pytest
from dagster import Definitions
from dagster._core.definitions.resolved_asset_deps import resolve_similar_asset_names


@pytest.fixture(
    name="string_similarity_package",
    params=["difflib", "rapidfuzz"],
    ids=["no difflib", "no rapidfuzz"],
    autouse=True,
)
def uninstall_string_similarity_package_fixture(request: pytest.FixtureRequest):
    with mock.patch.dict(sys.modules, {request.param: None}):
        yield


@pytest.mark.parametrize("group_name", [None, "my_group"])
@pytest.mark.parametrize("asset_key_prefix", [[], ["my_prefix"]])
def test_typo_upstream_asset_one_similar(group_name, asset_key_prefix) -> None:
    @dg.asset(group_name=group_name, key_prefix=asset_key_prefix)
    def asset1(): ...

    @dg.asset(
        group_name=group_name,
        key_prefix=asset_key_prefix,
        ins={"asst1": dg.AssetIn(asset_key_prefix + ["asst1"])},
    )
    def asset2(asst1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"asst1\".* is not produced by any of the provided asset ops and is"
            r" not one of the provided sources. Did you mean one of the following\?"
            rf"\n\t{re.escape(asset1.key.to_string())}"
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asset1, asset2]))


def test_typo_upstream_asset_no_similar() -> None:
    @dg.asset
    def asset1(): ...

    @dg.asset
    def asset2(not_close_to_asset1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"not_close_to_asset1\".* is not produced by any of the provided asset"
            r" ops and is not one of the provided sources."
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asset1, asset2]))


def test_typo_upstream_asset_many_similar() -> None:
    @dg.asset
    def asset1(): ...

    @dg.asset
    def assets1(): ...

    @dg.asset
    def asst(): ...

    @dg.asset
    def asset2(asst1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"asst1\".* is not produced by any of the provided asset ops and is"
            r" not one of the provided sources. Did you mean one of the following\?"
            rf"\n\t{re.escape(asset1.key.to_string())},"
            rf" {re.escape(assets1.key.to_string())},"
            rf" {re.escape(asst.key.to_string())}"
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asst, asset1, assets1, asset2]))


def test_typo_upstream_asset_wrong_prefix() -> None:
    @dg.asset(key_prefix=["my", "prefix"])
    def asset1(): ...

    @dg.asset(ins={"asset1": dg.AssetIn(key=dg.AssetKey(["my", "prfix", "asset1"]))})
    def asset2(asset1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"asset1\".* is not produced by any of the provided asset ops and is"
            r" not one of the provided sources. Did you mean one of the following\?"
            rf"\n\t{re.escape(asset1.key.to_string())}"
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asset1, asset2]))


def test_typo_upstream_asset_wrong_prefix_and_wrong_key() -> None:
    # In the case that the user has a typo in the key and the prefix, we don't suggest the asset since it's too different.

    @dg.asset(key_prefix=["my", "prefix"])
    def asset1(): ...

    @dg.asset(ins={"asset1": dg.AssetIn(key=dg.AssetKey(["my", "prfix", "asset4"]))})
    def asset2(asset1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"asset4\".* is not produced by any of the provided asset ops and is"
            r" not one of the provided sources."
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asset1, asset2]))


def test_one_off_component_prefix() -> None:
    @dg.asset(key_prefix=["my", "prefix"])
    def asset1(): ...

    # One more component in the prefix
    @dg.asset(ins={"asset1": dg.AssetIn(key=dg.AssetKey(["my", "prefix", "nested", "asset1"]))})
    def asset2(asset1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"asset1\".* is not produced by any of the provided asset ops and is"
            r" not one of the provided sources. Did you mean one of the following\?"
            rf"\n\t{re.escape(asset1.key.to_string())}"
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asset1, asset2]))

    # One fewer component in the prefix
    @dg.asset(ins={"asset1": dg.AssetIn(key=dg.AssetKey(["my", "asset1"]))})
    def asset3(asset1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"asset1\".* is not produced by any of the provided asset ops and is"
            r" not one of the provided sources. Did you mean one of the following\?"
            rf"\n\t{re.escape(asset1.key.to_string())}"
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asset1, asset3]))


def test_accidentally_using_slashes() -> None:
    @dg.asset(key_prefix=["my", "prefix"])
    def asset1(): ...

    # Use slashes instead of list
    @dg.asset(ins={"asset1": dg.AssetIn(key=dg.AssetKey(["my/prefix/asset1"]))})
    def asset2(asset1): ...

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match=(
            r"Input asset .*\"my/prefix/asset1\".* is not produced by any of the provided asset ops and is"
            r" not one of the provided sources. Did you mean one of the following\?"
            rf"\n\t{re.escape(asset1.key.to_string())}"
        ),
    ):
        Definitions.validate_loadable(dg.Definitions(assets=[asset1, asset2]))


NUM_ASSETS_TO_TEST_PERF = 5000
# As of 2/16/2023, `avg_elapsed_time_secs` is ~0.024s on a MBP, ~0.15s on BK
PERF_CUTOFF_SECS = 0.3
NUM_PERF_TRIALS = 10


def test_perf() -> None:
    assets: list[dg.AssetKey] = []
    for i in range(NUM_ASSETS_TO_TEST_PERF):

        @dg.asset(name="asset_" + str(i))
        def my_asset(): ...

        assets.append(my_asset.key)

    total_elapsed_time_secs = 0
    for _ in range(NUM_PERF_TRIALS):
        start_time = time.time()
        resolve_similar_asset_names(dg.AssetKey("asset_" + str(NUM_ASSETS_TO_TEST_PERF)), assets)
        end_time = time.time()

        elapsed_time_secs = end_time - start_time

        total_elapsed_time_secs += elapsed_time_secs

    avg_elapsed_time_secs = total_elapsed_time_secs / NUM_PERF_TRIALS

    assert avg_elapsed_time_secs < PERF_CUTOFF_SECS, (
        "Performance of resolve_similar_asset_names has regressed"
    )
