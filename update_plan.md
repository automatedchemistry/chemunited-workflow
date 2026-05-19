# Step 01 — Platform & ComponentClient Design Plan

## Overview

Introduce two new constructs to `chemunited-workflow`:

- **`BaseClient`** — foundational HTTP session wrapper
- **`ComponentClient`** — device-semantic layer, extends `BaseClient`
- **`Platform`** — registry of `ComponentClient` instances, lives as an attribute of `Process`

---

## Class Hierarchy

```
BaseClient          (HTTP mechanics: session, hooks, verb methods, _request)
    └── ComponentClient  (device semantics: concurrency guard, single-device contract)

Platform            (Mapping[str, ComponentClient] — the registry)
    └── Process.platform
```

---

## 1. `BaseClient`

Owns the HTTP mechanics. Responsible for session creation, URL building, verb methods,
response hooks, and the central `_request` method.

### Session

A plain `requests.Session` is sufficient. Thread safety is delegated to `ComponentClient`'s
concurrency guard (see §2), so no `threading.local()` is needed here.

```python
class BaseClient:
    def __init__(self, url: str | AnyHttpUrl) -> None:
        self.base_url = str(url).rstrip("/")
        self._session = self._make_session()

    def _make_session(self) -> requests.Session:
        """Override in subclasses to add auth, retry adapters, default headers, etc."""
        session = requests.Session()
        session.hooks["response"] = [
            self._log_response,      # log first — preserves evidence before raising
            self._raise_for_status,
        ]
        return session
```

### URL Building

```python
def _build_url(self, path: str) -> str:
    return f"{self.base_url}/{path.lstrip('/')}"
```

### `_request` method

All verbs funnel through `_request`. Placing the logic here means `ComponentClient`
can override a single method (to add the concurrency guard) and all verbs are
protected automatically.

```python
def _request(self, method: str, path: str, **kwargs) -> requests.Response:
    return self._session.request(method, self._build_url(path), **kwargs)
```

Step 04 (Dry-Run Mode) extends this method on `BaseClient` with a dry-run branch.
`ComponentClient` overrides it to add the concurrency guard and then calls
`super()._request(...)` — see §2 and Step 04 for the full call chain.

### Verb Methods

All verbs delegate to `_request`. Keyword-only arguments after `path` keep the API
explicit and consistent with `requests`.

```python
def get(self, path: str, *, params=None, **kwargs) -> requests.Response:
    return self._request("GET", path, params=params, **kwargs)

def put(self, path: str, *, params=None, json=None, **kwargs) -> requests.Response:
    return self._request("PUT", path, params=params, json=json, **kwargs)

def post(self, path: str, *, params=None, json=None, **kwargs) -> requests.Response:
    return self._request("POST", path, params=params, json=json, **kwargs)
```

### Response Hooks

`requests` exposes a single hook event: `"response"`. It fires after every response,
before the result is returned to the caller. The hook callable receives:

```python
def hook(response: requests.Response, *args, **kwargs) -> None: ...
```

**Hook order matters.** Place `_log_response` before `_raise_for_status` so that failed
responses are always logged before the exception unwinds the stack.

#### Where request data lives inside the hook

| What you pass to the verb | Where it appears in the hook |
|---|---|
| `params={"a": 1}` | `response.request.url` (encoded into query string) |
| `json={"a": 1}` | `response.request.body` (bytes) |
| `data="raw"` | `response.request.body` (string) |
| custom headers | `response.request.headers` |
| HTTP method | `response.request.method` |
| elapsed time | `response.elapsed` (timedelta) |

#### `_log_response` implementation

```python
def _log_response(self, response: requests.Response, *args, **kwargs) -> None:
    body = response.request.body
    if isinstance(body, bytes):
        try:
            body = body.decode()
        except UnicodeDecodeError:
            body = f"<binary {len(body)} bytes>"

    logger.debug(
        "{} {} body={} → {} in {:.0f}ms",
        response.request.method,
        response.request.url,        # includes query params
        body or "<no body>",
        response.status_code,
        response.elapsed.total_seconds() * 1000,
    )
```

---

## 2. `ComponentClient`

Extends `BaseClient` with a **concurrency guard**. A `ComponentClient` represents a
physical device — concurrent access from multiple workflow threads would be physically
incorrect and must fail loudly.

### Concurrency Guard

Uses `threading.Lock` with `blocking=False`. If a second thread attempts access while
the lock is held, a `ConcurrentClientAccessError` is raised immediately — no waiting,
no silent queuing.

The guard lives in `_request`, which overrides `BaseClient._request`. After acquiring
the lock, it calls `super()._request(...)` to continue the normal execution chain.
This structure ensures that the concurrency guard is always applied before any
downstream logic (including the dry-run branch added in Step 04).

```python
class ComponentClient(BaseClient):
    def __init__(self, url: str | AnyHttpUrl) -> None:
        super().__init__(url)
        self._access_lock = threading.Lock()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self._access_lock.acquire(blocking=False):
            raise ConcurrentClientAccessError(
                f"{type(self).__name__}(url={self.base_url!r}) was accessed from two threads "
                "simultaneously. Each ComponentClient must be used by only one workflow node "
                "at a time — check your workflow graph for nodes that share the same device."
            )
        try:
            return super()._request(method, path, **kwargs)  # → BaseClient._request
        finally:
            self._access_lock.release()
```

**Call chain:**
```
verb method (get/put/post)
    └── ComponentClient._request   — acquires lock
            └── BaseClient._request  — dry-run check (Step 04) → self._session.request
```

### Custom Exception

```python
class ConcurrentClientAccessError(RuntimeError):
    """Raised when a ComponentClient is accessed from more than one thread at a time."""
```

This is a first-class package exception, distinct from workflow errors and from
`requests` HTTP errors, so callers can handle each category independently.

---

## 3. `Platform`

A read-only registry that behaves like a dict. Implements `collections.abc.Mapping` to
get `get()`, `keys()`, `values()`, `items()`, `__contains__`, and `__eq__` for free.

```python
from collections.abc import Mapping

class Platform(Mapping[str, ComponentClient]):
    def __init__(self, components: dict[str, ComponentClient] | None = None) -> None:
        self._registry: dict[str, ComponentClient] = dict(components or {})

    def __getitem__(self, name: str) -> ComponentClient:
        try:
            return self._registry[name]
        except KeyError:
            raise KeyError(
                f"Component '{name}' is not registered. "
                f"Available: {list(self._registry)}"
            )

    def __iter__(self):
        return iter(self._registry)

    def __len__(self) -> int:
        return len(self._registry)

    def register(self, name: str, client: ComponentClient) -> None:
        self._registry[name] = client
```

---

## 4. Integration with `Process`

`Platform` is an optional argument to `Process.__init__`. Backward compatibility is
preserved — existing code that passes only `config` continues to work.

```python
class Process(ABC, Generic[ConfigT]):
    def __init__(self, config: ConfigT, platform: Platform | None = None) -> None:
        self.config = config
        self.platform = platform if platform is not None else Platform()
```

Node methods access devices via `ctx.process.platform`:

```python
def dose_reagent(self, ctx: NodeExecutionContext) -> bool:
    ctx.process.platform["pump"].put("dose", json={"volume_ml": 5.0})
    return True
```

---

## 5. Files to Create

| File | Content |
|---|---|
| `chemunited_workflow/clients.py` | `BaseClient`, `ComponentClient` |
| `chemunited_workflow/platform.py` | `Platform` |
| `chemunited_workflow/exceptions.py` | `ConcurrentClientAccessError` (and future exceptions) |

Update `chemunited_workflow/__init__.py` to export the new public symbols.

Also update `README.md`: the Quick Start example uses `node_id="IN"` and
`node_id="OUT"`. These must be changed to `node_id="start"` and `node_id="finish"`
to match the project-wide node ID convention established in Step 03.

---

## Design Decisions Summary

| Decision | Choice | Reason |
|---|---|---|
| Session thread safety | Plain session + concurrency guard | Guard makes `threading.local()` unnecessary |
| Concurrency policy | Fail immediately on contention | Devices cannot be physically shared |
| Platform protocol | `Mapping[str, ComponentClient]` | Full dict-like API with minimal implementation |
| Hierarchy | `BaseClient → ComponentClient` | Separates HTTP mechanics from device semantics |
| `_request` placement | `BaseClient` | Single override point; all verbs and subclasses protected automatically |
| `ComponentClient._request` delegation | `super()._request(...)` | Lock wraps the full downstream chain, including the dry-run branch added in Step 04 |
| Verb signatures | Keyword-only after `path` | Explicit, consistent with `requests` |
| Hook order | log → raise | Preserves evidence before exception unwinds |
| Process integration | Optional `platform` arg | Backward compatible |
| Exception strategy | Dedicated `exceptions.py` | Distinguishable from workflow and HTTP errors |


---


# Step 02 — `main_parameters` and `load_parameters` Design Plan

## Overview

Extend `Process` with:

- **`main_parameters`** — an optional `BaseModel` attribute that holds experiment-level
  parameters shared across all nodes (reagent volumes, set-points, …)
- **`process_index`** — an integer that distinguishes multiple instances of the same
  process class (e.g. `pump_process_0`, `pump_process_1`) when they coexist in a run
- **`load_parameters()`** — a method that hydrates both `main_parameters` and `config`
  from a `MainParameter` class file and an optional historic JSON file

No new top-level files are needed; all changes land in `chemunited_workflow/process.py`
and the public re-exports in `chemunited_workflow/__init__.py`.

---

## Conventions

### Directory layout expected at runtime

```
my_project/
├── protocols_hystoric/          # JSON snapshots created by the UI
│   └── parameters.json
└── protocols/                   # process modules and shared parameters
    ├── __init__.py              # exports PROCESSES and CONFIGS dicts
    ├── my_process.py            # subclass of Process
    └── main_parameters.py       # defines MainParameter(BaseModel)
```

