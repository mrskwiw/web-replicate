"""web-replicate engine — deterministic capture instrument.

The engine is the "hands" of the web-replicate skill: it drives a headless
browser and captures, in reconstruction-grade detail, everything observable
about a page or a user path — rendered markup, stylesheets, scripts, assets,
storage, and a sanitized request/response log. It contains no AI and makes no
inferences: turning captures into a backend model and a replication blueprint is
the agent's job (see ../SKILL.md).

Borrows its architecture and much of its code from the sibling ``web-qa`` skill
(same Playwright driver, persistent-auth flows, dataclass ``to_dict`` models) —
the two are the same instrument pointed at different goals: web-qa *judges*
evidence, web-replicate *accumulates and structures* it for rebuilding.
"""

__version__ = "1.0.0-dev"
