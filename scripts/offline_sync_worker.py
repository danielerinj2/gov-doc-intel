#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.document_service import DocumentService
from app.services.offline_service import OfflineService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rate-controlled offline sync worker")
    parser.add_argument("--tenant-id", required=True, help="Tenant/department identifier")
    parser.add_argument("--officer-id", required=True, help="Officer/service account executing sync")
    parser.add_argument("--capacity-per-minute", type=int, default=50, help="Max documents synced in this run")
    parser.add_argument("--fetch-limit", type=int, default=500, help="Max pending docs to inspect")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    service = DocumentService()
    offline = OfflineService(service)

    current_officer = service.repo.get_officer(args.officer_id)
    if not current_officer or str(current_officer.get("tenant_id")) != args.tenant_id:
        service.register_officer(args.officer_id, args.tenant_id, "verifier")

    pending = service.repo.list_pending_offline_documents(args.tenant_id, limit=args.fetch_limit)
    pending_ids = [str(row.get("id")) for row in pending if row.get("id")]

    if not pending_ids:
        print(
            json.dumps(
                {
                    "tenant_id": args.tenant_id,
                    "pending": 0,
                    "synced": 0,
                    "failed": 0,
                    "queue_overflow": False,
                },
                indent=2,
            )
        )
        return

    backpressure = offline.apply_sync_backpressure(
        tenant_id=args.tenant_id,
        officer_id=args.officer_id,
        pending_document_ids=pending_ids,
        sync_capacity_per_minute=max(1, args.capacity_per_minute),
    )

    to_sync = pending_ids[: max(1, args.capacity_per_minute)]
    synced = 0
    failures: list[dict[str, Any]] = []
    for document_id in to_sync:
        try:
            offline.sync_offline_document(
                tenant_id=args.tenant_id,
                document_id=document_id,
                officer_id=args.officer_id,
            )
            synced += 1
        except Exception as exc:  # pragma: no cover
            failures.append({"document_id": document_id, "error": str(exc)})

    print(
        json.dumps(
            {
                "tenant_id": args.tenant_id,
                "pending": len(pending_ids),
                "attempted": len(to_sync),
                "synced": synced,
                "failed": len(failures),
                "queue_overflow": bool(backpressure.get("queue_overflow", False)),
                "backlog_size": int(backpressure.get("backlog_size", len(pending_ids))),
                "failures": failures[:10],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