`main_parameters.py` lives **inside** the `protocols/` package, next to each process
module. `parameters.json` lives in a `protocols_hystoric/` directory **one level above**
`protocols/` (i.e. at project root).

### JSON format

```json
{
    "main_parameter": {
        "reagent_volume_ml": 5.0
    },
    "my_process_0": {
        "flow_rate": 1.2
    },
    "my_process_1": {
        "flow_rate": 0.8
    }
}
```

The config key is `"{process_stem}_{process_index}"`, matching the stem of the process
module file and the `process_index` attribute of the instance.

---

## 1. `_load_class` (module-level private helper)

Dynamically imports a Python source file and returns the named class.

```python
def _load_class(path: Path, class_name: str) -> type:
    """Return *class_name* from the Python source file at *path*.

    Raises
    ------
    ImportError
        If the file cannot be loaded as a module.
    AttributeError
        If *class_name* is not defined in the module.
    """
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create a module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]
    return getattr(module, class_name)       # AttributeError if not found
```

**Why not return `None` when the class is missing?**
The caller already catches `AttributeError` and logs a clear message. A sentinel
`None` return would require an extra `if class_is_none` guard and could silently
swallow real `None` attribute values from pathological modules. Raising is simpler
and more Pythonic.

---

## 2. `Process` — new attributes

### `ConfigT` type constraint

`load_parameters` calls `type(self.config).model_validate(...)`, which requires that
`ConfigT` is a `pydantic.BaseModel` subclass. This must be enforced at the type level:

```python
from typing import TypeVar
from pydantic import BaseModel

ConfigT = TypeVar("ConfigT", bound=BaseModel)
```

`config` is a required positional argument — it is never `None` in practice, and the
type bound makes this explicit to the type checker.

```python
class Process(ABC, Generic[ConfigT]):
    def __init__(
        self,
        config: ConfigT,
        platform: Platform | None = None,
        process_index: int = 0,
    ) -> None:
        self.config = config
        self.platform = platform if platform is not None else Platform()
        self.process_index = process_index
        self.main_parameters: BaseModel | None = None
```

| Attribute | Type | Default | Purpose |
|---|---|---|---|
| `process_index` | `int` | `0` | Distinguishes multiple instances of the same process class |
| `main_parameters` | `BaseModel \| None` | `None` | Populated by `load_parameters()`; `None` until then |

Backward compatibility is fully preserved — existing code that passes only `config`
continues to work unchanged.

---

## 3. `Process.load_parameters()`

```python
def load_parameters(self, historic_file: str | None = None) -> bool:
    """Load main and process parameters from files next to the process module.

    Two-phase loading:

    **Phase 1 — MainParameter class**
    Looks for ``main_parameters.py`` in the same directory as the concrete
    process subclass.  If found, instantiates ``MainParameter`` with its
    defaults and assigns it to ``self.main_parameters``.

    **Phase 2 — historic JSON**
    Looks for *historic_file* (default: ``"parameters.json"``) in a
    ``protocols_hystoric/`` directory one level above the process directory.
    If the file exists, it overrides both ``self.main_parameters`` and
    ``self.config`` with validated values from the JSON.

    Returns
    -------
    bool
        ``True`` on success.  Also ``True`` when the historic file does not
        exist (that is normal; protocols are created through the UI).
        ``False`` on any class-loading, validation, or I/O error.
    """
```

### Phase 1 — load `MainParameter` class

```
main_parameters.py exists?
├── No  → skip (main_parameters stays None)
└── Yes → _load_class("MainParameter")
          ├── AttributeError / ImportError → log error, return False
          └── OK → issubclass(cls, BaseModel)?
                   ├── No  → log error, return False
                   └── Yes → MainParameter()
                             ├── ValidationError → log error, return False
                             └── OK → self.main_parameters = instance
```

**Why check `issubclass` before instantiation?**
The original sketch checked `isinstance(instance, BaseModel)` after the call.
Checking `issubclass` first avoids running a potentially side-effectful `__init__`
only to discard the result.

### Phase 2 — load historic JSON

```
historic file exists?
├── No  → return True   (not an error)
└── Yes → parse JSON
           ├── "main_parameter" key present?
           │   ├── self.main_parameters is None → log error, return False
           │   └── OK → type(self.main_parameters).model_validate(data["main_parameter"])
           └── "{stem}_{index}" key present?
               ├── No  → log error, return False
               └── OK → type(self.config).model_validate(data[key])
```

Caught exceptions: `OSError`, `json.JSONDecodeError`, `ValidationError`.

---

## 4. Full implementation

```python
import importlib.util
import inspect
import json
from pathlib import Path
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def _load_class(path: Path, class_name: str) -> type:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create a module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]
    return getattr(module, class_name)


class Process(ABC, Generic[ConfigT]):
    def __init__(
        self,
        config: ConfigT,
        platform: Platform | None = None,
        process_index: int = 0,
    ) -> None:
        self.config = config
        self.platform = platform if platform is not None else Platform()
        self.process_index = process_index
        self.main_parameters: BaseModel | None = None

    # -- abstract interface --------------------------------------------------

    @abstractmethod
    def build_workflow(self) -> nx.DiGraph: ...

    # -- parameter loading ---------------------------------------------------

    def load_parameters(self, historic_file: str | None = None) -> bool:
        process_path = Path(inspect.getfile(self.__class__))
        process_dir = process_path.parent
        process_name = process_path.stem

        # Phase 1: MainParameter class
        main_parameters_path = process_dir / "main_parameters.py"
        if main_parameters_path.exists():
            try:
                cls = _load_class(main_parameters_path, "MainParameter")
            except AttributeError:
                logger.error(
                    "Could not load parameters from {}: MainParameter class not found.",
                    main_parameters_path,
                )
                return False
            except Exception as exc:
                logger.error(
                    "Could not load parameters from {}: {}", main_parameters_path, exc
                )
                return False

            if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
                logger.error(
                    "Could not load parameters from {}: "
                    "MainParameter must inherit from pydantic.BaseModel.",
                    main_parameters_path,
                )
                return False

            try:
                self.main_parameters = cls()
            except ValidationError as exc:
                logger.error(
                    "Could not load parameters from {}: {}", main_parameters_path, exc
                )
                return False

        # Phase 2: historic JSON
        historic_filename = historic_file if historic_file is not None else "parameters.json"
        historic_file_path = (
            process_dir.parent / "protocols_hystoric" / historic_filename
        )

        if not historic_file_path.exists():
            return True   # normal — protocols are created through the UI

        try:
            data = json.loads(historic_file_path.read_text(encoding="utf-8"))

            if "main_parameter" in data:
                if self.main_parameters is None:
                    logger.error(
                        "Could not load parameters from {}: "
                        "main_parameters.py was not loaded.",
                        historic_file_path,
                    )
                    return False
                self.main_parameters = type(self.main_parameters).model_validate(
                    data["main_parameter"]
                )

            key = f"{process_name}_{self.process_index}"
            if key not in data:
                logger.error(
                    "Could not load parameters from {}: key '{}' not found.",
                    historic_file_path,
                    key,
                )
                return False

            self.config = type(self.config).model_validate(data[key])

        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "Could not load parameters from {}: {}", historic_file_path, exc
            )
            return False

        return True
```

---

## 5. Files to modify

| File | Change |
|---|---|
| `chemunited_workflow/process.py` | Add `ConfigT` TypeVar with `bound=BaseModel`; add `_load_class`; add `process_index` + `main_parameters` to `Process.__init__`; add `load_parameters` method |
| `chemunited_workflow/__init__.py` | No new public symbols needed — `load_parameters` is a method, `_load_class` is private |

---

## 6. Usage example

```python
# protocols/my_process.py
from chemunited_workflow import Process, NodeExecutionContext
import networkx as nx

class MyProcess(Process):
    def build_workflow(self) -> nx.DiGraph: ...

    def dose_reagent(self, ctx: NodeExecutionContext) -> bool:
        volume = self.main_parameters.reagent_volume_ml   # type: ignore[union-attr]
        ctx.process.platform["pump"].put("dose", json={"volume_ml": volume})
        return True
```

```python
# protocols/main_parameters.py
from pydantic import BaseModel

class MainParameter(BaseModel):
    reagent_volume_ml: float = 5.0
    target_temperature_c: float = 25.0
```

```python
# runner.py
process = MyProcess(config=MyConfig(), process_index=0)
if not process.load_parameters():
    raise SystemExit("Parameter loading failed — see log.")

compiled = compile_workflow(process.build_workflow())
executor = WorkflowExecutor(compiled, max_workers=4)
result = executor.execute(process, start_node="start")
```

---

## 7. Design decisions summary

| Decision | Choice | Reason |
|---|---|---|
| `ConfigT` bound | `bound=BaseModel` | `load_parameters` calls `.model_validate()` — only valid on BaseModel subclasses; the type checker enforces this at definition time |
| `_load_class` return contract | Raise `AttributeError` on missing class | Avoids redundant `None` guard in caller; raising is more Pythonic |
| BaseModel guard placement | `issubclass` check before instantiation | Avoids running `__init__` only to discard the result |
| Logger calls | `logger.error("... {}", value)` placeholders | Loguru lazy evaluation — no f-string allocation on suppressed levels |
| `process_index` default | `0` | Single-instance processes need no change |
| Missing historic file | Return `True` | Not having a snapshot is the normal initial state |
| `config` key format | `"{process_stem}_{process_index}"` | Matches process file name; survives rename of the class |
| Folder name | `protocols_hystoric` | Kept as-is to match existing project layout |


---


# Step 04 — Dry-Run Mode

## Overview

