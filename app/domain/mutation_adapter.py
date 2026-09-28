"""Lossless adapter from deterministic resolutions to mutation bundles.

The rules engine remains responsible for checks, rolls, and compound outcomes.
This module only changes their representation so deterministic and JEV turns
share the mutation application and commit boundary.
"""

from app.domain.mutations import (
    AppendCanonicalEvent,
    ApplyDocumentMutation,
    ExecutionMetadata,
    InsertDocument,
    MutationBundle,
)
from app.domain.rules import Resolution


def resolution_to_mutation_bundle(
    resolution: Resolution, *, bundle_id: str
) -> MutationBundle:
    """Compile an accepted deterministic resolution without changing meaning."""
    if not resolution.accepted:
        raise ValueError("Only accepted resolutions can be compiled")

    mutations = [
        ApplyDocumentMutation(
            collection=mutation.collection,
            document_id=mutation.document_id,
            expected_version=mutation.expected_version,
            set_fields=dict(mutation.set_fields),
            inc_fields=dict(mutation.inc_fields),
            add_to_set_fields=dict(mutation.add_to_set_fields),
        )
        for mutation in resolution.mutations
    ]
    mutations.extend(
        InsertDocument(collection=insert.collection, document=dict(insert.document))
        for insert in resolution.inserts
    )
    mutations.extend(AppendCanonicalEvent(event=event) for event in resolution.events)

    return MutationBundle(
        bundle_id=bundle_id,
        action_description=resolution.outcome_summary or "Deterministic action",
        rationale="Compiled from the deterministic rules engine",
        draft_narration=resolution.outcome_summary,
        origin="DETERMINISTIC",
        execution=ExecutionMetadata(
            expected_turn=resolution.expected_turn,
            expected_campaign_version=resolution.expected_campaign_version,
            turn_id=resolution.turn_id,
            current_cell_id=resolution.current_cell_id,
            touched_entity_ids=list(resolution.touched_entity_ids),
            touched_cell_ids=list(resolution.touched_cell_ids),
            rejected_effects=list(resolution.rejected_effects),
        ),
        mutations=mutations,
    )
