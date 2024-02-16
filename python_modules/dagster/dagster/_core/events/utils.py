from json import JSONDecodeError
from typing import List, Sequence

import dagster._check as check
from dagster._core.definitions.events import AssetMaterialization, AssetObservation
from dagster._core.events import (
    AssetObservationData,
    AssetPartitionRangeMaterializationData,
    AssetPartitionRangeObservationData,
    DagsterEvent,
    DagsterEventType,
    StepMaterializationData,
    get_materialization_message,
)
from dagster._core.events.log import EventLogEntry
from dagster._serdes.errors import DeserializationError
from dagster._serdes.serdes import deserialize_value


def unpack_asset_partition_range_event(event: EventLogEntry) -> Sequence[EventLogEntry]:
    """Explode a partition range event into its constituent events."""
    check.inst_param(event, "event", EventLogEntry)

    data = event.get_dagster_event().event_specific_data
    if isinstance(data, AssetPartitionRangeMaterializationData):
        member_asset_events = data.materializations
        event_type = DagsterEventType.ASSET_MATERIALIZATION
    elif isinstance(data, AssetPartitionRangeObservationData):
        member_asset_events = data.observations
        event_type = DagsterEventType.ASSET_OBSERVATION
    else:
        check.failed(f"Unexpected event type: {type(data)}")

    events: List[EventLogEntry] = []
    for asset_event in member_asset_events:
        if isinstance(asset_event, AssetMaterialization):
            event_specific_data = StepMaterializationData(materialization=asset_event)
            message = get_materialization_message(asset_event)
        elif isinstance(asset_event, AssetObservation):
            event_specific_data = AssetObservationData(asset_observation=asset_event)
            message = None
        else:
            check.failed(f"Unexpected event type: {type(asset_event)}")

        events.append(
            EventLogEntry(
                error_info=None,
                level=event.level,
                user_message=event.user_message,
                run_id=event.run_id,
                timestamp=event.timestamp,
                step_key=event.step_key,
                job_name=event.job_name,
                dagster_event=DagsterEvent.from_parent_event(
                    event_type=event_type,
                    event_specific_data=event_specific_data,
                    parent_event=event.get_dagster_event(),
                    message=message,
                ),
            )
        )

    return events


def filter_dagster_events_from_cli_logs(log_lines):
    """Filters the raw log lines from a dagster-cli invocation to return only the lines containing json.

    - Log lines don't necessarily come back in order
    - Something else might log JSON
    - Docker appears to silently split very long log lines -- this is undocumented behavior

    TODO: replace with reading event logs from the DB

    """
    check.list_param(log_lines, "log_lines", str)

    coalesced_lines = []
    buffer = []
    in_split_line = False
    for raw_line in log_lines:
        line = raw_line.strip()
        if not in_split_line and line.startswith("{"):
            if line.endswith("}"):
                coalesced_lines.append(line)
            else:
                buffer.append(line)
                in_split_line = True
        elif in_split_line:
            buffer.append(line)
            if line.endswith("}"):  # Note: hack, this may not have been the end of the full object
                coalesced_lines.append("".join(buffer))
                buffer = []
                in_split_line = False

    events = []
    for line in coalesced_lines:
        try:
            events.append(deserialize_value(line, DagsterEvent))
        except JSONDecodeError:
            pass
        except check.CheckError:
            pass
        except DeserializationError:
            pass

    return events
