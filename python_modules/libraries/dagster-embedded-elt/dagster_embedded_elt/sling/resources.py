import contextlib
import json
import re
from enum import Enum
from subprocess import PIPE, STDOUT, Popen
from typing import IO, Any, AnyStr, Dict, Generator, Iterator, List, Optional

from dagster import ConfigurableResource, PermissiveConfig, get_dagster_logger
from dagster._annotations import experimental
from dagster._config.field_utils import EnvVar
from enum import Enum
from subprocess import PIPE, STDOUT, Popen
from typing import IO, Any, AnyStr, Dict, Generator, Iterator, List, Optional

from dagster import (
    ConfigurableResource,
    EnvVar,
    MaterializeResult,
    PermissiveConfig,
    get_dagster_logger,
)
from dagster._annotations import experimental
from dagster._utils.env import environ
from pydantic import Field
from sling import Sling

logger = get_dagster_logger()


class SlingMode(str, Enum):
    """The mode to use when syncing.

    See the Sling docs for more information: https://docs.slingdata.io/sling-cli/running-tasks#modes.
    """

    INCREMENTAL = "incremental"
    TRUNCATE = "truncate"
    FULL_REFRESH = "full-refresh"
    SNAPSHOT = "snapshot"

logger = get_dagster_logger()


class SlingMode(str, Enum):
    """The mode to use when syncing.

    See the Sling docs for more information: https://docs.slingdata.io/sling-cli/running-tasks#modes.
    """

    INCREMENTAL = "incremental"
    TRUNCATE = "truncate"
    FULL_REFRESH = "full-refresh"
    SNAPSHOT = "snapshot"


class SlingSourceConnection(PermissiveConfig):
    """A Sling Source Connection defines the source connection used by :py:class:`~dagster_elt.sling.SlingResource`.

    Examples:
        Creating a Sling Source for a file, such as CSV or JSON:

        .. code-block:: python

             source = SlingSourceConnection(type="file")

        Create a Sling Source for a Postgres database, using a connection string:

        .. code-block:: python

            source = SlingTargetConnection(type="postgres", connection_string=EnvVar("POSTGRES_CONNECTION_STRING"))
            source = SlingSourceConnection(type="postgres", connection_string="postgresql://user:password@host:port/schema")

        Create a Sling Source for a Postgres database, using keyword arguments, as described here:
        https://docs.slingdata.io/connections/database-connections/postgres

        .. code-block:: python

            source = SlingTargetConnection(type="postgres", host="host", user="hunter42", password=EnvVar("POSTGRES_PASSWORD"))

    """

    type: str = Field(description="Type of the source connection. Use 'file' for local storage.")
    connection_string: Optional[str] = Field(
        description="The connection string for the source database.",
        default=None,
    )


class SlingTargetConnection(PermissiveConfig):
    """A Sling Target Connection defines the target connection used by :py:class:`~dagster_elt.sling.SlingResource`.

    Examples:
        Creating a Sling Target for a file, such as CSV or JSON:

        .. code-block:: python

             source = SlingTargetConnection(type="file")

        Create a Sling Source for a Postgres database, using a connection string:

        .. code-block:: python

            source = SlingTargetConnection(type="postgres", connection_string="postgresql://user:password@host:port/schema"
            source = SlingTargetConnection(type="postgres", connection_string=EnvVar("POSTGRES_CONNECTION_STRING"))

        Create a Sling Source for a Postgres database, using keyword arguments, as described here:
        https://docs.slingdata.io/connections/database-connections/postgres

        .. code-block::python

            source = SlingTargetConnection(type="postgres", host="host", user="hunter42", password=EnvVar("POSTGRES_PASSWORD"))


    """

    type: str = Field(
        description="Type of the destination connection. Use 'file' for local storage."
    )
    connection_string: Optional[str] = Field(
        description="The connection string for the target database.",
        default=None,
    )


@experimental
class SlingResource(ConfigurableResource):
    """Resource for interacting with the Sling package.

    Examples:
        .. code-block:: python

            from dagster_etl.sling import SlingResource
            sling_resource = SlingResource(
                source_connection=SlingSourceConnection(
                    type="postgres", connection_string=EnvVar("POSTGRES_CONNECTION_STRING")
                ),
                target_connection=SlingTargetConnection(
                    type="snowflake",
                    host="host",
                    user="user",
                    database="database",
                    password="password",
                    role="role",
                ),
            )

    """

    source_connection: SlingSourceConnection
    target_connection: SlingTargetConnection

    @contextlib.contextmanager
    def _setup_config(self) -> Generator[None, None, None]:
        """Uses environment variables to set the Sling source and target connections."""
        sling_source = _process_env_vars(dict(self.source_connection))
        sling_target = _process_env_vars(dict(self.target_connection))

