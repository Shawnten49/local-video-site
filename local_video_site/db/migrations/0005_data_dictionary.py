from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from ..vocabulary import COLUMNS, VALUE_SETS, ColumnVocabulary
from .runner import BASELINE_VERSION, Migration


def _sql(value: object) -> str:
    if value is None:
        return "NULL"
    return "'{}'".format(str(value).replace("'", "''"))


def _identifier(value: str) -> str:
    return '"{}"'.format(str(value).replace('"', '""'))


def _schema_statements() -> Tuple[str, ...]:
    return (
        """
        CREATE TABLE IF NOT EXISTS schema_value_sets (
            set_key TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            description TEXT NOT NULL,
            is_open INTEGER NOT NULL DEFAULT 0
                CHECK (is_open IN (0, 1)),
            is_strict INTEGER NOT NULL DEFAULT 0
                CHECK (is_strict IN (0, 1)),
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS schema_value_set_items (
            set_key TEXT NOT NULL,
            value TEXT NOT NULL,
            label TEXT NOT NULL,
            description TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_default INTEGER NOT NULL DEFAULT 0
                CHECK (is_default IN (0, 1)),
            deprecated_at TEXT,
            PRIMARY KEY (set_key, value),
            FOREIGN KEY (set_key)
                REFERENCES schema_value_sets(set_key)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS schema_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            set_key TEXT NOT NULL,
            sql_type TEXT NOT NULL,
            nullable INTEGER NOT NULL
                CHECK (nullable IN (0, 1)),
            null_meaning TEXT,
            default_value TEXT,
            description TEXT NOT NULL,
            enforcement TEXT NOT NULL
                CHECK (
                    enforcement IN (
                        'check', 'trigger', 'application', 'none'
                    )
                ),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (table_name, column_name),
            FOREIGN KEY (set_key)
                REFERENCES schema_value_sets(set_key)
        )
        """,
        """
        DROP VIEW IF EXISTS schema_column_catalog
        """,
        """
        CREATE VIEW schema_column_catalog AS
        SELECT
            c.table_name,
            c.column_name,
            c.sql_type,
            c.nullable,
            c.null_meaning,
            c.default_value,
            c.description AS column_description,
            c.enforcement,
            s.set_key,
            s.label AS set_label,
            s.description AS set_description,
            s.is_open,
            s.is_strict,
            i.value,
            i.label AS value_label,
            i.description AS value_description,
            i.sort_order,
            i.is_default,
            i.deprecated_at
        FROM schema_columns c
        JOIN schema_value_sets s ON s.set_key = c.set_key
        LEFT JOIN schema_value_set_items i
            ON i.set_key = c.set_key
        """,
        """
        DROP VIEW IF EXISTS schema_column_summary
        """,
        """
        CREATE VIEW schema_column_summary AS
        SELECT
            c.table_name,
            c.column_name,
            c.sql_type,
            c.nullable,
            c.null_meaning,
            c.default_value,
            c.description,
            c.enforcement,
            c.set_key,
            s.label AS set_label,
            s.description AS set_description,
            s.is_open,
            s.is_strict,
            GROUP_CONCAT(
                CASE
                    WHEN i.value IS NULL THEN NULL
                    ELSE i.value || '=' || i.label
                END,
                ' | '
            ) AS allowed_values
        FROM schema_columns c
        JOIN schema_value_sets s ON s.set_key = c.set_key
        LEFT JOIN schema_value_set_items i
            ON i.set_key = c.set_key
        GROUP BY
            c.table_name, c.column_name, c.sql_type, c.nullable,
            c.null_meaning, c.default_value, c.description,
            c.enforcement, c.set_key, s.label, s.description,
            s.is_open, s.is_strict
        """,
    )


