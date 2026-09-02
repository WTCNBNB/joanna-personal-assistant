from __future__ import annotations

from joanna.core.memory import JoannaMemory
from joanna.core.schema import (
    Correction,
    EvolutionProposal,
    EvolutionStatus,
    ExperienceEvent,
)


def proposals_from_correction(
    correction: Correction,
    events: list[ExperienceEvent],
) -> list[EvolutionProposal]:
    # v0.2 compatibility: corrections are preserved as feedback evidence.
    # They no longer auto-create applied tuning proposals.
    return []


def save_evolution_proposals(memory: JoannaMemory, proposals: list[EvolutionProposal]) -> None:
    for proposal in proposals:
        memory.upsert_evolution_proposal(proposal)


def approve_proposal(memory: JoannaMemory, proposal_id: str) -> EvolutionProposal:
    proposal = memory.get_evolution_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"evolution proposal not found: {proposal_id}")
    memory.set_evolution_status(proposal_id, EvolutionStatus.APPLIED)
    updated = memory.get_evolution_proposal(proposal_id)
    if not updated:
        raise ValueError(f"evolution proposal not found after approve: {proposal_id}")
    return updated


def reject_proposal(memory: JoannaMemory, proposal_id: str) -> EvolutionProposal:
    proposal = memory.get_evolution_proposal(proposal_id)
    if not proposal:
        raise ValueError(f"evolution proposal not found: {proposal_id}")
    memory.set_evolution_status(proposal_id, EvolutionStatus.REJECTED)
    updated = memory.get_evolution_proposal(proposal_id)
    if not updated:
        raise ValueError(f"evolution proposal not found after reject: {proposal_id}")
    return updated

