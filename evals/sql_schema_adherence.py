import re

import sqlglot
from sqlglot import expressions as exp

# Matches the blocks produced by database/db_queries.py::fetch_context (via
# format_schema): "Table Schema of <table>:\n\n- col: type\n- col: type\n\n"
_TABLE_BLOCK_RE = re.compile(
    r"Table Schema of (?P<table>\S+):\s*\n\n(?P<columns>.*?)(?=\n\nTable Schema of|\Z)",
    re.DOTALL,
)
_COLUMN_LINE_RE = re.compile(r"^-\s*(?P<column>[^:]+):", re.MULTILINE)


def parse_table_schema(table_schema: str) -> dict:
    """
    Parse the `table_schema` text produced by database/db_queries.py::fetch_context
    back into {table_name: {column_name, ...}} for deterministic comparison.
    """

    schema_map = {}
    if not table_schema:
        return schema_map

    normalized = table_schema.strip() + "\n\n"
    for match in _TABLE_BLOCK_RE.finditer(normalized):
        table = match.group("table").strip().rstrip(":").lower()
        columns = {
            col.strip().lower() for col in _COLUMN_LINE_RE.findall(match.group("columns"))
        }
        schema_map[table] = columns

    return schema_map


def extract_sql_identifiers(sql: str, dialect: str = "postgres") -> tuple:
    """Parse `sql` and return (tables, columns) referenced in it, lowercased."""

    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:
        raise ValueError(f"Failed to parse generated SQL: {e}") from e

    tables = {table.name.lower() for table in parsed.find_all(exp.Table) if table.name}
    columns = {column.name.lower() for column in parsed.find_all(exp.Column) if column.name}

    return tables, columns


def check_schema_adherence(sql: str, table_schema: str, dialect: str = "postgres") -> dict:
    """
    Deterministically verify that every table/column referenced in `sql` is
    present in `table_schema` - the exact context the SQL-generation LLM was
    given (see prompts/prompts_templates.py::format_sql_prompt's ##Table
    Schema## section). This is the "SQL groundedness" check: no LLM judge
    needed, since correctness here is a fact about the schema, not a matter
    of interpretation.

    Returns:
        {
          "adherent": bool,
          "unknown_tables": [...],   # referenced but not in table_schema
          "unknown_columns": [...],  # referenced but not in table_schema
        }
    """

    schema_map = parse_table_schema(table_schema)
    known_tables = set(schema_map.keys())
    known_columns = {column for columns in schema_map.values() for column in columns}

    tables, columns = extract_sql_identifiers(sql, dialect=dialect)

    unknown_tables = sorted(table for table in tables if table not in known_tables)
    # Columns aren't always unambiguously qualified to a single table in the
    # generated SQL, so membership is checked against the union of all known
    # columns across the tables provided in the schema context.
    unknown_columns = sorted(column for column in columns if column not in known_columns)

    return {
        "adherent": not unknown_tables and not unknown_columns,
        "unknown_tables": unknown_tables,
        "unknown_columns": unknown_columns,
    }


if __name__ == "__main__":
    sample_schema = (
        "Table Schema of work_orders:\n\n"
        "- wo_id: integer\n"
        "- wo_status: character varying\n"
        "- facility_code: character varying\n\n"
        "Table Schema of assets:\n\n"
        "- asset_id: integer\n"
        "- asset_name: character varying\n\n"
    )

    good_sql = (
        "SELECT wo_id, wo_status FROM work_orders "
        "WHERE facility_code IN ('<facilitycode>') LIMIT 50;"
    )
    bad_sql = (
        "SELECT wo_id, technician_name FROM work_orders "
        "JOIN maintenance_log ON work_orders.wo_id = maintenance_log.wo_id LIMIT 50;"
    )

    print("Expected adherent=True ->", check_schema_adherence(good_sql, sample_schema))
    print("Expected adherent=False ->", check_schema_adherence(bad_sql, sample_schema))
