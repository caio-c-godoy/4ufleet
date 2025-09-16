# app/tenant_scope.py
from flask import g
from sqlalchemy import event
from sqlalchemy.orm import with_loader_criteria
from app.extensions import db

class TenantScoped:
    """
    Mixin para modelos multi-tenant. 
    Só precisa herdar dele e ter a coluna tenant_id.
    """
    pass

def init_tenant_scope(models_using_mixin: list[type]):
    """
    Ativa o filtro automático de tenant para todos os SELECTs.
    Chame isso no factory/app.py depois de criar o app.
    """
    @event.listens_for(db.session, "do_orm_execute")
    def _add_tenant_filter(execute_state):
        # só em SELECTs normais
        if not execute_state.is_select:
            return
        ten = getattr(g, "tenant", None)
        if not ten:
            return
        # aplica com with_loader_criteria em cada modelo tenant-scoped
        for Model in models_using_mixin:
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(
                    Model,
                    lambda cls: cls.tenant_id == ten.id,
                    include_aliases=True,
                )
            )
