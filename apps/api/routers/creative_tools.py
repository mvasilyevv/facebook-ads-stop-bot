# -*- coding: utf-8 -*-
"""FastAPI роутер для инструментов подготовки креативов."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from apps.api.schemas import (
    CreativeUniquifyFileSchema,
    CreativeUniquifyResponseSchema,
    OpenCreativeFolderRequestSchema,
    OpenCreativeFolderResponseSchema,
)
from core.creatives.folder_opener import CreativeFolderOpenError, open_generated_folder
from core.creatives.service import CreativeInput, CreativeValidationError, uniquify_creatives

router = APIRouter(prefix="/api", tags=["creative-tools"])


@router.post("/tools/creative-uniquify", response_model=CreativeUniquifyResponseSchema)
async def create_creative_uniquify_job(
    offer_name: str = Form(...),
    copies: int = Form(...),
    files: list[UploadFile] = File(...),
):
    """Уникализировать загруженные креативы и разложить их по папкам."""
    creatives: list[CreativeInput] = []
    for file in files:
        content = await file.read()
        creatives.append(CreativeInput(filename=file.filename or "creative", content=content))

    try:
        result = await uniquify_creatives(
            offer_name=offer_name,
            copies=copies,
            creatives=creatives,
        )
    except CreativeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CreativeUniquifyResponseSchema(
        root_dir=result.root_dir,
        iteration_dir=result.iteration_dir,
        iteration_name=result.iteration_name,
        creative_count=result.creative_count,
        copy_count=result.copy_count,
        files=[
            CreativeUniquifyFileSchema(
                copy_index=file.copy_index,
                source_name=file.source_name,
                output_name=file.output_name,
                output_path=file.output_path,
            )
            for file in result.files
        ],
    )


@router.post(
    "/tools/creative-uniquify/open-folder",
    response_model=OpenCreativeFolderResponseSchema,
)
async def open_creative_uniquify_folder(body: OpenCreativeFolderRequestSchema):
    """Открыть папку результата через локальную ОС."""
    try:
        await open_generated_folder(body.path)
    except CreativeFolderOpenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OpenCreativeFolderResponseSchema(ok=True)
