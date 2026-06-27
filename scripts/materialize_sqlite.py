import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.dataset_service import dataset_service


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def inspect_database(dataset_id: str, rebuild: bool = False) -> None:
    database_path = (
        dataset_service.rebuild_database(dataset_id)
        if rebuild
        else dataset_service.get_database_path(dataset_id)
    )
    with closing(sqlite3.connect(database_path)) as connection:
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
        row_counts = {
            table_name: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {quote_identifier(table_name)}"
                ).fetchone()[0]
            )
            for table_name in table_names
        }

    print(f"dataset_id={dataset_id}")
    print(f"database={database_path}")
    print(f"size_bytes={database_path.stat().st_size}")
    print(f"tables={len(table_names)}")
    for table_name, row_count in row_counts.items():
        print(f"  {table_name}: {row_count} rows")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create missing dataset.sqlite3 files and verify their tables."
    )
    parser.add_argument(
        "--dataset-id",
        action="append",
        help="Only materialize the specified dataset ID. May be supplied multiple times.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild SQLite files even when they already exist.",
    )
    args = parser.parse_args()

    dataset_ids = args.dataset_id or list(dataset_service.datasets)
    if not dataset_ids:
        print("No datasets found.")
        return

    for index, dataset_id in enumerate(dataset_ids):
        if index:
            print()
        inspect_database(dataset_id, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
