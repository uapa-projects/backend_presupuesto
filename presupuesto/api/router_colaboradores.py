from ninja import Router
from ninja import NinjaAPI, Schema
from django.shortcuts import get_object_or_404
from typing import List
import uuid
from decimal import Decimal
from authentication import directorio

router = Router()

class ColaboradorOut(Schema):
    id: uuid.UUID
    first_name: str
    last_name: str
    email: str

    @staticmethod
    def resolve_full_name(obj):
        return obj.get_full_name()

@router.get("/list", response=List[ColaboradorOut])
def listar_colaboradores(request):
    # Usuarios activos con acceso a CEP en la plataforma (para que no aparezcan ex-empleados en la lista)
    lista = [directorio.Persona(u['id'], u) for u in directorio.buscar_usuarios(app='cep', limit=500)]
    return sorted(lista, key=lambda p: p.first_name)
