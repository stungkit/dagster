import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional, Union, cast

from dagster._annotations import public

from .core.resources_v2 import DbtCliResource
from .errors import DagsterDbtManifestNotPreparedError, DagsterDbtProjectNotFoundError


class _NullObj:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


_NULL_LOGGER = cast(logging.Logger, _NullObj())


class DbtArtifacts:
    def __init__(
        self,
        project_dir: Union[Path, str],
        *,
        target_folder: Union[Path, str] = "target",
        prepare_command: List[str] = ["parse", "--quiet"],
        package_data_dir: Optional[Union[Path, str]] = None,
    ):
        """A utility class to help manage dbt artifacts in different deployment contexts.

        This class provides a setup to solve for these goals:
        * During development, reload the manifest at run time to pick up any changes.
        * When deploying, expect a manifest that was prepared at build time to reduce start-up time.
        * Handle the scenario when the dbt project is copied in to a directory for packaging.

        Args:
            project_dir (Union[str, Path]):
                The directory of the dbt project.
            target_folder (Union[str, Path]):
                The folder in the project project directory to output artifacts.
                Default: "target"
            prepare_command: The dbt cli command to run to prepare the manifest.json
                Default: ["parse", "--quiet"]
            package_data_dir (Union[str, Path]):
                A directory that will contain a copy of the dbt project, synced at build time.
        """
        self.project_dir = Path(project_dir)
        if not self.project_dir.exists():
            raise DagsterDbtProjectNotFoundError(f"project_dir {project_dir} does not exist.")
        self._target_folder = Path(target_folder)
        self._prepare_command = prepare_command
        # this is ok if it doesn't exist, will get created by `prepare`
        self._package_data_dir = Path(package_data_dir) if package_data_dir else None

        if self._should_prepare_at_runtime():
            self._manifest_path = self._prepare_manifest()
        else:
            self._manifest_path = self._base_dir.joinpath(self._target_folder, "manifest.json")

    @public
    @property
    def manifest_path(self) -> Path:
        """The path to the manifest.json, compiling the manifest first if in development or
        ensuring it already exists if not.
        """
        if not self._manifest_path.exists():
            if self._should_prepare_at_runtime():
                raise DagsterDbtManifestNotPreparedError(
                    f"Unexpected state, {self._manifest_path} should have been created when this "
                    "object was initialized."
                )
            else:
                raise DagsterDbtManifestNotPreparedError(
                    f"Did not find prepared manifest.json at expected path {self._manifest_path}.\n"
                    "If this is an environment that is expected to prepare the manifest at run time, "
                    "set the environment variable DAGSTER_DBT_PARSE_PROJECT_ON_LOAD."
                )

        return self._manifest_path

    @public
    def prepare(self, *, quiet=False) -> None:
        """A method that can be called as part of the deployment process to handle
        preparing the manifest and if package_data_dir is set, handle copying
        the dbt project to that directory.

        Args:
            quiet (bool):
                Disable logging
                Default: False
        """
        logger = _NULL_LOGGER if quiet else logging.getLogger("dagster-dbt-artifacts")
        self._prepare_manifest(logger)
        self._handle_package_data(logger)
        logger.info("Preparation complete.")

    @public
    def get_cli_resource(self, **kwargs) -> DbtCliResource:
        """Construct a DbtCliResource that targets the correct project directory, passing any
        kwargs to DbtCliResource.
        If package_data_dir is set, resolves to that outside of the development context and
        uses project_dir when in a development context.
        """
        if self._should_prepare_at_runtime():
            return DbtCliResource(project_dir=os.fspath(self.project_dir))

        return DbtCliResource(
            project_dir=os.fspath(self._base_dir),
            **kwargs,
        )

    def _should_prepare_at_runtime(self) -> bool:
        return (
            # if launched via `dagster dev` cli
            bool(os.getenv("DAGSTER_IS_DEV_CLI"))
            or
            # or if explicitly opted in
            bool(os.getenv("DAGSTER_DBT_PARSE_PROJECT_ON_LOAD"))
        )

    @property
    def _base_dir(self) -> Path:
        return self._package_data_dir if self._package_data_dir else self.project_dir

    def _prepare_manifest(self, logger: logging.Logger = _NULL_LOGGER) -> Path:
        logger.info(f"Preparing dbt artifacts in {self.project_dir}.")
        return (
            DbtCliResource(project_dir=os.fspath(self.project_dir))
            .cli(
                self._prepare_command,
                target_path=self._target_folder,
            )
            .wait()
            .target_path.joinpath("manifest.json")
        )

    def _handle_package_data(self, logger: logging.Logger = _NULL_LOGGER) -> None:
        if self._package_data_dir is None:
            return
        logger.info(f"Preparing package data directory {self._package_data_dir}.")
        if self._package_data_dir.exists():
            logger.info(f"Removing existing contents at {self._package_data_dir}.")
            shutil.rmtree(self._package_data_dir)

        # Determine if the package data dir is within the project dir, and ignore
        # that path if so.
        rel_path = Path(os.path.relpath(self._package_data_dir, self.project_dir))
        rel_ignore = ""
        if len(rel_path.parts) > 0 and rel_path.parts[0] != "..":
            rel_ignore = rel_path.parts[0]

        logger.info(f"Copying {self.project_dir} to {self._package_data_dir}.")
        shutil.copytree(
            src=self.project_dir,
            dst=self._package_data_dir,
            ignore=shutil.ignore_patterns(
                "*.git*",
                "*partial_parse.msgpack",
                rel_ignore,
            ),
        )
