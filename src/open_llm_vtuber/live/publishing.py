"""Safe draft/approval boundary for comments or posts.

This module deliberately has no platform client and no ``publish()`` method.
Creating a draft never publishes anything. A human operator must approve it;
a danmaku event or the character model can never mark it approved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

__all__ = ["PublishApprovalError", "PublishApprovalGate", "PublishDraft"]


class PublishApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishDraft:
    id: str
    platform: str
    kind: str
    content: str
    source_event_ids: tuple[str, ...] = ()
    status: str = "draft"
    approved_by: str = ""
    created_at_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "platform": self.platform,
            "kind": self.kind,
            "content": self.content,
            "source_event_ids": list(self.source_event_ids),
            "status": self.status,
            "approved_by": self.approved_by,
            "created_at_ms": self.created_at_ms,
        }


class PublishApprovalGate:
    """In-memory approval ledger; it cannot send anything to a platform."""

    _AUTOMATIC_ACTORS = frozenset({"", "system", "auto", "xiaojing", "model", "planner"})

    def create(
        self,
        *,
        platform: str,
        kind: str,
        content: str,
        source_event_ids: tuple[str, ...] = (),
        now_ms: int = 0,
    ) -> PublishDraft:
        text = content.strip()
        if not text:
            raise PublishApprovalError("publish draft cannot be empty")
        if kind not in {"comment", "post", "reply"}:
            raise PublishApprovalError(f"unsupported publish kind: {kind}")
        return PublishDraft(
            id=f"draft-{uuid.uuid4().hex}",
            platform=platform.strip() or "unknown",
            kind=kind,
            content=text[:2000],
            source_event_ids=source_event_ids,
            created_at_ms=now_ms,
        )

    def approve(self, draft: PublishDraft, *, by: str) -> PublishDraft:
        actor = by.strip()
        if actor.lower() in self._AUTOMATIC_ACTORS:
            raise PublishApprovalError("publish approval requires a human operator")
        if draft.status != "draft":
            raise PublishApprovalError(f"draft is not pending: {draft.status}")
        return replace(draft, status="approved", approved_by=actor)

    def reject(self, draft: PublishDraft, *, by: str, reason: str = "") -> PublishDraft:
        actor = by.strip() or "operator"
        if draft.status != "draft":
            raise PublishApprovalError(f"draft is not pending: {draft.status}")
        return replace(draft, status="rejected", approved_by=actor)

    @staticmethod
    def can_publish(draft: PublishDraft) -> bool:
        return draft.status == "approved" and bool(draft.approved_by.strip())
