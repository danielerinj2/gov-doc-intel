#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.infra.repositories import Repository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build curated training dataset from correction-gated records")
    parser.add_argument("--tenant-id", required=True, help="Tenant/department identifier")
    parser.add_argument(
        "--output",
        default="artifacts/training/curated_training.jsonl",
        help="Output JSONL path",
    )
    parser.add_argument(
        "--include-pending-qa",
        action="store_true",
        help="Include PENDING_QA records for analysis-only dataset",
    )
    parser.add_argument(
        "--approve-gates",
        action="store_true",
        help="Promote HIGH_CONFIDENCE gates to TRAINING_APPROVED after export",
    )
    return parser.parse_args()


def build_example(
    *,
    tenant_id: str,
    document: dict[str, Any],
    record: dict[str, Any] | None,
    correction_event: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    wrapped = dict((record or {}).get("record") or {})
    doc_record = dict(wrapped.get("document_record") or {})
    extraction_out = dict(doc_record.get("extraction_output") or {})
    validation_out = dict(doc_record.get("validation_output") or {})
    fraud_out = dict(doc_record.get("fraud_risk_output") or {})
    explainability = dict(doc_record.get("explainability") or {})
    template_ref = dict(doc_record.get("template_definition_ref") or {})

    return {
        "tenant_id": tenant_id,
        "document_id": correction_event.get("document_id"),
        "job_id": (record or {}).get("job_id"),
        "state": document.get("state"),
        "decision": document.get("decision"),
        "template_id": template_ref.get("template_id"),
        "template_version": template_ref.get("template_version"),
        "field_name": correction_event.get("field_name"),
        "old_value": correction_event.get("old_value"),
        "new_value": correction_event.get("new_value"),
        "reason": correction_event.get("reason"),
        "officer_id": correction_event.get("officer_id"),
        "correction_event_id": correction_event.get("id"),
        "gate_id": gate.get("id"),
        "gate_status": gate.get("status"),
        "gate_notes": gate.get("notes", []),
        "created_at": correction_event.get("created_at"),
        "record_snapshot": {
            "extraction_output": extraction_out,
            "validation_output": validation_out,
            "fraud_risk_output": fraud_out,
            "explainability": explainability,
        },
    }


def main() -> None:
    args = parse_args()
    repo = Repository()

    eligible_statuses = ["HIGH_CONFIDENCE", "TRAINING_APPROVED"]
    if args.include_pending_qa:
        eligible_statuses.append("PENDING_QA")

    gates: list[dict[str, Any]] = []
    for status in eligible_statuses:
        gates.extend(repo.list_correction_gate_records(args.tenant_id, status=status))

    curated_rows: list[dict[str, Any]] = []
    promoted: list[str] = []
    skipped = 0

    for gate in gates:
        correction_event_id = str(gate.get("correction_event_id") or "")
        if not correction_event_id:
            skipped += 1
            continue

        correction_event = repo.get_correction_event(correction_event_id)
        if not correction_event or correction_event.get("tenant_id") != args.tenant_id:
            skipped += 1
            continue

        document_id = str(correction_event.get("document_id") or "")
        if not document_id:
            skipped += 1
            continue

        doc = repo.get_document(document_id, tenant_id=args.tenant_id)
        if not doc:
            skipped += 1
            continue

        latest_record = repo.get_latest_document_record(args.tenant_id, document_id)
        curated_rows.append(
            build_example(
                tenant_id=args.tenant_id,
                document=doc,
                record=latest_record,
                correction_event=correction_event,
                gate=gate,
            )
        )

        if args.approve_gates and str(gate.get("status")) == "HIGH_CONFIDENCE":
            notes = list(gate.get("notes") or [])
            if "PROMOTED_TO_TRAINING" not in notes:
                notes.append("PROMOTED_TO_TRAINING")
            updated = repo.update_correction_gate_record(
                str(gate.get("id")),
                status="TRAINING_APPROVED",
                notes=notes,
                validated_at=datetime.now(timezone.utc).isoformat(),
            )
            if updated:
                promoted.append(str(gate.get("id")))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in curated_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    print(
        json.dumps(
            {
                "tenant_id": args.tenant_id,
                "output": str(output_path),
                "rows": len(curated_rows),
                "gates_scanned": len(gates),
                "skipped": skipped,
                "promoted_gates": len(promoted),
                "promoted_gate_ids": promoted[:20],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
