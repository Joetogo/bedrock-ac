"""Per-user conversation storage in a single DynamoDB table.

Keys:  PK = USER#<sub>
       SK = CONV#<id>                 (thread metadata: title, createdAt, updatedAt)
       SK = CONV#<id>#MSG#<ts>        (message: role, text, ts)
       SK = JOB#<jobId>              (async job: status, conversationId, answer, error, ts)
"""
from __future__ import annotations

import os

import boto3
from boto3.dynamodb.conditions import Key


def _table(table=None):
    if table is not None:
        return table
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _pk(sub: str) -> str:
    return f"USER#{sub}"


def upsert_thread(sub: str, conv_id: str, title: str, now: str, table=None) -> None:
    t = _table(table)
    # Set title only on first write; always bump updatedAt; set createdAt once.
    t.update_item(
        Key={"PK": _pk(sub), "SK": f"CONV#{conv_id}"},
        UpdateExpression=(
            "SET updatedAt = :now, "
            "createdAt = if_not_exists(createdAt, :now), "
            "title = if_not_exists(title, :title)"),
        ExpressionAttributeValues={":now": now, ":title": title or "New conversation"},
    )


def put_message(sub: str, conv_id: str, role: str, text: str, ts: str, table=None) -> None:
    t = _table(table)
    t.put_item(Item={
        "PK": _pk(sub), "SK": f"CONV#{conv_id}#MSG#{ts}",
        "role": role, "text": text, "ts": ts,
    })


def list_threads(sub: str, table=None) -> list[dict]:
    t = _table(table)
    resp = t.query(
        KeyConditionExpression=Key("PK").eq(_pk(sub)) & Key("SK").begins_with("CONV#"))
    threads = []
    for it in resp.get("Items", []):
        if "#MSG#" in it["SK"]:
            continue
        threads.append({
            "id": it["SK"].split("CONV#", 1)[1],
            "title": it.get("title", "New conversation"),
            "updatedAt": it.get("updatedAt", ""),
        })
    threads.sort(key=lambda x: x["updatedAt"], reverse=True)
    return threads


def get_messages(sub: str, conv_id: str, table=None) -> list[dict]:
    t = _table(table)
    resp = t.query(
        KeyConditionExpression=Key("PK").eq(_pk(sub))
        & Key("SK").begins_with(f"CONV#{conv_id}#MSG#"))
    return [{"role": it["role"], "text": it["text"], "ts": it["ts"]}
            for it in resp.get("Items", [])]


def put_job(sub: str, job_id: str, status: str, *, conv_id: str = "",
            answer: str = "", error: str = "", ts: str = "", table=None) -> None:
    t = _table(table)
    t.put_item(Item={
        "PK": _pk(sub), "SK": f"JOB#{job_id}",
        "status": status, "conversationId": conv_id,
        "answer": answer, "error": error, "ts": ts,
    })


def get_job(sub: str, job_id: str, table=None) -> dict | None:
    t = _table(table)
    resp = t.get_item(Key={"PK": _pk(sub), "SK": f"JOB#{job_id}"})
    it = resp.get("Item")
    if not it:
        return None
    return {
        "status": it.get("status", ""),
        "conversationId": it.get("conversationId", ""),
        "answer": it.get("answer", ""),
        "error": it.get("error", ""),
    }


def delete_thread(sub: str, conv_id: str, table=None) -> None:
    t = _table(table)
    resp = t.query(
        KeyConditionExpression=Key("PK").eq(_pk(sub))
        & Key("SK").begins_with(f"CONV#{conv_id}"))
    meta_sk = f"CONV#{conv_id}"
    msg_prefix = f"CONV#{conv_id}#MSG#"
    with t.batch_writer() as batch:
        for it in resp.get("Items", []):
            sk = it["SK"]
            if sk != meta_sk and not sk.startswith(msg_prefix):
                # Prefix collision with a different conversation (e.g. c1 vs c10); skip it.
                continue
            batch.delete_item(Key={"PK": it["PK"], "SK": sk})