def _seed_statements() -> Tuple[str, ...]:
    statements: List[str] = [
        "DELETE FROM schema_columns",
        "DELETE FROM schema_value_set_items",
        "DELETE FROM schema_value_sets",
    ]
    for value_set in VALUE_SETS:
        statements.append(
            """
            INSERT INTO schema_value_sets (
                set_key, label, description, is_open, is_strict,
                version, updated_at
            ) VALUES ({}, {}, {}, {}, {}, 1, datetime('now'))
            """.format(
                _sql(value_set.key),
                _sql(value_set.label),
                _sql(value_set.description),
                int(value_set.is_open),
                int(value_set.is_strict),
            )
        )
        for term in value_set.values:
            statements.append(
                """
                INSERT INTO schema_value_set_items (
                    set_key, value, label, description, sort_order,
                    is_default, deprecated_at
                ) VALUES ({}, {}, {}, {}, {}, {}, {})
                """.format(
                    _sql(value_set.key),
                    _sql(term.value),
                    _sql(term.label),
                    _sql(term.description),
                    int(term.sort_order),
                    int(term.is_default),
                    _sql(term.deprecated_at),
                )
            )
    for column in COLUMNS:
        statements.append(
            """
            INSERT INTO schema_columns (
                table_name, column_name, set_key, sql_type, nullable,
                null_meaning, default_value, description, enforcement,
                updated_at
            ) VALUES ({}, {}, {}, {}, {}, {}, {}, {}, {},
                      datetime('now'))
            """.format(
                _sql(column.table_name),
                _sql(column.column_name),
                _sql(column.set_key),
                _sql(column.sql_type),
                int(column.nullable),
                _sql(column.null_meaning),
                _sql(column.default_value),
                _sql(column.description),
                _sql(column.enforcement),
            )
        )
    return tuple(statements)


def _strict_columns_by_table() -> Dict[str, List[ColumnVocabulary]]:
    value_sets = {item.key: item for item in VALUE_SETS}
    result: Dict[str, List[ColumnVocabulary]] = defaultdict(list)
    for column in COLUMNS:
        value_set = value_sets[column.set_key]
        if (
            value_set.is_strict
            and column.enforcement == "trigger"
        ):
            result[column.table_name].append(column)
    return {
        table: sorted(items, key=lambda item: item.column_name)
        for table, items in result.items()
    }


def _trigger_conditions(
    columns: Iterable[ColumnVocabulary],
    prefix: str,
    value_sets: dict,
) -> Tuple[str, ...]:
    conditions = []
    for column in columns:
        values = value_sets[column.set_key].values
        value_sql = ", ".join(
            _sql(term.value)
            for term in values
        )
        name = _identifier(column.column_name)
        invalid_non_null = (
            "NEW.{name} IS NOT NULL AND "
            "CAST(NEW.{name} AS TEXT) NOT IN ({values})"
        ).format(name=name, values=value_sql)
        if column.nullable:
            invalid = invalid_non_null
        else:
            invalid = (
                "(NEW.{name} IS NULL OR ({invalid_non_null}))"
            ).format(name=name, invalid_non_null=invalid_non_null)
        conditions.append(
            """
            SELECT CASE
                WHEN {invalid}
                THEN RAISE(
                    ABORT,
                    'schema vocabulary violation: {table}.{column}'
                )
            END
            """.format(
                invalid=invalid,
                table=column.table_name,
                column=column.column_name,
            )
        )
    return tuple(conditions)


def _trigger_statements() -> Tuple[str, ...]:
    value_sets = {item.key: item for item in VALUE_SETS}
    statements: List[str] = []
    for table_name, columns in _strict_columns_by_table().items():
        table = _identifier(table_name)
        safe_table = re.sub(r"[^a-zA-Z0-9_]", "_", table_name)
        update_columns = ", ".join(
            _identifier(item.column_name)
            for item in columns
        )
        insert_trigger = "trg_vocab_{}_insert".format(safe_table)
        update_trigger = "trg_vocab_{}_update".format(safe_table)
        statements.extend((
            "DROP TRIGGER IF EXISTS {}".format(
                _identifier(insert_trigger)
            ),
            """
            CREATE TRIGGER {}
            BEFORE INSERT ON {}
            BEGIN
                {}
            ;
            END
            """.format(
                _identifier(insert_trigger),
                table,
                ";\n".join(
                    _trigger_conditions(
                        columns,
                        insert_trigger,
                        value_sets,
                    )
                ),
            ),
            "DROP TRIGGER IF EXISTS {}".format(
                _identifier(update_trigger)
            ),
            """
            CREATE TRIGGER {}
            BEFORE UPDATE OF {} ON {}
            BEGIN
                {}
            ;
            END
            """.format(
                _identifier(update_trigger),
                update_columns,
                table,
                ";\n".join(
                    _trigger_conditions(
                        columns,
                        update_trigger,
                        value_sets,
                    )
                ),
            ),
        ))
    return tuple(statements)


def data_dictionary_statements() -> Tuple[str, ...]:
    return (
        *_schema_statements(),
        *_seed_statements(),
        """
        UPDATE video_format_jobs
        SET operation = 'container_remux'
        WHERE operation = 'mkv_to_mp4_remux'
        """,
        *_trigger_statements(),
    )


data_dictionary_migration = Migration(
    BASELINE_VERSION + 4,
    "add searchable schema vocabulary and strict value guards",
    data_dictionary_statements(),
)
