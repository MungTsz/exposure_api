"""PUT /place-labels/{label_id} (create or update) and DELETE
/place-labels/{label_id}. Labels are shared by every pid when stays are
classified (app/io_types/place_labels.py); there is deliberately no
endpoint that lists them, because Home circles are sensitive."""

from fastapi import APIRouter, HTTPException, Path, Query, Response

from app.api.schemas import PlaceLabelIn, PlaceLabelOut
from app.config.constants import PLACE_LABEL_ID_MAX_LENGTH
from app.db import place_label_repository
from app.db.place_label_repository import DeleteOutcome, UpsertOutcome

router = APIRouter(tags=["place labels"])

_NOT_OWNER = "label_id belongs to another contributor_id"

LabelId = Path(..., min_length=1, max_length=PLACE_LABEL_ID_MAX_LENGTH, description="client-generated, unique per label")


@router.put(
    "/place-labels/{label_id}",
    response_model=PlaceLabelOut,
    responses={
        201: {"description": "created", "model": PlaceLabelOut},
        403: {"description": _NOT_OWNER},
    },
)
def put_place_label(body: PlaceLabelIn, response: Response, label_id: str = LabelId) -> PlaceLabelOut:
    """Create (201) or update (200) a label. Idempotent: if the stored label
    is as new or newer than `updated_at`, nothing changes and the stored
    label is returned with 200."""
    outcome, stored = place_label_repository.upsert(label_id, **body.model_dump())
    if outcome == UpsertOutcome.FORBIDDEN:
        raise HTTPException(403, _NOT_OWNER)
    if outcome == UpsertOutcome.CREATED:
        response.status_code = 201
    return PlaceLabelOut(**stored)


@router.delete(
    "/place-labels/{label_id}",
    status_code=204,
    responses={403: {"description": _NOT_OWNER}, 404: {"description": "unknown label_id"}},
)
def delete_place_label(
    label_id: str = LabelId,
    contributor_id: str = Query(..., min_length=1, max_length=PLACE_LABEL_ID_MAX_LENGTH),
) -> Response:
    outcome = place_label_repository.delete(label_id, contributor_id)
    if outcome == DeleteOutcome.NOT_FOUND:
        raise HTTPException(404, "unknown label_id")
    if outcome == DeleteOutcome.FORBIDDEN:
        raise HTTPException(403, _NOT_OWNER)
    return Response(status_code=204)
