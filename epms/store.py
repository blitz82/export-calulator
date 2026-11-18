"""SQLite persistence and transaction boundaries."""
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Column, ForeignKey, Integer, LargeBinary, MetaData, String, Table, UniqueConstraint, create_engine, event
from sqlalchemy.engine import URL

from epms.rules import format_line_number

metadata = MetaData()
organizations = Table("organizations", metadata, Column("id", Integer, primary_key=True),
                      Column("name", String, unique=True, nullable=False))
office = Table("office", metadata, Column("id", Integer, primary_key=True),
               Column("org", ForeignKey("organizations.id"), nullable=False))
companies = Table("companies", metadata, Column("number", String, primary_key=True))
memberships = Table("memberships", metadata, Column("id", Integer, primary_key=True),
                    Column("org", ForeignKey("organizations.id"), nullable=False),
                    Column("number", ForeignKey("companies.number"), nullable=False),
                    Column("revision", Integer, nullable=False), Column("data", JSON, nullable=False),
                    UniqueConstraint("org", "number"))
import_files = Table("import_files", metadata, Column("id", Integer, primary_key=True),
                     Column("org", ForeignKey("organizations.id"), nullable=False),
                     Column("sha", String, nullable=False), Column("kind", String, nullable=False),
                     Column("name", String, nullable=False), Column("data", LargeBinary, nullable=False),
                     UniqueConstraint("org", "sha", "kind"))
import_rows = Table("import_rows", metadata, Column("id", Integer, primary_key=True),
                    Column("file", ForeignKey("import_files.id"), nullable=False),
                    Column("sheet", String, nullable=False), Column("row", Integer, nullable=False),
                    Column("original", JSON, nullable=False))
ocr_profiles = Table("ocr_profiles", metadata, Column("id", Integer, primary_key=True),
                     Column("org", ForeignKey("organizations.id"), nullable=False),
                     Column("data", JSON, nullable=False), Column("validated", Integer, nullable=False),
                     Column("active", Integer, nullable=False))
declarations = Table("declaration_lines", metadata, Column("id", Integer, primary_key=True),
                     Column("org", ForeignKey("organizations.id"), nullable=False),
                     Column("revision", Integer, nullable=False),
                     Column("source", ForeignKey("import_rows.id")), Column("data", JSON, nullable=False),
                     Column("deleted", Integer, nullable=False, default=0))
policies = Table("rule_sets", metadata, Column("org", ForeignKey("organizations.id"), primary_key=True),
                 Column("year", Integer, primary_key=True), Column("data", JSON, nullable=False))
runs = Table("calculation_runs", metadata, Column("id", Integer, primary_key=True),
             Column("org", ForeignKey("organizations.id"), nullable=False),
             Column("year", Integer, nullable=False), Column("time", String, nullable=False),
             Column("data", JSON, nullable=False))
submissions = Table("submission_snapshots", metadata, Column("id", Integer, primary_key=True),
                    Column("org", ForeignKey("organizations.id"), nullable=False),
                    Column("first_run", ForeignKey("calculation_runs.id"), nullable=False),
                    Column("second_run", ForeignKey("calculation_runs.id"), nullable=False),
                    Column("sheet", String, nullable=False), Column("data", LargeBinary, nullable=False),
                    UniqueConstraint("org", "first_run", "second_run", "sheet"))
audit = Table("audit_logs", metadata, Column("id", Integer, primary_key=True),
              Column("org", ForeignKey("organizations.id"), nullable=False),
              Column("time", String, nullable=False), Column("actor", String, nullable=False),
              Column("entity", String, nullable=False), Column("target", Integer, nullable=False),
              Column("reason", String, nullable=False), Column("before", JSON), Column("after", JSON))


def now():
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def engine_for(path=None):
    if path is None:
        data_dir = (Path(sys.executable).parent / "data" if getattr(sys, "frozen", False)
                    else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EPMS" / "data")
        path = os.environ.get("EPMS_DB") or data_dir / "epms.db"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(path.resolve())), connect_args={"timeout": 20})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")).replace("%", "%%"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine


def records(conn, table, org):
    query = table.select().where(table.c.org == org).order_by(table.c.id)
    rows = []
    if table is declarations:
        query = query.where(table.c.deleted == 0)
    for item in conn.execute(query):
        row = dict(item.data, id=item.id, revision=item.revision)
        if table is declarations:
            row["source"] = item.source
            if "line" in row:
                row["line"] = format_line_number(row["line"])
        rows.append(row)
    return rows


def log(conn, org, actor, entity, target, reason, before, after):
    conn.execute(audit.insert().values(org=org, actor=actor, entity=entity, target=target,
                                      reason=reason, time=now(), before=before, after=after))
