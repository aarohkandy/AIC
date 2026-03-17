from __future__ import annotations

from functools import lru_cache

from app.core.settings import Settings, get_settings
from app.services.compilers.cadquery_compiler import CadQueryCompiler
from app.services.design_service import DesignService
from app.services.executors.cadquery_executor import CadQueryExecutor
from app.services.gateway.model_gateway import ModelGateway
from app.services.planners.rule_based_planner import RuleBasedPlanner
from app.services.revision.revision_engine import RevisionEngine
from app.services.storage.file_store import FileStore
from app.services.validation.design_validator import DesignValidator
from app.services.validation.source_validator import SourceValidator


@lru_cache
def get_design_service() -> DesignService:
    settings: Settings = get_settings()
    store = FileStore(settings)
    validator = DesignValidator()
    planner = RuleBasedPlanner()
    gateway = ModelGateway(settings, planner, validator)
    compiler = CadQueryCompiler(SourceValidator())
    executor = CadQueryExecutor(settings)
    revision_engine = RevisionEngine()
    return DesignService(
        settings=settings,
        store=store,
        gateway=gateway,
        compiler=compiler,
        executor=executor,
        validator=validator,
        revision_engine=revision_engine,
    )

