# Concepts

## How Execution Works

1. **Graph definition** — `build_workflow()` returns a `networkx.DiGraph` where each node has a `method` attribute pointing to a method on the `Process` class, and edges carry boolean `condition` values.
2. **Compilation** — `compile_workflow()` validates the graph, extracts loopback edges (cycles), and ensures the remaining graph is a DAG.
3. **Execution** — `WorkflowExecutor` traverses the compiled graph using a `ThreadPoolExecutor`: nodes whose predecessors have all completed are scheduled concurrently; edge conditions are evaluated to route execution; loopbacks are triggered when a node returns `True` to repeat a section.
4. **Events** — The executor emits `WorkflowExecutionEvent` objects for each state transition, consumed by the API to provide real-time status and log streaming.
5. **Result** — A `WorkflowResult` is returned with the final state, per-node results, runtime, and any errors.

## Physical Units

Use `ChemUnitQuantity` from `chemunited-quantities` for values that carry SI units:

```python
from chemunited_quantities import ChemUnitQuantity

volume = ChemUnitQuantity.parse("500 ul")
double = volume * 2          # 1000 ul
in_ml  = volume.to("ml")     # 0.5 ml
```

Units are validated and propagated through arithmetic. Import
`ChemQuantityValidator` directly from `chemunited_quantities` for Pydantic models.

## Dry-Run Mode

Pass `dry_run=True` to `Platform` to suppress all HTTP calls — useful for testing graph logic without hardware:

```python
platform = Platform.from_connectivity(path, dry_run=True)
```

---

[← Back to README](../README.md)