Add a `dry_run` flag that allows workflow execution to be tested end-to-end without
sending any HTTP request to physical devices. The workflow graph, node logic,
concurrency guard, and response hooks all execute normally — only the actual network
call is suppressed.

Every suppressed call returns a synthetic `200 OK` with an **empty body**. Node methods
that branch on response content will receive an empty body in dry-run mode; this is by
design and is documented clearly in the API. The goal is to validate workflow feasibility
(graph traversal, conditional routing, join/fan-out logic), not to simulate device
behaviour.

---

## 1. `BaseClient` — `dry_run` flag and updated `_request`

Step 01 established a basic `BaseClient._request` that calls `self._session.request`
directly. Step 04 extends it with a dry-run branch. `ComponentClient._request`
(which wraps this with the concurrency guard) requires no further changes because it
already delegates to `super()._request(...)`.

Full call chain in dry-run mode:
```
verb method (get/put/post)
    └── ComponentClient._request   — acquires lock
            └── BaseClient._request  — dry_run=True → returns synthetic response
                                       dry_run=False → self._session.request(...)
```

```python
class BaseClient:
    def __init__(self, url: str | AnyHttpUrl, *, dry_run: bool = False) -> None:
        self.base_url = str(url).rstrip("/")
        self._dry_run = dry_run
        self._session = self._make_session()
```

### `_make_dry_response()`

```python
def _make_dry_response(self) -> requests.Response:
    """Return a synthetic 200 OK response used in dry-run mode.

    The response body is always empty (``response.text == ""`` and
    ``response.json()`` will raise).  Node methods that inspect the response
    body will therefore receive no data.  This is intentional: dry-run mode
    is designed to validate workflow feasibility (graph traversal, conditional
    routing, join/fan-out logic) and not to simulate device behaviour.

    If a node method must branch on response content, it should guard against
    an empty body explicitly, or be excluded from dry-run testing.
    """
    response = requests.Response()
    response.status_code = 200
    response._content = b""
    response.headers["Content-Type"] = "application/json"
    return response
```

### Updated `_request`

The dry-run branch runs **after** the concurrency guard acquires the lock (because
`ComponentClient._request` calls `super()._request` while holding the lock), so the
single-device contract is still enforced during simulated runs. Response hooks are
**not** fired for dry responses — there is no real request object to log, and raising
hooks on a synthetic response would be misleading.

```python
def _request(self, method: str, path: str, **kwargs) -> requests.Response:
    if self._dry_run:
        logger.info(
            "DRY RUN: {} {} body={}",
            method,
            self._build_url(path),
            kwargs.get("json") or kwargs.get("data") or "<no body>",
        )
        return self._make_dry_response()
    return self._session.request(method, self._build_url(path), **kwargs)
```

---

## 2. `ComponentClient` — forward `dry_run`

No logic change to `_request` — the `super()._request(...)` call established in Step 01
already routes through the updated `BaseClient._request`. Only `__init__` needs to
forward the flag:

```python
class ComponentClient(BaseClient):
    def __init__(self, url: str | AnyHttpUrl, *, dry_run: bool = False) -> None:
        super().__init__(url, dry_run=dry_run)
        self._access_lock = threading.Lock()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self._access_lock.acquire(blocking=False):
            raise ConcurrentClientAccessError(...)
        try:
            return super()._request(method, path, **kwargs)  # unchanged from Step 01
        finally:
            self._access_lock.release()
```

---

## 3. `Platform` — propagate `dry_run` through construction

Both classmethods accept `dry_run` and forward it to every `ComponentClient`:

```python
@classmethod
def from_connectivity(cls, path: Path | str, *, dry_run: bool = False) -> "Platform":
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    server_url = data["server_url"].rstrip("/")
    components = {
        assoc["component"]: ComponentClient(
            f"{server_url}/{assoc['component_url']}",
            dry_run=dry_run,
        )
        for assoc in data["associations"]
        if assoc.get("component_url", "").strip()
    }
    return cls(components)

@classmethod
def from_project_dir(cls, project_dir: Path | str, *, dry_run: bool = False) -> "Platform":
    return cls.from_connectivity(
        Path(project_dir) / "connectivity" / "associations.json",
        dry_run=dry_run,
    )
```

---

## 4. `RunnerService` — accept and forward `dry_run`

```python
def start(self, snapshot_filename: str, *, dry_run: bool = False) -> str:
    """Launch execution in a background thread. Returns run_id immediately.

    Parameters
    ----------
    dry_run:
        When ``True``, all HTTP calls to devices are suppressed.  The workflow
        graph, node logic, and concurrency guard run normally.  Every device
        call returns a synthetic ``200 OK`` with an **empty body** — node
        methods that branch on response content will receive no data.
    """
    ...
    thread = threading.Thread(
        target=self._execute,
        args=(run_id, snapshot_filename, sequence, data, dry_run),
        daemon=True,
    )
    ...

def _execute(self, run_id, snapshot_filename, sequence, data, dry_run: bool) -> None:
    ...
    platform = Platform.from_project_dir(self._project_dir, dry_run=dry_run)
    ...
```

---

## 5. API — `RunRequest` and `POST /run`

Add `dry_run` as an optional field on `RunRequest`. No new endpoint is needed —
the same `POST /run` handles both real and simulated runs.

```python
class RunRequest(BaseModel):
    snapshot: str           # filename inside protocols_hystoric/
    dry_run: bool = False   # when True, no HTTP calls are sent to devices
```

The runner router already unpacks `body: RunRequest` and calls `svc.start()`,
so only `start()` needs the extra parameter — the router itself is unchanged.

---

## 6. MCP — `start_run` tool

```python
@mcp.tool()
def start_run(snapshot: str, dry_run: bool = False) -> dict:
    """Start executing a protocol snapshot in the background.
    Returns a ``run_id`` to poll with ``get_run_status``.

    Parameters
    ----------
    snapshot:
        Filename in ``protocols_hystoric/``.
    dry_run:
        When ``True``, all HTTP calls to devices are suppressed and the
        workflow runs in simulation mode.  Every device call returns a
        synthetic ``200 OK`` with an **empty body** — useful for validating
        graph logic and parameter correctness before committing to a real run.
    """
    run_id = runner.start(snapshot, dry_run=dry_run)
    return {"run_id": run_id}
```

---

## 7. Files to Modify

| File | Change |
|---|---|
| `chemunited_workflow/clients.py` | `dry_run` arg on `BaseClient.__init__`; `_make_dry_response()`; dry-run branch in `BaseClient._request()`; `dry_run` arg forwarded in `ComponentClient.__init__()` |
| `chemunited_workflow/platform.py` | `dry_run` arg on `from_connectivity()` and `from_project_dir()` |
| `chemunited_workflow/api/schemas.py` | `dry_run: bool = False` field on `RunRequest` |
| `chemunited_workflow/api/services/runner.py` | `dry_run` param on `start()` and `_execute()` |
| `chemunited_workflow/mcp/tools.py` | `dry_run` param on `start_run` tool |

---

## 8. Design Decisions Summary

| Decision | Choice | Reason |
|---|---|---|
| Dry-run response body | Always empty | Simulating device responses is out of scope; purpose is workflow validation |
| Hook firing | Skipped for dry responses | No real request object exists; firing hooks on a synthetic response would mislead logging |
| Concurrency guard in dry-run | Kept active | Enforces single-device contract even in simulation; catches graph errors early |
| New endpoint vs flag | Flag on `RunRequest` | Same resource, different behaviour — standard REST practice; avoids endpoint duplication |
| `dry_run` default | `False` | Backward compatible; existing callers are unaffected |
| Scope | `BaseClient._request()` only | All verbs protected automatically; no change needed in `get()`, `put()`, `post()` |
| `ComponentClient._request` | Unchanged from Step 01 | Delegates to `super()._request()`; dry-run logic is transparently inherited |


---


# Step 03 — API Framework, MCP Server, and CLI

## Overview

Introduce three additions to `chemunited-workflow`:

- **`Platform.from_connectivity()`** — classmethod that builds a `Platform` from a
  `connectivity/associations.json` file
- **`chemunited_workflow/api/`** — package providing a `create_api()` factory for
  FastAPI servers (API 1 execute-only and API 2 builder+execute, controlled by a flag)
- **`chemunited_workflow/mcp/`** — package providing a `create_mcp_server()` factory
  for MCP tool servers (API 3)

At the project level, a single `__main__.py` acts as the CLI entry point using `click`.

---

## Conventions

### Node ID convention

Every `Process.build_workflow()` implementation **must** use `"start"` as the entry
node ID and `"finish"` as the terminal node ID. `RunnerService` always calls:

```python
executor.execute(process, start_node="start")
```

This is a project-wide naming contract, not an arbitrary string. It must be reflected
in `WorkflowNodeSpec` documentation and in all examples.

### Project directory layout

```
my_project/
├── __main__.py                  # CLI entry point
├── connectivity/
│   └── associations.json        # device-to-URL mapping
├── protocols_hystoric/          # JSON snapshots (append-only archive)
│   └── suzuki_batch_14_2026-05-15T10-38-00.json
└── protocols/                   # process modules and shared parameters
    ├── __init__.py              # exports PROCESSES and CONFIGS dicts
    ├── main_parameters.py       # defines MainParameter(BaseModel)
    ├── clean.py                 # subclass of Process
    └── react.py                 # subclass of Process
```

### Snapshot JSON format (recap from Step 02)

The historic JSON files in `protocols_hystoric/` serve as both the **parameter store**
and the **execution sequence**. The **insertion order** of non-`main_parameter` keys
defines the process execution order at runtime.

```json
{
  "main_parameter": {"repetition_ractions": 1},
  "clean_0": {},
  "React_1": {},
  "clean_2": {}
}
```

