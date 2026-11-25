from __future__ import annotations

import hashlib
import importlib
import inspect
import sqlite3
import textwrap
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from ..connection import connect
from .lock import migration_lock


BASELINE_VERSION = 1
BASELINE_NAME = "legacy schema baseline"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple
    compatible_checksums: tuple = ()

    @property
    def checksum(self) -> str:
        material = "{}\0{}\0{}".format(
            self.version,
            self.name,
            "\0".join(self.statements),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def apply(self, connection: sqlite3.Connection) -> None:
        for statement in self.statements:
            connection.execute(statement)


def source_checksum(functions: Iterable[Callable]) -> str:
    digest = hashlib.sha256()
    for function in functions:
        source = textwrap.dedent(inspect.getsource(function))
        digest.update(function.__module__.encode("utf-8"))
        digest.update(b"\0")
        digest.update(function.__qualname__.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.replace("\r\n", "\n").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def legacy_identifier_checksum(version: int, name: str) -> str:
    return hashlib.sha256(
        "{}:{}".format(version, name).encode("utf-8")
    ).hexdigest()


_baseline_module = importlib.import_module(
    "{}.0001_baseline".format(__package__)
)
_integrity_module = importlib.import_module(
    "{}.0002_runtime_integrity".format(__package__)
)
_jobs_module = importlib.import_module(
    "{}.0003_jobs_and_leases".format(__package__)
)
_metadata_language_module = importlib.import_module(
    "{}.0004_metadata_language".format(__package__)
)
_data_dictionary_module = importlib.import_module(
    "{}.0005_data_dictionary".format(__package__)
)
_long_title_display_module = importlib.import_module(
    "{}.0006_long_title_display".format(__package__)
)
_settings_module = importlib.import_module(
    "{}.0007_settings".format(__package__)
)
_subtitle_assets_module = importlib.import_module(
    "{}.0008_subtitle_assets".format(__package__)
)
_subtitle_default_priority_module = importlib.import_module(
    "{}.0009_subtitle_default_priority".format(__package__)
)
_format_subtitle_selection_module = importlib.import_module(
    "{}.0010_format_subtitle_selection".format(__package__)
)

MIGRATIONS = (
    _integrity_module.runtime_integrity_migration,
    _jobs_module.jobs_and_leases_migration,
    _metadata_language_module.metadata_language_migration,
    _data_dictionary_module.data_dictionary_migration,
    _long_title_display_module.long_title_display_migration,
    _settings_module.settings_migration,
    _subtitle_assets_module.subtitle_assets_migration,
    _subtitle_default_priority_module.subtitle_default_priority_migration,
    _format_subtitle_selection_module.format_subtitle_selection_migration,
)


def _now() -> str:
    from ..transaction import now_iso

    return now_iso()


def _applied_versions() -> Dict[int, str]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT version, checksum FROM schema_migrations"
        ).fetchall()
    return {int(row["version"]): str(row["checksum"]) for row in rows}


def applied_migrations() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT version, name, checksum, applied_at
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _record_migration(version: int, name: str, checksum: str) -> None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (
                version, name, checksum, applied_at
            ) VALUES (?, ?, ?, ?)
            """,
            (version, name, checksum, _now()),
        )


def _update_checksum(version: int, checksum: str) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = ?",
            (checksum, version),
        )


def run_migrations(
    baseline_apply: Optional[Callable[[], None]] = None,
    baseline_functions: Iterable[Callable] = (),
    compatible_baseline_checksums: Iterable[str] = (),
) -> None:
    functions = tuple(baseline_functions)
    if not functions:
        functions = _baseline_module.baseline_functions()
    apply_baseline = baseline_apply or _baseline_module.baseline_apply
    baseline_checksum = source_checksum(functions)
    with migration_lock():
        with connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )

        versions = _applied_versions()
        if BASELINE_VERSION not in versions:
            apply_baseline()
            _record_migration(
                BASELINE_VERSION,
                BASELINE_NAME,
                baseline_checksum,
            )
        elif versions[BASELINE_VERSION] != baseline_checksum:
            compatible = set(compatible_baseline_checksums)
            compatible.update(_baseline_module.prior_checksums)
            current = versions[BASELINE_VERSION]
            if (
                current != legacy_identifier_checksum(
                    BASELINE_VERSION,
                    BASELINE_NAME,
                )
                and current not in compatible
            ):
                raise RuntimeError("Schema baseline checksum mismatch")
            apply_baseline()
            _update_checksum(BASELINE_VERSION, baseline_checksum)

        for migration in MIGRATIONS:
            applied_checksum = versions.get(migration.version)
            if applied_checksum is None:
                with connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    migration.apply(connection)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations (
                            version, name, checksum, applied_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            migration.version,
                            migration.name,
                            migration.checksum,
                            _now(),
                        ),
                    )
                continue
            if applied_checksum == migration.checksum:
                continue
            compatible = {
                legacy_identifier_checksum(
                    migration.version,
                    migration.name,
                ),
                *migration.compatible_checksums,
            }
            if applied_checksum not in compatible:
                raise RuntimeError(
                    "Migration {} checksum mismatch".format(
                        migration.version
                    )
                )
            with connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                migration.apply(connection)
            _update_checksum(migration.version, migration.checksum)
