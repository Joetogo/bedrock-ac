import boto3
import pytest
from moto import mock_aws

import store

TABLE = "web-console-test"


@pytest.fixture()
def table(monkeypatch):
    with mock_aws():
        res = boto3.resource("dynamodb", region_name="us-east-1")
        res.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                       {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                  {"AttributeName": "SK", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("TABLE_NAME", TABLE)
        yield res.Table(TABLE)


def test_thread_roundtrip_and_message_order(table):
    store.upsert_thread("u1", "c1", "List rooms", "2026-07-08T00:00:00.000Z", table=table)
    store.put_message("u1", "c1", "user", "List rooms", "2026-07-08T00:00:01.000Z", table=table)
    store.put_message("u1", "c1", "assistant", "6 rooms", "2026-07-08T00:00:02.000Z", table=table)

    msgs = store.get_messages("u1", "c1", table=table)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["text"] == "List rooms"


def test_list_threads_newest_first_and_excludes_messages(table):
    store.upsert_thread("u1", "c1", "First", "2026-07-08T00:00:00.000Z", table=table)
    store.upsert_thread("u1", "c2", "Second", "2026-07-08T01:00:00.000Z", table=table)
    store.put_message("u1", "c1", "user", "hi", "2026-07-08T00:00:05.000Z", table=table)

    threads = store.list_threads("u1", table=table)
    assert [t["id"] for t in threads] == ["c2", "c1"]           # newest updatedAt first
    assert all("MSG" not in str(t) for t in threads)             # no message rows leaked


def test_upsert_thread_preserves_first_title(table):
    store.upsert_thread("u1", "c1", "Original", "2026-07-08T00:00:00.000Z", table=table)
    store.upsert_thread("u1", "c1", "", "2026-07-08T02:00:00.000Z", table=table)
    threads = store.list_threads("u1", table=table)
    assert threads[0]["title"] == "Original"
    assert threads[0]["updatedAt"] == "2026-07-08T02:00:00.000Z"


def test_delete_thread_removes_metadata_and_messages(table):
    store.upsert_thread("u1", "c1", "T", "2026-07-08T00:00:00.000Z", table=table)
    store.put_message("u1", "c1", "user", "hi", "2026-07-08T00:00:01.000Z", table=table)
    store.delete_thread("u1", "c1", table=table)
    assert store.list_threads("u1", table=table) == []
    assert store.get_messages("u1", "c1", table=table) == []


def test_delete_thread_does_not_delete_prefix_sibling(table):
    store.upsert_thread("u1", "c1", "First", "2026-07-08T00:00:00.000Z", table=table)
    store.put_message("u1", "c1", "user", "hi c1", "2026-07-08T00:00:01.000Z", table=table)
    store.upsert_thread("u1", "c10", "Tenth", "2026-07-08T01:00:00.000Z", table=table)
    store.put_message("u1", "c10", "user", "hi c10", "2026-07-08T01:00:01.000Z", table=table)

    store.delete_thread("u1", "c1", table=table)

    threads = store.list_threads("u1", table=table)
    assert [t["id"] for t in threads] == ["c10"]
    assert store.get_messages("u1", "c1", table=table) == []
    msgs = store.get_messages("u1", "c10", table=table)
    assert [m["text"] for m in msgs] == ["hi c10"]


def test_users_are_isolated(table):
    store.upsert_thread("u1", "c1", "mine", "2026-07-08T00:00:00.000Z", table=table)
    assert store.list_threads("u2", table=table) == []
