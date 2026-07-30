"""Data models for the web-replicate engine.

Borrowed wholesale from web-qa's model conventions: every model is a
``@dataclass`` that serializes via :meth:`Serializable.to_dict`, which converts
enum members to their ``.value`` and recurses into nested dataclasses/lists so
the emitted JSON is clean (plain :func:`dataclasses.asdict` would leave enum
objects in place). **Preserve this pattern when adding fields.**

Where web-qa's models describe *judgments about* a page, these describe a page
(or a user path) in enough structural detail to **rebuild** it: a ref to the
rendered markup, the stylesheets/scripts/assets it pulls, its storage, and a
sanitized request/response log the agent reads to infer the backend contract.

Large blobs (rendered HTML, response bodies, screenshots) are written to a
capture directory by the CLI and referenced here by a ``*_ref`` relative path,
so the JSON the agent reads stays lean while nothing is lost for replication.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Serialization helper (identical to web-qa — keep in sync)
# ---------------------------------------------------------------------------


def _clean(obj: Any) -> Any:
    """Recursively convert enums to their values within dicts/lists."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


class Serializable:
    """Mixin giving dataclasses an enum-safe ``to_dict``."""

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Enums (the engine's vocabulary)
# ---------------------------------------------------------------------------


class BrowserEngine(str, Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    TYPE = "type"
    PRESS = "press"
    SCROLL = "scroll"
    WAIT_FOR = "wait_for"
    SELECT = "select"


class AssetKind(str, Enum):
    STYLESHEET = "stylesheet"
    SCRIPT = "script"
    IMAGE = "image"
    FONT = "font"
    MEDIA = "media"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Actions (a step in a recorded path)
# ---------------------------------------------------------------------------


@dataclass
class Action(Serializable):
    """A single interaction the engine can perform, mirroring web-qa's Action.

    ``value`` is a generic string payload (form value, or scroll distance in px
    for ``SCROLL``). ``intent`` is a free-text note on what the step accomplishes
    in the user path — carried into the path documentation.
    """

    type: ActionType
    selector: Optional[str] = None
    url: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None
    intent: Optional[str] = None


# ---------------------------------------------------------------------------
# Raw observations
# ---------------------------------------------------------------------------


@dataclass
class ConsoleMessage(Serializable):
    """A console message. ``location`` (``url:line:col`` when Playwright supplies
    it) ties the message to the script that emitted it — useful when documenting
    which bundle owns a given behavior."""

    level: str  # "error", "warning", "log", "info", "debug"
    text: str
    location: Optional[str] = None


@dataclass
class NetworkRecord(Serializable):
    """One request/response pair — the primary evidence for backend inference.

    Headers and bodies are **sanitized** (secrets redacted) before capture unless
    the run opts in with ``--include-secrets``. Text bodies below the inline cap
    live in ``response_body`` / ``request_body``; larger ones are written to the
    capture dir and referenced by ``response_body_ref``. Binary responses
    (images/fonts/media) carry no body — only their metadata.
    """

    method: str
    url: str
    resource_type: str = "other"  # playwright resource_type: xhr|fetch|document|...
    status: int = 0
    request_headers: Dict[str, str] = field(default_factory=dict)
    request_content_type: Optional[str] = None
    request_body: Optional[str] = None
    response_headers: Dict[str, str] = field(default_factory=dict)
    response_content_type: Optional[str] = None
    response_body: Optional[str] = None
    response_body_ref: Optional[str] = None
    body_bytes: int = 0


@dataclass
class Cookie(Serializable):
    name: str
    value: str
    domain: str = ""
    path: str = "/"
    http_only: bool = False
    secure: bool = False
    same_site: Optional[str] = None
    expires: Optional[float] = None


@dataclass
class StorageSnapshot(Serializable):
    """Client-side persistence a faithful rebuild must reproduce: which keys the
    app writes to Web Storage, and its cookie shape (values redacted by default)."""

    local: Dict[str, str] = field(default_factory=dict)
    session: Dict[str, str] = field(default_factory=dict)
    cookies: List[Cookie] = field(default_factory=list)


@dataclass
class AssetRef(Serializable):
    """A resource the page depends on. ``attrs`` carries kind-specific detail
    (``alt``/``width``/``height`` for images, ``media`` for stylesheets,
    ``module``/``async``/``defer`` for scripts). ``local_ref`` is set only when
    the asset was downloaded to the capture dir (``--download-assets``)."""

    kind: AssetKind
    url: str
    attrs: Dict[str, str] = field(default_factory=dict)
    local_ref: Optional[str] = None


# ---------------------------------------------------------------------------
# Page structure (reused shapes from web-qa's snapshot)
# ---------------------------------------------------------------------------


@dataclass
class InteractiveElement(Serializable):
    """A control on the page, with a resolvable selector — the interaction
    surface a rebuild must recreate."""

    selector: str
    role: str
    text: str
    kind: str = "other"  # cta | nav | field | footer | other
    rank: int = 4


@dataclass
class FormField(Serializable):
    """One input/select/textarea, tied to its **visible label** and the
    validation the markup declares — the pair a rebuild needs to recreate the
    control and its rules. ``label`` is resolved robustly (``<label for>`` /
    wrapping label / ``aria-label`` / preceding text / placeholder). HTML-level
    constraints (``required``/``minlength``/``pattern``/…) are captured when
    present; JS-only rules (e.g. a Zod schema) won't be, but the label text
    often encodes them (e.g. "Business Description *0/70 characters")."""

    selector: str
    type: str
    name: Optional[str] = None
    label: str = ""
    required: bool = False
    placeholder: Optional[str] = None
    autocomplete: Optional[str] = None
    minlength: Optional[int] = None
    maxlength: Optional[int] = None
    min: Optional[str] = None
    max: Optional[str] = None
    pattern: Optional[str] = None
    options: List[str] = field(default_factory=list)  # visible option labels for <select>


@dataclass
class FormInfo(Serializable):
    selector: str
    fields: List[FormField] = field(default_factory=list)
    submit: Optional[str] = None
    action: Optional[str] = None  # the form's action attribute (a backend hint)
    method: Optional[str] = None


@dataclass
class LinkInfo(Serializable):
    selector: str
    text: str
    href: str
    scheme: str = "relative"  # http | https | mailto | tel | relative | unknown
    external: bool = False
    new_tab: bool = False


@dataclass
class DesignTokens(Serializable):
    """The design system extracted from computed styles + accessible CSS — what a
    rebuild needs to reproduce the *look*, not just the structure. ``css_variables``
    are the app's own `:root` custom properties (its design-token source when it has
    one); the palettes/fonts/radii/spacing are the most-used *rendered* values across
    a sample of elements (so it works even for utility-CSS apps that expose no
    variables); ``samples`` are computed styles of representative elements (body, h1,
    primary button, link) a rebuild can match directly."""

    css_variables: Dict[str, str] = field(default_factory=dict)
    palette_text: List[str] = field(default_factory=list)
    palette_bg: List[str] = field(default_factory=list)
    palette_border: List[str] = field(default_factory=list)
    fonts: List[str] = field(default_factory=list)
    font_sizes: List[str] = field(default_factory=list)
    radii: List[str] = field(default_factory=list)
    spacing: List[str] = field(default_factory=list)
    shadows: List[str] = field(default_factory=list)
    breakpoints: List[str] = field(default_factory=list)
    color_scheme: Optional[str] = None  # 'light' | 'dark' | 'light dark' (from color-scheme / media)
    samples: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class TechFingerprint(Serializable):
    """Best-effort stack detection from client-observable signals only.

    ``frameworks`` is the client framework(s) detected (e.g. ``["Next.js",
    "React"]``); ``server``/``powered_by``/``via`` come from the document
    response headers; ``build_data_ref`` points at a dumped framework state blob
    (``__NEXT_DATA__``, ``__NUXT__``) when present — often the richest single
    source of the app's data shapes and routes. ``evidence`` records why each
    call was made so the agent can weigh it."""

    frameworks: List[str] = field(default_factory=list)
    build_tool: Optional[str] = None  # Vite | Next.js build | Nuxt build | Webpack | ...
    libraries: List[str] = field(default_factory=list)  # Tailwind, Stripe.js, GA/GTM, Sentry, ...
    meta_generator: Optional[str] = None
    server: Optional[str] = None
    powered_by: Optional[str] = None
    via: Optional[str] = None
    build_data_ref: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The capture bundle — one page, in reconstruction-grade detail
# ---------------------------------------------------------------------------


@dataclass
class PageCapture(Serializable):
    """Everything observable about one page, structured for rebuilding it.

    This is the stable engine↔agent contract for a single page. ``*_ref`` fields
    are relative paths under the capture dir (rendered HTML, screenshot); the
    rest is inline structured data the agent reads directly.
    """

    url: str
    final_url: str
    title: str
    status: int
    ready_state: str
    lang: Optional[str] = None
    meta: Dict[str, str] = field(default_factory=dict)
    html_ref: Optional[str] = None
    html_bytes: int = 0
    dom_outline: str = ""
    content: str = ""
    interactive: List[InteractiveElement] = field(default_factory=list)
    forms: List[FormInfo] = field(default_factory=list)
    # Every labeled control on the page — including inputs that live OUTSIDE any
    # <form> (common in React apps that submit via fetch), which ``forms`` misses.
    # This is the authoritative field inventory for reconstructing the UI.
    fields: List[FormField] = field(default_factory=list)
    links: List[LinkInfo] = field(default_factory=list)
    stylesheets: List[AssetRef] = field(default_factory=list)
    scripts: List[AssetRef] = field(default_factory=list)
    assets: List[AssetRef] = field(default_factory=list)
    inline_styles: List[str] = field(default_factory=list)
    design_tokens: Optional[DesignTokens] = None
    storage: StorageSnapshot = field(default_factory=StorageSnapshot)
    console: List[ConsoleMessage] = field(default_factory=list)
    network: List[NetworkRecord] = field(default_factory=list)
    tech: TechFingerprint = field(default_factory=TechFingerprint)
    screenshot_ref: Optional[str] = None
    # Set when the capture had to bail on a sub-step (a page that never settles —
    # SSE/websocket saturation, infinite scroll). The capture is still emitted with
    # whatever was gathered; this records what was skipped so nothing looks complete
    # when it isn't.
    capture_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Recorded user path (a traced journey — the reuse of web-qa's `flow`)
# ---------------------------------------------------------------------------


@dataclass
class PathStep(Serializable):
    """One step of a recorded path: the action taken and what it caused.

    ``http``/``console`` are the *deltas* for this step (what appeared beyond the
    prior step); ``capture_ref`` points at the full :class:`PageCapture` JSON for
    the after-state, so the path stays readable while full detail is one file
    away."""

    index: int
    label: str
    action: Action
    url_before: str
    url_after: str
    http: List[NetworkRecord] = field(default_factory=list)
    console: List[ConsoleMessage] = field(default_factory=list)
    capture_ref: Optional[str] = None
    screenshot_ref: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PathRecording(Serializable):
    """A documented user path: an ordered list of steps driven in one persistent
    (optionally authenticated) context, plus the end-state storage and the tech
    fingerprint observed while traversing it."""

    name: str
    entry_url: str
    engine: BrowserEngine
    steps: List[PathStep] = field(default_factory=list)
    storage_end: StorageSnapshot = field(default_factory=StorageSnapshot)
    tech: TechFingerprint = field(default_factory=TechFingerprint)
