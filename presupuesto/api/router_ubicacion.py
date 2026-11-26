from ninja import Router
from ninja import NinjaAPI, Schema
from django.shortcuts import get_object_or_404
from typing import List
import uuid
from decimal import Decimal
from authentication import directorio

router = Router()

class SedeOut(Schema):
    id: uuid.UUID
    codigo: str
    nombre: str

@router.get("/list", response=List[SedeOut])
def listar_sedes(request):
    return directorio.sedes()  # recintos de la plataforma