Keys follow the pattern `"{registry_name}_{process_index}"`. The key `"main_parameter"`
is reserved and always processed first.

### Connectivity file format

```json
{
  "server_url": "http://device-server:8000",
  "associations": [
    {"component": "AS pump",      "component_url": "sim-ml600-AS/right_pump"},
    {"component": "photo sensor", "component_url": ""}
  ]
}
```

Entries with an empty `component_url` are silently skipped — they represent devices
that exist physically but are not yet mapped to a server endpoint.

---

## 1. `Platform` — construction classmethods

Two classmethods are added to `Platform` in `chemunited_workflow/platform.py`.

### `from_connectivity()`

```python
@classmethod
def from_connectivity(cls, path: Path | str) -> "Platform":
    """Build a Platform from a connectivity/associations.json file.

    Parameters
    ----------
    path:
        Path to the associations JSON file.

    Returns
    -------
    Platform
        A fully populated Platform instance.

    Raises
    ------
    OSError
        If the file cannot be read.
    KeyError
        If the file is missing the ``server_url`` or ``associations`` keys.
    json.JSONDecodeError
        If the file is not valid JSON.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    server_url = data["server_url"].rstrip("/")
    components = {
        assoc["component"]: ComponentClient(f"{server_url}/{assoc['component_url']}")
        for assoc in data["associations"]
        if assoc.get("component_url", "").strip()
    }
    return cls(components)
```

**Why skip empty `component_url` silently?**
Entries with an empty URL represent devices that are physically present but not yet
mapped. Raising on them would prevent any process from starting until every device in
the lab is wired up — that is too strict. Skipping them lets the project start with a
partial mapping; any process that actually tries to access an unmapped device will fail
at call time with a clear `KeyError` from `Platform.__getitem__`.

### `from_project_dir()`

```python
@classmethod
def from_project_dir(cls, project_dir: Path | str) -> "Platform":
    """Shorthand: load from ``{project_dir}/connectivity/associations.json``.

    Parameters
    ----------
    project_dir:
        Root directory of the experiment project (the directory containing
        ``protocols/``, ``connectivity/``, and ``__main__.py``).
    """
    return cls.from_connectivity(
        Path(project_dir) / "connectivity" / "associations.json"
    )
```

This is the call site used by both `RunnerService` and `__main__.py`.

---

## 2. Project-level `__main__.py`

The project `__main__.py` is the single CLI entry point. It is registered in
`pyproject.toml` as the installed command for the experiment. All heavy imports
(`create_api`, `create_mcp_server`, `uvicorn`) are deferred inside the command body
so `--help` remains instant.

### `pyproject.toml` entry point

```toml
[project.scripts]
my_experiment = "my_experiment.__main__:main"
```

### CLI behaviour

| Invocation | Mode | Behaviour |
|---|---|---|
| `my_experiment snapshot.json --fastapi` | API 1 | FastAPI, execute-only |
| `my_experiment --fastapi` | API 2 | FastAPI, full builder + execute |
| `my_experiment --mcp` | API 3 | MCP server |

The `snapshot` positional argument being present sets `enable_builder=False`.
Its absence sets `enable_builder=True`.

### Implementation

```python
"""Entry point for the experiment project.

Usage
-----
    my_experiment --help
    my_experiment snapshot.json --fastapi   # API 1 — execute a specific snapshot
    my_experiment --fastapi                 # API 2 — full builder + execute API
    my_experiment --mcp                     # API 3 — MCP server
"""
from __future__ import annotations

from pathlib import Path

import click


@click.command()
@click.argument(
    "snapshot",
    required=False,
    type=click.Path(exists=True, path_type=Path),
)
@click.option("--fastapi", "mode", flag_value="fastapi", default=True,
              help="Start the FastAPI server (default).")
@click.option("--mcp", "mode", flag_value="mcp",
              help="Start the MCP server.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Bind host for the FastAPI server.")
@click.option("--port", default=8000, show_default=True, type=int,
              help="Bind port for the FastAPI server.")
@click.option("--reload", is_flag=True, default=False,
              help="Enable auto-reload (development only).")
def main(
    snapshot: Path | None,
    mode: str,
    host: str,
    port: int,
    reload: bool,
) -> None:
    project_dir = Path(__file__).parent

    from protocols import CONFIGS, PROCESSES
    from protocols.main_parameters import MainParameter

    if mode == "mcp":
        from chemunited_workflow.mcp import create_mcp_server

        server = create_mcp_server(
            project_dir=project_dir,
            processes=PROCESSES,
            configs=CONFIGS,
            main_parameter_class=MainParameter,
        )
        server.run()

    else:  # fastapi (default)
        import uvicorn

        from chemunited_workflow.api import create_api

        enable_builder = snapshot is None
        app = create_api(
            project_dir=project_dir,
            processes=PROCESSES,
            configs=CONFIGS,
            main_parameter_class=MainParameter,
            enable_builder=enable_builder,
        )
        uvicorn.run(app, host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
```

**Why deferred imports?**
All heavy imports happen inside the command body. `click` resolves options before the
body runs, so `my_experiment --help` never pays the import cost of FastAPI or uvicorn.

---

## 3. `chemunited_workflow/api/` — package structure

```
chemunited_workflow/api/
├── __init__.py          ← create_api() factory — public entry point
├── dependencies.py      ← get_protocol_service(), get_runner_service() — Depends() stubs
├── schemas.py           ← SnapshotIn, RunRequest, RunStatus, ProcessInfo, LogMeta
├── run_store.py         ← RunStore — thread-safe in-memory run registry
├── routers/
│   ├── __init__.py
│   ├── processes.py     ← GET /processes, GET /processes/{name}/schema
│   ├── snapshots.py     ← read_router (always) + write_router (builder mode only)
│   ├── runner.py        ← POST /run, GET /run/*, SSE stream, DELETE /run/{id}
│   ├── components.py    ← GET /components
│   └── logs.py          ← GET /logs, GET /logs/{filename}
└── services/
    ├── __init__.py
    ├── protocol.py      ← ProtocolService
    └── runner.py        ← RunnerService
```

### 3.1 `RunStore`

A thread-safe registry keyed by `run_id`. Each entry holds the run state, a list of
`WorkflowExecutionEvent` objects accumulated since the last poll, and the ordered list
of `WorkflowResult` objects — one per process that has completed.

```python
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum

from chemunited_workflow import WorkflowExecutionEvent, WorkflowResult


class RunState(str, Enum):
    RUNNING   = "running"
    FINISHED  = "finished"
    FAILED    = "failed"
    CANCELLED = "cancelled"


@dataclass
class RunRecord:
    run_id: str
    state: RunState = RunState.RUNNING
    events: list[WorkflowExecutionEvent] = field(default_factory=list)
    results: list[WorkflowResult] = field(default_factory=list)


class RunStore:
    """Thread-safe in-memory registry of active and recent runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, RunRecord] = {}

    def create(self) -> str:
        run_id = str(uuid.uuid4())
        with self._lock:
            self._records[run_id] = RunRecord(run_id=run_id)
        return run_id

    def append_event(self, run_id: str, event: WorkflowExecutionEvent) -> None:
        with self._lock:
            self._records[run_id].events.append(event)

    def pop_events(self, run_id: str) -> list[WorkflowExecutionEvent]:
        """Return and clear all events accumulated since the last poll."""
        with self._lock:
            events = list(self._records[run_id].events)
            self._records[run_id].events.clear()
            return events

    def append_result(self, run_id: str, result: WorkflowResult) -> None:
        """Append the WorkflowResult of a completed process step."""
        with self._lock:
            self._records[run_id].results.append(result)

    def set_state(self, run_id: str, success: bool) -> None:
        """Mark the run as finished or failed once all process steps are done."""
        with self._lock:
            rec = self._records[run_id]
            rec.state = RunState.FINISHED if success else RunState.FAILED

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def cancel(self, run_id: str) -> bool:
        """Mark a run as cancelled. The background thread checks this flag between processes."""
        with self._lock:
            rec = self._records.get(run_id)
            if rec is None or rec.state != RunState.RUNNING:
                return False
            rec.state = RunState.CANCELLED
            return True

    @property
    def active_run_id(self) -> str | None:
        """Return the run_id of the currently running process, if any."""
        with self._lock:
            for rec in self._records.values():
                if rec.state == RunState.RUNNING:
                    return rec.run_id
            return None
```

**Why `pop_events` clears on read?**
MCP polling calls `get_run_status` repeatedly. Clearing events on each read means the
client receives only new events since the last poll — exactly the right behaviour for
incremental progress. The SSE route reads events directly from the listener callback
and never calls `pop_events`.

**Why `results` is a list?**
A run executes multiple processes in sequence, each producing its own
`WorkflowResult`. Storing all of them preserves the full execution history — useful
for post-run analysis and debugging. The `get_run_report` endpoint returns the
complete list.

### 3.2 `ProtocolService`

Owns all file I/O for the project directory: process introspection, snapshot CRUD,
connectivity reading, and log access.

