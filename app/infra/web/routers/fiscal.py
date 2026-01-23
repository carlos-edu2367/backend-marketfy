import uuid
import shutil
import os
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db
from infra.web.dependencies import (
    get_fiscal_service, get_current_user, PermissionChecker
)
from application.services.fiscal_service import FiscalService
from application.dtos import FiscalConfigCreateDTO, FiscalConfigResponseDTO, InvoiceResponseDTO
from domain.shared import BusinessRuleException

router = APIRouter()

# Pasta local para salvar certificados (em prod seria S3/GCS)
UPLOAD_DIR = "uploads/certificates"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.get("/{market_id}/config", response_model=FiscalConfigResponseDTO)
async def get_config(
    market_id: uuid.UUID,
    service: FiscalService = Depends(get_fiscal_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    config = await service.get_config(market_id)
    if not config:
        # Retorna vazio mas 200 OK para o front saber que não tem config
        return FiscalConfigResponseDTO(environment="homologacao", csc_id="", has_certificate=False, default_ncm=None)
    return config

@router.post("/{market_id}/config")
async def save_config(
    market_id: uuid.UUID,
    csc_token: str = Form(...),
    csc_id: str = Form(...),
    environment: str = Form(...),
    default_ncm: str = Form(None),
    certificate_password: str = Form(...),
    certificate_file: UploadFile = File(None), # Opcional se já tiver
    service: FiscalService = Depends(get_fiscal_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint multipart/form-data para receber o arquivo .pfx e os dados.
    """
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    
    cert_path = None
    if certificate_file:
        if not certificate_file.filename.endswith(".pfx"):
             raise HTTPException(status_code=400, detail="O certificado deve ser um arquivo .pfx")
        
        # Salva arquivo
        safe_filename = f"{market_id}_{certificate_file.filename}"
        cert_path = os.path.join(UPLOAD_DIR, safe_filename)
        with open(cert_path, "wb") as buffer:
            shutil.copyfileobj(certificate_file.file, buffer)

    dto = FiscalConfigCreateDTO(
        csc_token=csc_token,
        csc_id=csc_id,
        environment=environment,
        default_ncm=default_ncm,
        certificate_password=certificate_password
    )

    try:
        return await service.save_config(market_id, dto, cert_path)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{market_id}/sales/{sale_id}/emit", response_model=InvoiceResponseDTO)
async def emit_invoice(
    market_id: uuid.UUID,
    sale_id: uuid.UUID,
    service: FiscalService = Depends(get_fiscal_service),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Tenta emitir a NFC-e para uma venda existente.
    """
    await PermissionChecker.verify_market_ownership(market_id, current_user.id, db)
    try:
        return await service.emit_invoice(market_id, sale_id)
    except BusinessRuleException as e:
        raise HTTPException(status_code=400, detail=str(e))