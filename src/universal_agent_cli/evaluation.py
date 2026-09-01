"""CLI evaluation dispatch: thin re-export of the kernel implementation."""

from __future__ import annotations

from universal_agent.evaluation.dispatch import (
    DispatchExit,
    _dispatch_eval,
    _dispatch_eval_replay,
    _evaluation_dataset_body,
    evaluation_dataset_verification_body,
)

__all__ = [
    "DispatchExit",
    "_dispatch_eval",
    "_dispatch_eval_replay",
    "_evaluation_dataset_body",
    "evaluation_dataset_verification_body",
]