@experimental
class SlingResource(ConfigurableResource):
    """Resource for interacting with the Sling package.

    Examples:
        .. code-block:: python

            from dagster_etl.sling import SlingResource
            sling_resource = SlingResource(
                source_connection=SlingSourceConnection(
                    type="postgres", connection_string=EnvVar("POSTGRES_CONNECTION_STRING")
                ),
                target_connection=SlingTargetConnection(
                    type="snowflake",
                    host="host",
                    user="user",
                    database="database",
                    password="password",
                    role="role",
                ),
            )

    """

    source_connection: SlingSourceConnection
    target_connection: SlingTargetConnection

    @contextlib.contextmanager
    def _setup_config(self) -> Generator[None, None, None]:
        """Uses environment variables to set the Sling source and target connections."""
        sling_source = _process_env_vars(dict(self.source_connection))
        sling_target = _process_env_vars(dict(self.target_connection))

        if self.source_connection.connection_string:
            sling_source["url"] = self.source_connection.connection_string
        if self.target_connection.connection_string:
            sling_target["url"] = self.target_connection.connection_string
        with environ(

        if self.source_connection.connection_string:
            sling_source["url"] = self.source_connection.connection_string
        if self.target_connection.connection_string:
            sling_target["url"] = self.target_connection.connection_string
        with environ(
            {
                "SLING_SOURCE": json.dumps(sling_source),
                "SLING_TARGET": json.dumps(sling_target),
            }
        ):
            yield

    def process_stdout(self, stdout: IO[AnyStr], encoding="utf8") -> Iterator[str]:
        """Process stdout from the Sling CLI."""
        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        for line in stdout:
            assert isinstance(line, bytes)
            fmt_line = bytes.decode(line, encoding=encoding, errors="replace")
            clean_line: str = ansi_escape.sub("", fmt_line).replace("INF", "")
            yield clean_line

    def _exec_sling_cmd(
        self, cmd, stdin=None, stdout=PIPE, stderr=STDOUT, encoding="utf8"
    ) -> Generator[str, None, None]:
        with Popen(cmd, shell=True, stdin=stdin, stdout=stdout, stderr=stderr) as proc:
            if proc.stdout:
                for line in self.process_stdout(proc.stdout, encoding=encoding):
                    yield line

            proc.wait()
            if proc.returncode != 0:
                raise Exception("Sling command failed with error code %s", proc.returncode)

    def sync(
        self,
        source_stream: str,
        target_object: str,
        mode: SlingMode = SlingMode.FULL_REFRESH,
        primary_key: Optional[List[str]] = None,
        update_key: Optional[str] = None,
        source_options: Optional[Dict[str, Any]] = None,
        target_options: Optional[Dict[str, Any]] = None,
        encoding: str = "utf8",
    ) -> Generator[str, None, None]:
        """Runs a Sling sync from the given source table to the given destination table. Generates
        output lines from the Sling CLI.
        """
        if self.source_connection.type == "file" and not source_stream.startswith("file://"):
            source_stream = "file://" + source_stream

        if self.target_connection.type == "file" and not target_object.startswith("file://"):
            target_object = "file://" + target_object

        with self._setup_config():
            config = {
                "mode": mode,
                "source": {
                    "conn": "SLING_SOURCE",
                    "stream": source_stream,
                    "primary_key": primary_key,
                    "update_key": update_key,
                    "options": source_options,
                },
                "target": {
                    "conn": "SLING_TARGET",
                    "object": target_object,
                    "options": target_options,
                },
            }
            config["source"] = {k: v for k, v in config["source"].items() if v is not None}
            config["target"] = {k: v for k, v in config["target"].items() if v is not None}

            sling_cli = Sling(**config)
            logger.info("Starting Sling sync with mode: %s", mode)
            cmd = sling_cli._prep_cmd()  # noqa: SLF001

            yield from self._exec_sling_cmd(cmd, encoding=encoding)


def _process_env_vars(config: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in config.items():
        if isinstance(value, dict) and len(value) == 1 and next(iter(value.keys())) == "env":
            out[key] = EnvVar(next(iter(value.values()))).get_value()
        else:
            out[key] = value
    return out
            source_stream = "file://" + source_stream

        if self.target_connection.type == "file" and not target_object.startswith("file://"):
            target_object = "file://" + target_object

        with self._setup_config():
            uid = uuid.uuid4()
            temp_dir = tempfile.gettempdir()
            temp_file = os.path.join(temp_dir, f"sling-replication-{uid}.json")
            env = os.environ.copy()

            with open(temp_file, "w") as file:
                json.dump(replication_config, file, cls=sling.JsonEncoder)

            debug_str = "-d" if debug else ""

            cmd = f"{sling.SLING_BIN} run {debug_str} -r {temp_file}"

            logger.debug(f"Running Sling replication with command: {cmd}")

            results = sling._run(  # noqa
                cmd=cmd,
                temp_file=temp_file,
                return_output=True,
                env=env,
            )

        for stream in stream_definition:
            output_name = dagster_sling_translator.get_asset_key(stream)
            yield MaterializeResult(asset_key=output_name)

            with open(temp_file, "w") as file:
                json.dump(replication_config, file, cls=sling.JsonEncoder)

            debug_str = "-d" if debug else ""

            cmd = f"{sling.SLING_BIN} run {debug_str} -r {temp_file}"

            logger.debug(f"Running Sling replication with command: {cmd}")

            results = sling._run(  # noqa
                cmd=cmd,
                temp_file=temp_file,
                return_output=True,
                env=env,
            )

        for stream in stream_definition:
            output_name = dagster_sling_translator.get_asset_key(stream)
            yield MaterializeResult(asset_key=output_name)


def _process_env_vars(config: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in config.items():
        if isinstance(value, dict) and len(value) == 1 and next(iter(value.keys())) == "env":
            out[key] = EnvVar(next(iter(value.values()))).get_value()
        else:
            out[key] = value
    return out
