from __future__ import annotations

"""Auditable orchestration for the paper delivery workflow.

The orchestrator deliberately does not generate manuscript content.  It plans
and records deterministic tool work, then decides whether the result may move
to formal delivery or must return to the author for confirmation.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass
class PlanStep:
    id: str
    tool: str
    objective: str
    status: str = "pending"
    summary: str = ""
    evidence: list[str] = field(default_factory=list)


class PaperDeliveryAgent:
    """Keeps a run-level plan, decisions and tool evidence in one JSON trace."""

    def __init__(self, run_dir: Path, source_name: str, target_name: str) -> None:
        self.run_dir = run_dir
        self.trace_path = run_dir / "agent_trace.json"
        self.trace: dict[str, Any] = {
            "schema_version": "1.0",
            "agent": "PaperFormat Delivery Agent",
            "goal": "Produce a traceable, verifiable manuscript delivery package without inventing content.",
            "source": source_name,
            "target": target_name or "unspecified",
            "started_at": self._now(),
            "status": "running",
            "steps": [
                asdict(PlanStep("intake", "prepare_project", "Parse the source and establish a protected workspace.")),
                asdict(PlanStep("rules", "rule_resolver", "Load only evidenced formatting and citation rules.")),
                asdict(PlanStep("repair", "analyze_and_repair", "Apply reversible formatting repairs; never author research content.")),
                asdict(PlanStep("assets", "asset_matcher", "Match figures and tables exactly, then collect unresolved items.")),
                asdict(PlanStep("verify", "latex_compiler", "Compile and record the verification result.")),
                asdict(PlanStep("delivery", "delivery_gate", "Allow formal delivery only when blocking items are resolved.")),
            ],
            "decisions": [],
        }
        self.persist()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def complete_step(self, step_id: str, summary: str, evidence: list[str] | None = None) -> None:
        for step in self.trace["steps"]:
            if step["id"] == step_id:
                step.update({"status": "completed", "summary": summary, "evidence": evidence or []})
                self.persist()
                return
        raise ValueError(f"Unknown agent plan step: {step_id}")

    def block_step(self, step_id: str, summary: str, evidence: list[str] | None = None) -> None:
        for step in self.trace["steps"]:
            if step["id"] == step_id:
                step.update({"status": "blocked", "summary": summary, "evidence": evidence or []})
                self.persist()
                return
        raise ValueError(f"Unknown agent plan step: {step_id}")

    def decide(self, decision: str, reason: str) -> None:
        self.trace["decisions"].append({"at": self._now(), "decision": decision, "reason": reason})
        self.persist()

    def finish(self, blockers: list[str], compile_status: str) -> None:
        if blockers:
            self.trace["status"] = "needs_confirmation"
            self.decide("Block formal delivery", f"{len(blockers)} unresolved item(s) require author confirmation.")
            self.block_step("delivery", "Formal delivery is blocked pending author confirmation.", blockers)
        elif compile_status == "失败":
            self.trace["status"] = "verification_failed"
            self.decide("Hold formal delivery", "LaTeX compilation failed; inspect the compile log before publishing.")
            self.block_step("delivery", "Formal delivery is held because compilation did not pass.")
        else:
            self.trace["status"] = "ready_for_review"
            self.decide("Request human review", "All deterministic checks passed; the user must review the preview before formal export.")
            self.complete_step("delivery", "Ready for human review and formal export.")
        self.trace["finished_at"] = self._now()
        self.persist()

    def mark_formal_export(self, success: bool, notes: list[str]) -> None:
        self.trace["formal_export"] = {"at": self._now(), "success": success, "notes": notes}
        self.trace["status"] = "delivered" if success else "verification_failed"
        self.persist()

    def persist(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path.write_text(json.dumps(self.trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.trace_path

    @classmethod
    def load(cls, run_dir: Path) -> "PaperDeliveryAgent | None":
        path = run_dir / "agent_trace.json"
        if not path.exists():
            return None
        agent = cls.__new__(cls)
        agent.run_dir = run_dir
        agent.trace_path = path
        agent.trace = json.loads(path.read_text(encoding="utf-8"))
        return agent

    def to_markdown(self) -> str:
        labels = {
            "pending": "待执行", "completed": "完成", "blocked": "已阻断",
            "needs_confirmation": "等待确认", "ready_for_review": "等待审阅",
            "verification_failed": "验证失败", "delivered": "已交付", "running": "执行中",
        }
        lines = ["### Agent 执行轨迹", "", f"- 当前状态：`{labels.get(self.trace['status'], self.trace['status'])}`"]
        for step in self.trace["steps"]:
            lines.append(f"- `{labels.get(step['status'], step['status'])}` **{step['objective']}**：{step['summary'] or '等待执行'}")
        if self.trace["decisions"]:
            latest = self.trace["decisions"][-1]
            lines.extend(["", f"**最新决策：** {latest['decision']}。{latest['reason']}"])
        return "\n".join(lines)
