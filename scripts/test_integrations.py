#!/usr/bin/env python3
"""Validate the Neat and Graph tenant integrations directly, bypassing Bedrock
and the gateway. Exercises the same shared client code the Lambdas use, reading
the same Secrets Manager secrets.

  # set these so the shared clients know which secrets to read:
  $env:NEAT_SECRET_ARN  = "neat-graph-bedrock/neat-pulse"
  $env:GRAPH_SECRET_ARN = "neat-graph-bedrock/graph-app"
  $env:AWS_REGION       = "us-east-1"

  py scripts/test_integrations.py neat
  py scripts/test_integrations.py graph
  py scripts/test_integrations.py both
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# make src/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from _shared import clients  # noqa: E402


def hr(title: str) -> None:
    print("\n" + "=" * 60 + f"\n{title}\n" + "=" * 60)


def test_neat() -> bool:
    hr("NEAT — list rooms")
    try:
        rooms = clients.neat_get("/rooms")
        print(json.dumps(rooms, indent=2)[:2000])
        # try sensors on the first room if any
        items = rooms.get("rooms") or rooms.get("value") or rooms.get("data") or []
        if items:
            rid = items[0].get("id")
            if rid:
                hr(f"NEAT — sensors for room {rid}")
                sensors = clients.neat_get(f"/rooms/{rid}/sensor")
                print(json.dumps(sensors, indent=2)[:2000])
        else:
            print("\n(no rooms returned — check the org has rooms + a paid Pulse plan)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"\nNEAT FAILED: {type(e).__name__}: {e}")
        return False


def test_graph() -> bool:
    hr("GRAPH — acquire app-only token")
    try:
        tok = clients.graph_token()
        print(f"token acquired, length {len(tok)} (ok)")
    except Exception as e:  # noqa: BLE001
        print(f"\nGRAPH TOKEN FAILED: {type(e).__name__}: {e}")
        print("check tenant_id / client_id / client_secret in the graph-app secret")
        return False

    hr("GRAPH — list recent call records (last 24h)")
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        frm = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        recs = clients.graph_get(
            "/communications/callRecords",
            {"$filter": f"startDateTime ge {frm} and startDateTime le {to}", "$top": "5"},
        )
        n = len(recs.get("value", []))
        print(f"call records in window: {n}")
        print(json.dumps(recs, indent=2)[:2000])
        if n == 0:
            print("\n(no records — either no Teams calls in 24h, or the ~30min lag, "
                  "or CallRecords.Read.All app permission / admin consent missing)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"\nGRAPH CALLRECORDS FAILED: {type(e).__name__}: {e}")
        print("most likely: CallRecords.Read.All not granted as APPLICATION "
              "permission, or admin consent not given")
        return False


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    ok = True
    if which in ("neat", "both"):
        ok &= test_neat()
    if which in ("graph", "both"):
        ok &= test_graph()
    hr("RESULT")
    print("PASS" if ok else "FAIL — see errors above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()