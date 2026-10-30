"""Build a new fictional seed; existing files are never replaced."""
import argparse
from pathlib import Path

from tender_app.execution_seed import create_demo_database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    create_demo_database(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
