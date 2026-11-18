from fastapi import APIRouter, Depends
from src.config.database import db_session
from src.utils.security import current, writer
from src.schemas.migration import MigrationPlanInput
from src.services import migration_plans as service

router = APIRouter()


@router.get('/migration_plans')
def listing(db=Depends(db_session), user=Depends(current)):
    return service.listing(db)


@router.post('/migration_plans', status_code=201)
def create(data: MigrationPlanInput, db=Depends(db_session), user=Depends(writer)):
    return service.create(db, data, user)


@router.put('/migration_plans/{ident}')
def update(ident: int, data: MigrationPlanInput, db=Depends(db_session), user=Depends(writer)):
    return service.update(db, ident, data, user)
