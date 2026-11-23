# SPDX-Copyright: Copyright (c) Capital One Services, LLC
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Integration tests for IBM Db2 through the generic ``Database`` class.

These tests need a reachable Db2 server and are skipped otherwise. Configure
them with environment variables:

``LOCOPY_DB2_DSN``
    Full ``KEY=VALUE;`` connection string, for example
    ``DATABASE=testdb;HOSTNAME=localhost;PORT=50000;PROTOCOL=TCPIP;``
``LOCOPY_DB2_USER`` / ``LOCOPY_DB2_PASSWORD``
    Credentials (optional when they are already in the DSN).

A throwaway server can be started with Db2 Community Edition::

    docker run -d --name db2 --privileged -p 50000:50000 \\
        -e LICENSE=accept -e DB2INST1_PASSWORD=<password> -e DBNAME=testdb \\
        icr.io/db2_community/db2
"""

import datetime
import decimal
import os
import uuid

import locopy
import pandas as pd
import polars as pl
import pytest

ibm_db_dbi = pytest.importorskip("ibm_db_dbi")

DSN = os.environ.get("LOCOPY_DB2_DSN")
CREDS = {
    "dsn": DSN,
    "user": os.environ.get("LOCOPY_DB2_USER", ""),
    "password": os.environ.get("LOCOPY_DB2_PASSWORD", ""),
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="LOCOPY_DB2_DSN is not set"),
]


@pytest.fixture()
def table():
    """Create a uniquely named table and drop it afterwards."""
    name = f"LOCOPY_TEST_{uuid.uuid4().hex[:8].upper()}"
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(
            f"CREATE TABLE {name} ("
            "id INTEGER NOT NULL, "
            "big BIGINT, "
            "amount DECIMAL(10, 2), "
            "ratio DOUBLE, "
            "label VARCHAR(50), "
            "created DATE, "
            "updated TIMESTAMP)"
        )
    yield name
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(f"DROP TABLE {name}")


ROWS = [
    (
        1,
        9_007_199_254_740_993,
        decimal.Decimal("12.34"),
        0.5,
        "alpha",
        datetime.date(2026, 1, 2),
        datetime.datetime(2026, 1, 2, 3, 4, 5),
    ),
    (2, None, None, None, None, None, None),
]


def test_db2_select_single_row():
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute("SELECT 1 AS field_1, 'a' AS field_2 FROM SYSIBM.SYSDUMMY1")
        assert db.column_names() == ["field_1", "field_2"]
        df = db.to_dataframe()

    assert df.to_dict("records") == [{"field_1": 1, "field_2": "a"}]


def test_db2_select_multiple_rows_polars():
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(
            "SELECT 1 AS field_1 FROM SYSIBM.SYSDUMMY1 "
            "UNION ALL SELECT 2 FROM SYSIBM.SYSDUMMY1 ORDER BY 1"
        )
        df = db.to_dataframe(df_type="polars")

    assert isinstance(df, pl.DataFrame)
    assert df["field_1"].to_list() == [1, 2]


def test_db2_qmark_params_and_executemany(table):
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)",
            params=ROWS,
            many=True,
        )
        db.execute(f"SELECT * FROM {table} WHERE id = ?", params=(1,))
        rows = list(db.to_dict())

    assert rows == [
        {
            "id": 1,
            "big": 9_007_199_254_740_993,
            "amount": decimal.Decimal("12.34"),
            "ratio": 0.5,
            "label": "alpha",
            "created": datetime.date(2026, 1, 2),
            "updated": datetime.datetime(2026, 1, 2, 3, 4, 5),
        }
    ]


def test_db2_nulls_to_dataframe(table):
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)",
            params=ROWS,
            many=True,
        )
        db.execute(f"SELECT * FROM {table} ORDER BY id")
        df = db.to_dataframe()

    assert list(df.columns) == [
        "id",
        "big",
        "amount",
        "ratio",
        "label",
        "created",
        "updated",
    ]
    assert df["id"].tolist() == [1, 2]
    assert pd.isna(df.loc[1, "label"])


def test_db2_fetch_in_chunks(table):
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(
            f"INSERT INTO {table} (id) VALUES (?)",
            params=[(i,) for i in range(10)],
            many=True,
        )
        db.execute(f"SELECT id FROM {table} ORDER BY id")
        first = db.to_dataframe(size=4)
        second = db.to_dataframe(size=4)

    assert first["id"].tolist() == [0, 1, 2, 3]
    assert second["id"].tolist() == [4, 5, 6, 7]


def test_db2_commit_false_is_rolled_back(table):
    # ibm_db_dbi starts with autocommit off, so an uncommitted insert must
    # disappear when the connection closes.
    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(f"INSERT INTO {table} (id) VALUES (1)", commit=False)
        db.conn.rollback()

    with locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db:
        db.execute(f"SELECT COUNT(*) AS n FROM {table}")
        assert db.to_dataframe()["n"].tolist() == [0]


def test_db2_bad_sql_raises_dberror():
    with (
        locopy.Database(dbapi=ibm_db_dbi, **CREDS) as db,
        pytest.raises(locopy.errors.DBError),
    ):
        db.execute("SELECT * FROM LOCOPY_TABLE_THAT_DOES_NOT_EXIST")


def test_db2_bad_password_raises_dberror():
    creds = {**CREDS, "password": "definitely-wrong"}
    db = locopy.Database(dbapi=ibm_db_dbi, **creds)
    with pytest.raises(locopy.errors.DBError):
        db.connect()