```python
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from chemunited_workflow import Process


class ProtocolService:
    def __init__(
        self,
        project_dir: Path,
        processes: dict[str, type[Process]],
        configs: dict[str, type[BaseModel]],
        main_parameter_class: type[BaseModel],
    ) -> None:
        self._project_dir = project_dir
        self._processes = processes
        self._configs = configs
        self._main_parameter_class = main_parameter_class

    # ── Process introspection ────────────────────────────────────────────────

    def list_processes(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": (cls.__doc__ or "").strip(),
                "config_schema": self._configs[name].model_json_schema(),
            }
            for name, cls in self._processes.items()
        ]

    def get_process_schema(self, name: str) -> dict[str, Any]:
        if name not in self._processes:
            raise KeyError(
                f"Process '{name}' not found. Available: {list(self._processes)}"
            )
        return {
            "process": name,
            "config_schema": self._configs[name].model_json_schema(),
            "main_parameter_schema": self._main_parameter_class.model_json_schema(),
        }

    # ── Snapshot CRUD ────────────────────────────────────────────────────────

    @property
    def _snapshot_dir(self) -> Path:
        return self._project_dir / "protocols_hystoric"

    def list_snapshots(self) -> list[dict[str, Any]]:
        return [
            {
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size,
            }
            for f in sorted(
                self._snapshot_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        ]

    def read_snapshot(self, filename: str) -> dict[str, Any]:
        path = self._snapshot_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Snapshot '{filename}' not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def write_snapshot(self, name: str, data: dict[str, Any]) -> str:
        """Validate all process configs, then write. Returns the new filename."""
        self._validate_snapshot(data)
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"{name}_{timestamp}.json"
        path = self._snapshot_dir / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return filename

    def _validate_snapshot(self, data: dict[str, Any]) -> None:
        for key, params in data.items():
            if key == "main_parameter":
                self._main_parameter_class.model_validate(params)
                continue
            m = re.fullmatch(r"(.+)_(\d+)", key)
            if not m:
                raise ValueError(
                    f"Invalid snapshot key '{key}'. Expected '{{process}}_{{index}}'."
                )
            process_name = m.group(1)
            if process_name not in self._configs:
                raise ValueError(
                    f"Unknown process '{process_name}' in snapshot key '{key}'."
                )
            self._configs[process_name].model_validate(params)

    # ── Components ───────────────────────────────────────────────────────────

    def read_components(self) -> dict[str, Any]:
        path = self._project_dir / "connectivity" / "associations.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # ── Logs ─────────────────────────────────────────────────────────────────

    @property
    def _log_dir(self) -> Path:
        return self._project_dir / "log"

    def list_logs(self) -> list[dict[str, Any]]:
        return [
            {
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "size_bytes": f.stat().st_size,
            }
            for f in sorted(
                self._log_dir.glob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        ]

    def read_log(self, filename: str, tail: int | None = None) -> str:
        path = self._log_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Log '{filename}' not found.")
        text = path.read_text(encoding="utf-8")
        if tail is not None:
            return "\n".join(text.splitlines()[-tail:])
        return text
```

### 3.3 `RunnerService`

Parses the snapshot sequence, instantiates each `Process`, builds the `Platform`, and
launches execution in a background daemon thread. Feeds `WorkflowExecutionEvent`
objects into `RunStore` as they arrive and appends each `WorkflowResult` after every
completed process step.

**Node ID contract:** every `Process.build_workflow()` must use `node_id="start"` as
the entry node. `_execute` always calls `executor.execute(process, start_node="start")`.

```python
import json
import re
import threading
from pathlib import Path

from pydantic import BaseModel

from chemunited_workflow import Process, WorkflowExecutor, compile_workflow
from chemunited_workflow.platform import Platform

from .run_store import RunState, RunStore


class RunnerService:
    def __init__(
        self,
        project_dir: Path,
        processes: dict[str, type[Process]],
        configs: dict[str, type[BaseModel]],
        run_store: RunStore,
    ) -> None:
        self._project_dir = project_dir
        self._processes = processes
        self._configs = configs
        self._run_store = run_store

    def start(self, snapshot_filename: str) -> str:
        """Launch execution in a background thread. Returns run_id immediately."""
        snapshot_path = self._project_dir / "protocols_hystoric" / snapshot_filename
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        sequence = self._parse_sequence(data)
        run_id = self._run_store.create()
        thread = threading.Thread(
            target=self._execute,
            args=(run_id, snapshot_filename, sequence, data),
            daemon=True,
        )
        thread.start()
        return run_id

    def _execute(
        self,
        run_id: str,
        snapshot_filename: str,
        sequence: list[tuple[str, int]],
        data: dict,
    ) -> None:
        try:
            platform = Platform.from_project_dir(self._project_dir)
            for process_name, process_index in sequence:
                if self._run_store.get(run_id).state == RunState.CANCELLED:
                    return
                config_data = data.get(f"{process_name}_{process_index}", {})
                config = self._configs[process_name].model_validate(config_data)
                process = self._processes[process_name](
                    config=config,
                    platform=platform,
                    process_index=process_index,
                )
                process.load_parameters(snapshot_filename)
                compiled = compile_workflow(process.build_workflow())
                executor = WorkflowExecutor(
                    compiled,
                    max_workers=4,
                    event_listeners=[
                        lambda e: self._run_store.append_event(run_id, e)
                    ],
                )
                result = executor.execute(process, start_node="start")
                self._run_store.append_result(run_id, result)
            self._run_store.set_state(run_id, success=True)
        except Exception:
            self._run_store.set_state(run_id, success=False)

    @staticmethod
    def _parse_sequence(data: dict) -> list[tuple[str, int]]:
        """Extract the ordered (process_name, index) list from snapshot key order."""
        sequence = []
        for key in data:
            if key == "main_parameter":
                continue
            m = re.fullmatch(r"(.+)_(\d+)", key)
            if m:
                sequence.append((m.group(1), int(m.group(2))))
        return sequence
```

**Cancellation granularity**
`RunStore.cancel()` sets the state to `CANCELLED`. `_execute()` checks this flag
between processes. Cancellation within a running process is not propagated — the
current process completes its full workflow before the runner stops. This is
intentional for physical device safety: a device in the middle of a dispensing
cycle must complete that cycle.

**Key parsing**
`re.fullmatch(r"(.+)_(\d+)", key)` splits on the **last** underscore. This correctly
handles process names that themselves contain underscores (e.g. `"my_process_0"`
→ name `"my_process"`, index `0`).

### 3.4 Request/response schemas (`schemas.py`)

```python
from typing import Any
from pydantic import BaseModel


class ProcessInfo(BaseModel):
    name: str
    description: str
    config_schema: dict[str, Any]


class SnapshotMeta(BaseModel):
    filename: str
    modified: str
    size_bytes: int


class SnapshotIn(BaseModel):
    """Request body for POST /snapshots. Each save always creates a new versioned file."""
    name: str
    data: dict[str, Any]   # full snapshot dict: main_parameter + process keys


class RunRequest(BaseModel):
    snapshot: str          # filename inside protocols_hystoric/


class RunStatus(BaseModel):
    run_id: str
    state: str
    events: list[dict[str, Any]]


class LogMeta(BaseModel):
    filename: str
    modified: str
    size_bytes: int
```

### 3.5 Dependencies (`dependencies.py`)

Stub functions that are overridden by `create_api()` using `dependency_overrides`.
This means routers never import service instances directly — they always go through
the injection mechanism.

```python
"""Dependency stubs — overridden by create_api() at startup via dependency_overrides."""
from .services.protocol import ProtocolService
from .services.runner import RunnerService


def get_protocol_service() -> ProtocolService:
    raise NotImplementedError("Dependency not wired — was create_api() called?")


def get_runner_service() -> RunnerService:
    raise NotImplementedError("Dependency not wired — was create_api() called?")
```

### 3.6 Routers

All routers use **function-based routes** (standard FastAPI style). Service objects are
injected via `Depends()`. Each module exports its `APIRouter` instance(s).

#### `routers/processes.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from ..dependencies import get_protocol_service
from ..services.protocol import ProtocolService

router = APIRouter(prefix="/processes", tags=["processes"])


@router.get("/")
async def list_processes(svc: ProtocolService = Depends(get_protocol_service)):
    return svc.list_processes()


