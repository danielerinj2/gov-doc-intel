from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DRTargets:
    rpo_minutes: int = 5
    rto_minutes: int = 15


@dataclass(frozen=True)
class FailoverPlan:
    mode: str = "active-passive"
    primary_region: str = "region-a"
    secondary_region: str = "region-b"
    health_check_interval_seconds: int = 30


class DRService:
    def __init__(self) -> None:
        self.targets = DRTargets()
        self.plan = FailoverPlan()

    def describe(self) -> dict:
        return {
            "targets": {
                "RPO_minutes": self.targets.rpo_minutes,
                "RTO_minutes": self.targets.rto_minutes,
            },
            "failover_plan": {
                "mode": self.plan.mode,
                "primary_region": self.plan.primary_region,
                "secondary_region": self.plan.secondary_region,
                "health_check_interval_seconds": self.plan.health_check_interval_seconds,
                "resume_strategy": "resume from last committed event",
            },
        }
