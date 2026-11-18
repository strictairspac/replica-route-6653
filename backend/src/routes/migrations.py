import json
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from src.config.database import db_session
from src.utils.security import current, writer
from src.schemas.migration import MigrationRunInput
from src.services import migrations as service

router = APIRouter()


@router.get('/migrations')
def listing(db=Depends(db_session), user=Depends(current)):
    return service.listing(db)


@router.post('/migrations', status_code=201)
def create(data: MigrationRunInput, db=Depends(db_session), user=Depends(writer)):
    return service.create(db, data, user)


@router.get('/migrations/{ident}')
def detail(ident: int, db=Depends(db_session), user=Depends(current)):
    return service.detail(db, ident)


@router.get('/migrations/{ident}/export')
def export(ident: int, db=Depends(db_session), user=Depends(current)):
    return Response(
        json.dumps(service.detail(db, ident), ensure_ascii=False, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="migration-{ident}.json"'})