@router.get("/{name}/schema")
async def get_process_schema(
    name: str,
    svc: ProtocolService = Depends(get_protocol_service),
):
    try:
        return svc.get_process_schema(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

#### `routers/snapshots.py`

Snapshots are **immutable** — every save creates a new timestamped file. There is no
PUT endpoint. `write_router` exposes only POST (create) and DELETE.

Two separate routers: `read_router` is always included; `write_router` is included
only when `enable_builder=True`.

```python
from fastapi import APIRouter, Depends, HTTPException
from ..dependencies import get_protocol_service
from ..schemas import SnapshotIn
from ..services.protocol import ProtocolService

read_router  = APIRouter(prefix="/snapshots", tags=["snapshots"])
write_router = APIRouter(prefix="/snapshots", tags=["snapshots"])


@read_router.get("/")
async def list_snapshots(svc: ProtocolService = Depends(get_protocol_service)):
    return svc.list_snapshots()


@read_router.get("/{filename}")
async def get_snapshot(
    filename: str,
    svc: ProtocolService = Depends(get_protocol_service),
):
    try:
        return svc.read_snapshot(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@write_router.post("/", status_code=201)
async def create_snapshot(
    body: SnapshotIn,
    svc: ProtocolService = Depends(get_protocol_service),
):
    try:
        filename = svc.write_snapshot(body.name, body.data)
        return {"filename": filename}
    except (ValueError, Exception) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@write_router.delete("/{filename}", status_code=204)
async def delete_snapshot(
    filename: str,
    svc: ProtocolService = Depends(get_protocol_service),
):
    path = svc._snapshot_dir / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot '{filename}' not found.")
    path.unlink()
```

#### `routers/runner.py`

```python
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from ..dependencies import get_runner_service
from ..schemas import RunRequest, RunStatus
from ..services.runner import RunnerService

router = APIRouter(prefix="/run", tags=["run"])


@router.post("/", status_code=202)
async def start_run(
    body: RunRequest,
    svc: RunnerService = Depends(get_runner_service),
):
    run_id = svc.start(body.snapshot)
    return {"run_id": run_id}


@router.get("/active")
async def get_active_run(svc: RunnerService = Depends(get_runner_service)):
    return {"run_id": svc._run_store.active_run_id}


@router.get("/{run_id}/status")
async def get_run_status(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    rec = svc._run_store.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    events = svc._run_store.pop_events(run_id)
    return RunStatus(
        run_id=run_id,
        state=rec.state.value,
        events=[e.model_dump() for e in events],
    )


@router.get("/{run_id}/report")
async def get_run_report(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    rec = svc._run_store.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    if rec.state == RunState.RUNNING:
        raise HTTPException(status_code=202, detail="Run has not finished yet.")
    return {
        "run_id": run_id,
        "state": rec.state.value,
        "results": [r.model_dump() for r in rec.results],
    }


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    """Server-Sent Events stream of WorkflowExecutionEvent objects."""
    async def generate():
        rec = svc._run_store.get(run_id)
        if rec is None:
            yield 'data: {"error": "run not found"}\n\n'
            return
        while rec.state.value == "running":
            for event in svc._run_store.pop_events(run_id):
                yield f"data: {event.model_dump_json()}\n\n"
            await asyncio.sleep(0.1)
        yield f'data: {{"state": "{rec.state.value}"}}\n\n'
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.delete("/{run_id}", status_code=204)
async def cancel_run(
    run_id: str,
    svc: RunnerService = Depends(get_runner_service),
):
    if not svc._run_store.cancel(run_id):
        raise HTTPException(
            status_code=404,
            detail=f"Run '{run_id}' not found or not running.",
        )
```

#### `routers/components.py` and `routers/logs.py`

```python
# components.py
from fastapi import APIRouter, Depends
from ..dependencies import get_protocol_service
from ..services.protocol import ProtocolService

router = APIRouter(prefix="/components", tags=["components"])

@router.get("/")
async def get_components(svc: ProtocolService = Depends(get_protocol_service)):
    return svc.read_components()
```

```python
# logs.py
from fastapi import APIRouter, Depends, HTTPException
from ..dependencies import get_protocol_service
from ..services.protocol import ProtocolService

router = APIRouter(prefix="/logs", tags=["logs"])

@router.get("/")
async def list_logs(svc: ProtocolService = Depends(get_protocol_service)):
    return svc.list_logs()

@router.get("/{filename}")
async def read_log(
    filename: str,
    tail: int | None = None,
    svc: ProtocolService = Depends(get_protocol_service),
):
    try:
        return {"content": svc.read_log(filename, tail=tail)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

### 3.7 `create_api()` factory (`api/__init__.py`)

```python
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel

from chemunited_workflow import Process
from .dependencies import get_protocol_service, get_runner_service
from .run_store import RunStore
from .routers.processes import router as processes_router
from .routers.snapshots import read_router as snapshots_read_router
from .routers.snapshots import write_router as snapshots_write_router
from .routers.runner import router as runner_router
from .routers.components import router as components_router
from .routers.logs import router as logs_router
from .services.protocol import ProtocolService
from .services.runner import RunnerService


def create_api(
    *,
    project_dir: Path,
    processes: dict[str, type[Process]],
    configs: dict[str, type[BaseModel]],
    main_parameter_class: type[BaseModel],
    enable_builder: bool = True,
) -> FastAPI:
    """Create and return a configured FastAPI application.

    Parameters
    ----------
    project_dir:
        Root directory of the experiment project.
    processes:
        ``PROCESSES`` dict from ``protocols/__init__.py``.
    configs:
        ``CONFIGS`` dict from ``protocols/__init__.py``.
    main_parameter_class:
        ``MainParameter`` class from ``protocols/main_parameters.py``.
    enable_builder:
        ``True`` (API 2) — include snapshot write/delete endpoints.
        ``False`` (API 1) — expose read and run endpoints only.
    """
    run_store = RunStore()
    protocol_service = ProtocolService(
        project_dir=project_dir,
        processes=processes,
        configs=configs,
        main_parameter_class=main_parameter_class,
    )
    runner_service = RunnerService(
        project_dir=project_dir,
        processes=processes,
        configs=configs,
        run_store=run_store,
    )

    title = "chemunited API — builder" if enable_builder else "chemunited API — execute"
    app = FastAPI(title=title)

    # Wire dependencies via override — no global state, each call is isolated
    app.dependency_overrides[get_protocol_service] = lambda: protocol_service
    app.dependency_overrides[get_runner_service]   = lambda: runner_service

    app.include_router(processes_router)
    app.include_router(snapshots_read_router)
    app.include_router(runner_router)
    app.include_router(components_router)
    app.include_router(logs_router)

    if enable_builder:
        app.include_router(snapshots_write_router)

    return app
```

**Why `dependency_overrides` instead of global singletons?**
Each call to `create_api()` produces an independent app with its own `RunStore` and
service instances. There is no module-level state, so tests can call `create_api()`
with a temporary directory and a mock registry without patching anything globally.

---

## 4. `chemunited_workflow/mcp/` — package structure

```
chemunited_workflow/mcp/
├── __init__.py    ← create_mcp_server() factory
└── tools.py       ← register_tools() with all MCP tool definitions
```

The MCP package reuses `ProtocolService`, `RunnerService`, and `RunStore` from
`chemunited_workflow/api/` — there is no duplication of business logic.

### 4.1 `create_mcp_server()` factory (`mcp/__init__.py`)

```python
from pathlib import Path
from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP

from chemunited_workflow import Process
from chemunited_workflow.api.run_store import RunStore
from chemunited_workflow.api.services.protocol import ProtocolService
from chemunited_workflow.api.services.runner import RunnerService
from .tools import register_tools


def create_mcp_server(
    *,
    project_dir: Path,
    processes: dict[str, type[Process]],
    configs: dict[str, type[BaseModel]],
    main_parameter_class: type[BaseModel],
) -> FastMCP:
    """Create and return a configured MCP server.

    Exposes the same capabilities as API 2 (builder + execute) as MCP tools,
    with polling instead of SSE for run status.
    """
    run_store = RunStore()
    protocol_service = ProtocolService(
        project_dir=project_dir,
        processes=processes,
        configs=configs,
        main_parameter_class=main_parameter_class,
    )
    runner_service = RunnerService(
        project_dir=project_dir,
        processes=processes,
        configs=configs,
        run_store=run_store,
    )
    mcp = FastMCP("chemunited")
    register_tools(mcp, protocol_service, runner_service)
    return mcp
```

### 4.2 Tool definitions (`mcp/tools.py`)

MCP tools mirror the API 2 endpoint set. Each tool carries a rich docstring because the
LLM uses it to decide when and how to call the tool.

```python
from mcp.server.fastmcp import FastMCP
from chemunited_workflow.api.services.protocol import ProtocolService
from chemunited_workflow.api.services.runner import RunnerService


def register_tools(
    mcp: FastMCP,
    protocol: ProtocolService,
    runner: RunnerService,
) -> None:

    @mcp.tool()
    def list_processes() -> list[dict]:
        """List all processes registered in this experiment with their parameter
        schemas. Call this first to discover what processes are available before
        building a snapshot."""
        return protocol.list_processes()

    @mcp.tool()
    def get_process_schema(name: str) -> dict:
        """Return the full parameter schema for a named process, including
        ProcessConfig fields and MainParameter fields. Field metadata includes
        ``group``, ``editable``, and ``visible`` from ``json_schema_extra``."""
        return protocol.get_process_schema(name)

    @mcp.tool()
    def list_snapshots() -> list[dict]:
        """List all protocol snapshots in protocols_hystoric/, most recent first."""
        return protocol.list_snapshots()

    @mcp.tool()
    def get_snapshot(filename: str) -> dict:
        """Read the full contents of a specific snapshot JSON file."""
        return protocol.read_snapshot(filename)

    @mcp.tool()
    def create_snapshot(name: str, data: dict) -> dict:
        """Validate and save a new protocol snapshot. Each call always creates a
        new versioned file — snapshots are immutable once written.

        Parameters
        ----------
        name:
            Short name, e.g. ``"suzuki_batch_14"``. The saved filename will be
            ``{name}_{timestamp}.json``.
        data:
            Full snapshot dict. Must contain ``"main_parameter"`` and one key per
            process step in ``"{process_name}_{index}"`` format, in execution order.

            Example::

                {
                  "main_parameter": {"repetition_ractions": 2},
                  "clean_0": {},
                  "React_1": {},
                  "clean_2": {}
                }
        """
        filename = protocol.write_snapshot(name, data)
        return {"filename": filename}

    @mcp.tool()
    def start_run(snapshot: str) -> dict:
        """Start executing a protocol snapshot in the background.
        Returns a ``run_id`` to poll with ``get_run_status``.

        Parameters
        ----------
        snapshot:
            Filename in ``protocols_hystoric/``, e.g.
            ``"suzuki_batch_14_2026-05-15T10-38-00.json"``.
        """
        run_id = runner.start(snapshot)
        return {"run_id": run_id}

    @mcp.tool()
    def get_run_status(run_id: str) -> dict:
        """Poll the status of a running or completed execution.
        Returns the current state and all events since the last call to this tool.
        Call repeatedly until ``state`` is ``"finished"`` or ``"failed"``."""
        rec = runner._run_store.get(run_id)
        if rec is None:
            return {"error": f"Run '{run_id}' not found."}
        events = runner._run_store.pop_events(run_id)
        return {
            "run_id": run_id,
            "state": rec.state.value,
            "events": [e.model_dump() for e in events],
        }

    @mcp.tool()
    def get_run_report(run_id: str) -> dict:
        """Return the full execution report for a finished run.
        Returns one WorkflowResult per process step, in execution order."""
        rec = runner._run_store.get(run_id)
        if rec is None:
            return {"error": f"Run '{run_id}' not found."}
        return {
            "state": rec.state.value,
            "results": [r.model_dump() for r in rec.results],
        }

    @mcp.tool()
    def cancel_run(run_id: str) -> dict:
        """Cancel an active run. The current process step will finish before
        the runner stops."""
        ok = runner._run_store.cancel(run_id)
        return {"cancelled": ok}

    @mcp.tool()
    def get_components() -> dict:
        """Return the full connectivity/associations.json — the device-to-URL
        mapping for the current machine."""
        return protocol.read_components()

    @mcp.tool()
    def list_logs() -> list[dict]:
        """List all execution log files, most recent first."""
        return protocol.list_logs()
```

**Why no SSE tool?**
MCP tools are synchronous request/response. Holding an open connection while waiting
for events is not a supported use case. The LLM polls `get_run_status` between its own
reasoning steps, which is the natural pattern for a long-running physical process.

---

## 5. Files to Create and Modify

| File | Action | Content |
|---|---|---|
| `chemunited_workflow/platform.py` | Modify | Add `from_connectivity()` and `from_project_dir()` classmethods |
| `chemunited_workflow/api/__init__.py` | Create | `create_api()` factory |
| `chemunited_workflow/api/dependencies.py` | Create | `get_protocol_service()`, `get_runner_service()` stubs |
| `chemunited_workflow/api/schemas.py` | Create | `ProcessInfo`, `SnapshotIn`, `RunRequest`, `RunStatus`, `LogMeta` |
| `chemunited_workflow/api/run_store.py` | Create | `RunStore`, `RunRecord`, `RunState` |
| `chemunited_workflow/api/routers/__init__.py` | Create | Empty |
| `chemunited_workflow/api/routers/processes.py` | Create | GET /processes, GET /processes/{name}/schema |
| `chemunited_workflow/api/routers/snapshots.py` | Create | `read_router` (GET) + `write_router` (POST, DELETE) |
| `chemunited_workflow/api/routers/runner.py` | Create | POST /run, GET /run/*, SSE stream, DELETE /run/{id} |
| `chemunited_workflow/api/routers/components.py` | Create | GET /components |
| `chemunited_workflow/api/routers/logs.py` | Create | GET /logs, GET /logs/{filename} |
| `chemunited_workflow/api/services/__init__.py` | Create | Empty |
| `chemunited_workflow/api/services/protocol.py` | Create | `ProtocolService` |
| `chemunited_workflow/api/services/runner.py` | Create | `RunnerService` |
| `chemunited_workflow/mcp/__init__.py` | Create | `create_mcp_server()` factory |
| `chemunited_workflow/mcp/tools.py` | Create | `register_tools()` with all tool definitions |
| `chemunited_workflow/__init__.py` | Modify | Export `create_api`, `create_mcp_server` |
| `README.md` | Modify | Update Quick Start: `node_id="IN"` → `node_id="start"`, `node_id="OUT"` → `node_id="finish"` |
| `examples/custom_project/__main__.py` | Create | Click CLI entry point |

---

## 6. Design Decisions Summary

| Decision | Choice | Reason |
|---|---|---|
| API 1 vs API 2 | Single `create_api(enable_builder=)` flag | Avoids duplication; one factory, one app object |
| Route style | Function-based with `Depends()` | Idiomatic FastAPI; clean separation from service logic |
| Service wiring | `app.dependency_overrides` | No global state; each `create_api()` call is fully isolated |
| MCP reuses API services | `ProtocolService` + `RunnerService` imported from `api/` | No duplication of business logic |
| Snapshot sequence encoding | JSON key insertion order | No extra field; Python 3.7+ dicts preserve order |
| Key parsing regex | `re.fullmatch(r"(.+)_(\d+)", key)` | Handles process names that contain underscores |
| SSE vs polling | SSE for REST (`/run/{id}/stream`), polling for MCP | SSE needs open connection; MCP tools are request/response |
| Event delivery | `pop_events` clears on read | MCP clients see only new events per poll; SSE bypasses it |
| Cancellation granularity | Between processes, not within | Physical device safety: current step must complete |
| MCP tool docstrings | Detailed with parameters and examples | LLM uses docstrings to decide when and how to call tools |
| Empty `component_url` | Skip silently in `from_connectivity` | Allows partial device mapping; fails loud only at actual use |
| `from_project_dir` shortcut | Separate classmethod | Avoids repeating the standard path at every call site |
| Deferred imports in CLI | Inside command body | `--help` is instant; FastAPI/uvicorn load only when needed |
| `api/schemas.py` not `api/models.py` | `schemas.py` | Disambiguates from the core library's `models.py`; FastAPI convention for request/response types |
| Snapshots immutable | POST only (no PUT) | `write_snapshot` always timestamps; `protocols_hystoric/` is an append-only archive of executed protocols |
| `default_snapshot` in `create_api` | Removed | No server-side restriction needed; API 1 vs API 2 distinction is purely about write endpoints |
| `results` in `RunRecord` | `list[WorkflowResult]` | A run spans multiple processes; the full history is needed for post-run analysis |
| Node ID convention | `"start"` / `"finish"` | Fixed names allow `RunnerService` to call `executor.execute(process, start_node="start")` without per-process configuration |


# Step 05 — Test Suite

## Overview

Add a complete automated test suite for all code introduced in Steps 01–04.
Implementation of each step is considered **done only when its tests pass**.
This is the final gate: the package is not releasable until the full suite is green.

---

## Convention: test-gated development

Each step has a corresponding test module. A step may not be merged until:

1. The implementation compiles without errors.
2. `pytest tests/unit/test_<step>.py` exits with code `0`.
3. `pytest tests/integration/` exits with code `0`.

Run the full suite before any release:

```bash
pytest --tb=short -q
```

---

## 1. Directory layout

```
tests/
├── conftest.py                    # shared fixtures and the MinimalProcess helper
├── helpers.py                     # FakeProcess, FakeConfig, write_source() utility
├── fixtures/
│   ├── associations.json          # two entries: one with URL, one empty
│   └── parameters.json            # main_parameter + my_process_0 + my_process_1
├── unit/
│   ├── test_clients.py            # Step 01 — BaseClient, ComponentClient
│   ├── test_platform.py           # Step 01 — Platform
│   ├── test_process_parameters.py # Step 02 — load_parameters
│   └── test_run_store.py          # Step 03 — RunStore
└── integration/
    ├── test_api.py                 # Step 03 — FastAPI routes via TestClient
    └── test_dry_run.py             # Step 04 — dry-run flag end-to-end
```

---

## 2. `conftest.py` — shared fixtures

```python
import pytest
from pydantic import BaseModel
from chemunited_workflow import (
    Process, NodeExecutionContext,
    WorkflowEdgeSpec, WorkflowNodeSpec,
)
import networkx as nx


class MinimalConfig(BaseModel):
    pass


class MinimalProcess(Process[MinimalConfig]):
    """Trivial two-node workflow used by every test that needs a runnable process."""

    def build_workflow(self) -> nx.DiGraph:
        g = nx.DiGraph()
        g.add_node("start",  **WorkflowNodeSpec(node_id="start",  method="start",  label="Start").model_dump(exclude_none=True))
        g.add_node("finish", **WorkflowNodeSpec(node_id="finish", method="finish", label="Finish").model_dump(exclude_none=True))
        g.add_edge("start", "finish", **WorkflowEdgeSpec(condition=True).model_dump(exclude_none=True))
        return g

    def start(self, ctx: NodeExecutionContext) -> bool:
        return True

    def finish(self, ctx: NodeExecutionContext) -> bool:
        return True


@pytest.fixture
def minimal_process():
    return MinimalProcess(config=MinimalConfig())
```

---

## 3. `helpers.py` — shared utilities

```python
from pathlib import Path


MINIMAL_PROCESS_SRC = '''
from pydantic import BaseModel
from chemunited_workflow import Process, NodeExecutionContext, WorkflowEdgeSpec, WorkflowNodeSpec
import networkx as nx

class MyConfig(BaseModel):
    value: float = 1.0

class MyProcess(Process):
    def build_workflow(self):
        g = nx.DiGraph()
        g.add_node("start",  **WorkflowNodeSpec(node_id="start",  method="start",  label="Start").model_dump(exclude_none=True))
        g.add_node("finish", **WorkflowNodeSpec(node_id="finish", method="finish", label="Finish").model_dump(exclude_none=True))
        g.add_edge("start", "finish", **WorkflowEdgeSpec(condition=True).model_dump(exclude_none=True))
        return g
    def start(self, ctx: NodeExecutionContext) -> bool:
        return True
    def finish(self, ctx: NodeExecutionContext) -> bool:
        return True
'''

MAIN_PARAMETERS_SRC = '''
from pydantic import BaseModel

class MainParameter(BaseModel):
    reagent_volume_ml: float = 5.0
    target_temperature_c: float = 25.0
'''


def write_source(directory: Path, filename: str, source: str) -> Path:
    """Write Python source text to *directory/filename* and return the path."""
    path = directory / filename
    path.write_text(source, encoding="utf-8")
    return path


def make_project_tree(tmp_path: Path) -> dict:
    """Create the standard project directory layout inside *tmp_path*.

    Returns a dict with keys ``process_dir``, ``historic_dir``,
    ``connectivity_dir`` pointing to the created directories.
    """
    process_dir = tmp_path / "processes"
    historic_dir = tmp_path / "protocols_hystoric"
    connectivity_dir = tmp_path / "connectivity"
    log_dir = tmp_path / "log"
    for d in (process_dir, historic_dir, connectivity_dir, log_dir):
        d.mkdir()
    write_source(process_dir, "my_process.py", MINIMAL_PROCESS_SRC)
    write_source(process_dir, "main_parameters.py", MAIN_PARAMETERS_SRC)
    return {
        "process_dir": process_dir,
        "historic_dir": historic_dir,
        "connectivity_dir": connectivity_dir,
        "log_dir": log_dir,
    }
```

---

## 4. `fixtures/associations.json`

```json
{
  "server_url": "http://device-server:8000",
  "associations": [
    {"component": "pump",   "component_url": "sim-ml600/pump"},
    {"component": "sensor", "component_url": ""}
  ]
}
```

After loading: `"pump"` is present, `"sensor"` is absent (empty URL skipped).

---

## 5. `fixtures/parameters.json`

```json
{
  "main_parameter": {
    "reagent_volume_ml": 9.0,
    "target_temperature_c": 60.0
  },
  "my_process_0": {
    "value": 2.5
  },
  "my_process_1": {
    "value": 0.8
  }
}
```

---

## 6. `unit/test_clients.py` — Step 01

### What to test

| Test | Assertion |
|---|---|
| `_build_url` strips trailing slash on base and leading slash on path | URL is always well-formed |
| `_log_response` emits method, full URL, body, status, elapsed | Hook produces correct log record |
| Hook list order is `[_log_response, _raise_for_status]` | Log fires before any exception unwinds |
| `_raise_for_status` raises `HTTPError` on 4xx and 5xx | Caller does not need to check status manually |
| 200 response — neither hook raises | Happy path |
| `ComponentClient`: sequential calls succeed | Lock is released in `finally` |
| `ComponentClient`: two threads simultaneously → second raises `ConcurrentClientAccessError` | Non-blocking lock enforced |
| After a failed concurrent call, client is still usable | Lock always released |
| Error message contains class name and base URL | Actionable error for the user |

### Implementation notes

- Use `responses` library (or `unittest.mock.patch`) to intercept `requests.Session.request` — no real HTTP calls.
- For the concurrency test, use `threading.Barrier(2)` so both threads enter `_request` simultaneously before either acquires the lock.

---

## 7. `unit/test_platform.py` — Step 01

| Test | Assertion |
|---|---|
| `platform["missing"]` raises `KeyError` containing available names | Helpful error message |
| `register` + `__contains__` + `len` | Full `Mapping` API |
| `from_connectivity` with `fixtures/associations.json` → `pump` present, `sensor` absent | Factory correct; empty URL skipped |
| `from_connectivity` with missing `server_url` key → `KeyError` | Bad file fails loudly |
| `from_project_dir` resolves `connectivity/associations.json` inside `tmp_path` | Shortcut path correct |

---

## 8. `unit/test_process_parameters.py` — Step 02

All tests use `tmp_path` with `make_project_tree()` from `helpers.py`.
The process class is instantiated via `importlib` (see §2 of the pre-step planning)
so that `inspect.getfile` resolves correctly to a real file on disk.

### Phase 1 — `MainParameter` class loading

| Test | Expected result |
|---|---|
| No `main_parameters.py` | `main_parameters` stays `None`; returns `True` |
| File exists but `MainParameter` class absent | Returns `False` |
| `MainParameter` defined but not a `BaseModel` subclass | Returns `False` |
| Valid `MainParameter` with defaults | `self.main_parameters` set; returns `True` |
| `MainParameter` has a required field (no default) | `ValidationError` → returns `False` |

### Phase 2 — historic JSON loading

| Test | Expected result |
|---|---|
| No `parameters.json` | Returns `True` (normal initial state) |
| JSON has `main_parameter` key but `main_parameters` is `None` | Returns `False` |
| JSON `main_parameter` overrides loaded defaults | `self.main_parameters` fields updated |
| JSON missing `my_process_0` key | Returns `False` |
| JSON with valid `my_process_0` | `self.config` updated |
| `process_index=1` → key looked up is `my_process_1` | Index suffix handled |
| JSON has invalid config values | `ValidationError` → returns `False` |
| File contains invalid JSON | `json.JSONDecodeError` → returns `False` |

---

## 9. `unit/test_run_store.py` — Step 03

| Test | Assertion |
|---|---|
| `create()` returns a UUID string | Unique IDs generated |
| `pop_events` after two `append_event` calls returns both, then empty | Clears on read |
| `cancel` on a running run → `True`, state `CANCELLED` | Cancellation correct |
| `cancel` on a finished run → `False` | Cannot cancel completed run |
| `active_run_id` returns running ID, `None` when all done | Correct active detection |
| 50 threads each call `append_event` once → all 50 retrieved | Thread-safe list |
| `set_result(success=True)` → `FINISHED`; `success=False` → `FAILED` | State transitions |

For the threading test, use `threading.Barrier(50)` so all threads append simultaneously.

---

## 10. `integration/test_api.py` — Step 03

Use `fastapi.testclient.TestClient`. The `app` fixture calls `create_api()` with
`tmp_path` as the project directory and `MinimalProcess` / `MinimalConfig` as the
registered process.

### Fixtures

```python
@pytest.fixture
def app(tmp_path):
    dirs = make_project_tree(tmp_path)
    # write a minimal snapshot
    snapshot = {
        "main_parameter": {"reagent_volume_ml": 5.0, "target_temperature_c": 25.0},
        "my_process_0": {"value": 1.0},
    }
    (dirs["historic_dir"] / "run_001.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )
    return create_api(
        project_dir=tmp_path,
        processes={"my_process": MyProcess},
        configs={"my_process": MyConfig},
        main_parameter_class=MainParameter,
        enable_builder=True,
    )

@pytest.fixture
def client(app):
    return TestClient(app)
```

### Route tests

| Test | Endpoint | Assertion |
|---|---|---|
| List processes | `GET /processes/` | 200, list contains `"my_process"` |
| Unknown process schema | `GET /processes/unknown/schema` | 404 |
| Create valid snapshot | `POST /snapshots/` | 201, `filename` in body |
| Create snapshot with bad key | `POST /snapshots/` | 422 |
| Read existing snapshot | `GET /snapshots/run_001.json` | 200 |
| Read missing snapshot | `GET /snapshots/missing.json` | 404 |
| Start run | `POST /run` `{"snapshot": "run_001.json"}` | 202, `run_id` present |
| Poll status | `GET /run/{run_id}/status` | 200, `state` present |
| Poll unknown run | `GET /run/bad-id/status` | 404 |
| Cancel run | `DELETE /run/{run_id}` | 204 |
| Cancel unknown | `DELETE /run/bad-id` | 404 |
| Write disabled | `POST /snapshots/` with `enable_builder=False` | 404 or 405 |
| Components | `GET /components/` | 200, `server_url` in body |
| List logs | `GET /logs/` | 200, list |
| Read log with tail | `GET /logs/app.log?tail=5` | 200, ≤5 lines |
| Read missing log | `GET /logs/missing.log` | 404 |

For the `POST /run` → `GET /run/{id}/status` flow, give the background thread up
to 2 seconds to complete (`time.sleep(0.2)` then poll in a loop).

---

## 11. `integration/test_dry_run.py` — Step 04

### Unit-level dry-run

| Test | Assertion |
|---|---|
| `BaseClient(url, dry_run=True).get("/x")` — `Session.request` never called | No real HTTP |
| Response has `status_code == 200` and `content == b""` | Synthetic shape |
| `response.json()` raises `JSONDecodeError` | Body is truly empty |
| `_log_response` and `_raise_for_status` not called | Hooks skipped |
| `DRY RUN` message logged at `INFO` | Observability |
| `ComponentClient` concurrency guard raises even in dry-run | Guard remains active |

For hook assertions, patch both hook methods and assert `call_count == 0`.

### Integration-level dry-run

| Test | Assertion |
|---|---|
| `Platform.from_connectivity(path, dry_run=True)` — every client has `_dry_run=True` | Propagation through factory |
| `POST /run` with `{"snapshot": "...", "dry_run": true}` → 202, run completes | API flag propagated |
| `start_run(snapshot="...", dry_run=True)` via MCP tools → returns `run_id` | MCP flag propagated |

---

## 12. Files to create

| File | Content |
|---|---|
| `tests/conftest.py` | `MinimalProcess`, `MinimalConfig`, `minimal_process` fixture |
| `tests/helpers.py` | `write_source()`, `make_project_tree()`, source string constants |
| `tests/fixtures/associations.json` | Two-entry connectivity fixture |
| `tests/fixtures/parameters.json` | `main_parameter` + `my_process_0` + `my_process_1` |
| `tests/unit/test_clients.py` | Step 01 unit tests |
| `tests/unit/test_platform.py` | Step 01 unit tests |
| `tests/unit/test_process_parameters.py` | Step 02 unit tests |
| `tests/unit/test_run_store.py` | Step 03 unit tests |
| `tests/integration/test_api.py` | Step 03 integration tests |
| `tests/integration/test_dry_run.py` | Step 04 integration tests |

Also add test dependencies to `pyproject.toml`:

```toml
[project.optional-dependencies]
test = [
    "pytest >= 8.0",
    "pytest-mock >= 3.12",
    "responses >= 0.25",
    "httpx >= 0.27",       # required by FastAPI TestClient
]
```

---

## 13. Design decisions summary

| Decision | Choice | Reason |
|---|---|---|
| Test gate | All tests must pass before merge | No step is done until it is verified |
| `tmp_path` for file I/O | pytest built-in fixture | Isolated, auto-cleaned; no shared state between tests |
| `importlib` for process loading | Write `.py` to `tmp_path`, import dynamically | `inspect.getfile` requires a real file on disk |
| `responses` library | Intercept HTTP at the `Session` level | Tests run offline; no mock server needed |
| `TestClient` for API tests | `fastapi.testclient.TestClient` | Synchronous; no `asyncio` setup needed |
| `threading.Barrier` | Synchronise concurrent test threads | Deterministic race — avoids timing-dependent failures |
| Short poll loop for run status | `time.sleep(0.2)` + retry up to 2 s | Background thread needs time; busy-wait is fragile |
| Test dependencies separate | `[project.optional-dependencies] test` | Not installed in production; installable with `pip install .[test]` |