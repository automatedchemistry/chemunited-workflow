# Script file of project parameter
# Updated/Created on: 2026-05-06T13:20:38.425134+00:00
# Project name: complete
from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated
from loguru import logger
from time import sleep

from chemunited_workflow import (
    NodeExecutionContext,
    Process,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
    ChemUnitQuantity,
    ChemQuantityValidator,
)


class MainParameter(BaseModel):

    repetition_ractions: int = Field(
        title="Repetition of the reaction",
        description="Repetition of the reaction caaried out",
        default=1,
        ge=0,
        le=100,
        json_schema_extra={'group': 'General', 'editable': True, 'visible': True},
    )

    model_config = ConfigDict(frozen=True)
