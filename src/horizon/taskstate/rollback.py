"""Rollback rules (PROJECT.md §8): restore, feed the failure reason back, enforce
the retry limit, and escalate to a human after the limit.

Pure functions over TaskState; the service saves the task.
"""

from __future__ import annotations

from horizon.models import Checkpoint, FailureEntry, TaskState
from horizon.taskstate.checkpoints import restore_steps

MAX_CHECKPOINTS = 20  # kept per task, plus the rollback target


def attach_checkpoint(task: TaskState, ckpt: Checkpoint) -> None:
    task.checkpoints.append(ckpt)
    if len(task.checkpoints) > MAX_CHECKPOINTS:
        keep = task.checkpoints[-MAX_CHECKPOINTS:]
        target = find_checkpoint(task, task.rollback_to)
        task.checkpoints = ([target] if target and target not in keep else []) + keep


def find_checkpoint(task: TaskState, checkpoint_id: str | None) -> Checkpoint | None:
    return next((c for c in task.checkpoints if c.id == checkpoint_id), None) if checkpoint_id else None


def checkpoint_for_decision(task: TaskState, recall_id: str | None) -> Checkpoint | None:
    """The checkpoint taken by the recall that fed this decision, else the latest one if it is newer than
    the previous decision. An older one predates work that already passed, so restoring it would undo that."""
    if recall_id:
        match = next((c for c in reversed(task.checkpoints) if c.recall_id == recall_id), None)
        if match:
            return match
    if not task.checkpoints:
        return None
    latest = task.checkpoints[-1]
    return latest if not task.decisions or latest.created_at > task.decisions[-1].at else None


def apply_outcome(task: TaskState, *, failed: bool, chosen: str, reason: str,
                  checkpoint: Checkpoint | None, max_attempts: int) -> dict | None:
    """Update the retry state for one outcome. Returns the rollback or escalation for a failure."""
    task.restore_check = None
    if not failed:
        task.attempts, task.failures, task.rollback_to = 0, [], None
        return None

    task.attempts += 1
    if task.attempts == 1:
        # Later attempts in the streak start from the same known-good state, even if the host
        # never restored and a later checkpoint captured a broken tree.
        task.rollback_to = checkpoint.id if checkpoint else None
    task.failures.append(FailureEntry(attempt=task.attempts, chosen=chosen, reason=reason,
                                      checkpoint_id=checkpoint.id if checkpoint else None))
    target = find_checkpoint(task, task.rollback_to)
    restore = restore_steps(target)
    failures = [{"attempt": f.attempt, "chosen": f.chosen, "reason": f.reason} for f in task.failures]

    if task.attempts >= max_attempts:
        task.status = "escalated"
        return {
            "action": "escalate",
            "attempt": task.attempts,
            "max_attempts": max_attempts,
            "failures": failures,
            "restore": restore,
            "next": ("Retry limit reached: stop retrying. Restore the checkpoint, then show the user these "
                     "failures and ask how to proceed. Pass their answer to recall_context as human_guidance."),
        }
    if target and target.git:
        task.restore_check = target.git.commit
    return {
        "action": "rollback",
        "attempt": task.attempts,
        "max_attempts": max_attempts,
        "failure_reason": reason,
        "failures": failures,
        "restore": restore,
        "next": ("Run restore.git now, before any other edit, even if you plan to rewrite the same code: "
                 "the retry must start from the known-good state. Then call recall_context and retry with an "
                 "approach not listed in failures."),
    }


def resume(task: TaskState) -> None:
    """A human answered the escalation: start a fresh streak."""
    task.status = "active"
    task.attempts, task.failures, task.rollback_to, task.restore_check = 0, [], None, None


def retry_state(task: TaskState, max_attempts: int) -> dict | None:
    """What the decision step needs to know about the current failure streak."""
    if not task.attempts and task.status != "escalated":
        return None
    return {
        "attempt": task.attempts,
        "max_attempts": max_attempts,
        "escalated": task.status == "escalated",
        "previous_failures": [{"attempt": f.attempt, "chosen": f.chosen, "reason": f.reason}
                              for f in task.failures],
        "rollback_to": task.rollback_to,
    }
