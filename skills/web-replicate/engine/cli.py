"""Engine CLI — the agent's hands (``capture`` / ``trace`` / ``blueprint``).

The engine is a deterministic capture instrument; orchestration (which pages,
which journeys, how deep) and all inference live in the agent workflow
(``SKILL.md``), not here.

Invoke as a module from the skill dir::

    python -m engine.cli capture --url https://example.com --out-dir cap/home
    python -m engine.cli trace --url https://example.com --steps signup.json --out-dir cap/signup
    python -m engine.cli blueprint --input results.json --output ../../../blueprints/example-2026-07-23
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict

import click

from .authcheck import parse_subs, probe_endpoints, resolve_base_url
from .blueprint import BlueprintGenerator
from .browser import CaptureController
from .flow import build_action, slug
from .models import BrowserEngine, PathRecording, PathStep

_ENGINE_CHOICE = click.Choice([e.value for e in BrowserEngine])


def _emit(payload: Dict[str, Any], output: str | None) -> None:
    """Print JSON to stdout, and also write it to ``output`` when given."""
    text = json.dumps(payload, indent=2)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text, encoding="utf-8")
    click.echo(text)


def _load_session(session: str | None) -> tuple[Any, str | None]:
    """Load a saved auth session bundle → (storage_state, user_agent). Same format
    as web-qa's ``flow --save-session`` output (reused verbatim)."""
    if not session:
        return None, None
    data = json.loads(Path(session).read_text(encoding="utf-8"))
    return data.get("storage_state"), data.get("user_agent")


def _controller(
    out_dir: str,
    engine: str,
    headless: bool,
    session: str | None,
    user_agent: str | None,
    redact: bool,
    download_assets: bool,
) -> CaptureController:
    storage_state, session_ua = _load_session(session)
    return CaptureController(
        out_dir=out_dir,
        engine=engine,
        headless=headless,
        storage_state=storage_state,
        user_agent=user_agent or session_ua,
        redact=redact,
        download_assets=download_assets,
    )


# Options shared by capture & trace (session/auth/safety).
def _common_options(fn):
    fn = click.option(
        "--browser", "engine", default=BrowserEngine.CHROMIUM.value, type=_ENGINE_CHOICE
    )(fn)
    fn = click.option("--headless/--no-headless", default=True)(fn)
    fn = click.option(
        "--session",
        type=click.Path(exists=True),
        default=None,
        help="Reuse a saved auth session (from `trace --save-session`) so the run is logged in.",
    )(fn)
    fn = click.option(
        "--user-agent",
        default=None,
        help="Override the user-agent (defaults to the one saved in --session).",
    )(fn)
    fn = click.option(
        "--include-secrets",
        is_flag=True,
        default=False,
        help="Do NOT redact auth headers / cookies / credential fields. Only on a "
        "target you own, when raw values are genuinely needed.",
    )(fn)
    fn = click.option(
        "--download-assets",
        is_flag=True,
        default=False,
        help="Download stylesheet/script/image/font bytes into the capture dir.",
    )(fn)
    fn = click.option(
        "--screenshot", is_flag=True, default=False, help="Also capture a full-page screenshot."
    )(fn)
    return fn


@click.group()
def cli() -> None:
    """web-replicate deterministic capture engine."""


@cli.command()
@click.option("--url", required=True, help="Page to capture.")
@click.option(
    "--out-dir",
    required=True,
    type=click.Path(),
    help="Capture directory. Receives page.json + pages/ bodies/ styles/ tech/ screenshots/.",
)
@_common_options
@click.option(
    "--output", type=click.Path(), default=None, help="Also write the page capture JSON here."
)
def capture(
    url: str,
    out_dir: str,
    engine: str,
    headless: bool,
    session: str | None,
    user_agent: str | None,
    include_secrets: bool,
    download_assets: bool,
    screenshot: bool,
    output: str | None,
) -> None:
    """Navigate to URL and capture one page in reconstruction-grade detail."""

    async def run():
        controller = _controller(
            out_dir, engine, headless, session, user_agent,
            redact=not include_secrets, download_assets=download_assets,
        )
        await controller.launch()
        try:
            controller.set_entry_url(url)
            await controller.navigate(url)
            network = await controller.harvest_network(since=0)
            return await controller.capture_page(
                "page", network, screenshot=screenshot
            )
        finally:
            await controller.close()

    page = asyncio.run(run())
    payload = page.to_dict()
    # Always persist the manifest inside the capture dir too, so a capture dir is
    # self-describing regardless of --output.
    (Path(out_dir) / "page.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    _emit(payload, output)


@cli.command()
@click.option("--url", required=True, help="Entry URL loaded once before step 1.")
@click.option(
    "--steps",
    "steps_path",
    required=True,
    type=click.Path(exists=True),
    help="Steps JSON: a list, or {'steps': [...]}. Each step is an action + optional 'intent'.",
)
@click.option(
    "--out-dir",
    required=True,
    type=click.Path(),
    help="Capture directory for this path (per-step captures land in steps/).",
)
@click.option("--name", default=None, help="Human name for this path (defaults to the steps filename).")
@_common_options
@click.option(
    "--save-session",
    type=click.Path(),
    default=None,
    help="After the trace, save the context's auth session (cookies + UA) here for "
    "reuse via --session. Run a login path ONCE and replay it (web-qa pattern).",
)
@click.option(
    "--output", type=click.Path(), default=None, help="Also write the path recording JSON here."
)
def trace(
    url: str,
    steps_path: str,
    out_dir: str,
    name: str | None,
    engine: str,
    headless: bool,
    session: str | None,
    user_agent: str | None,
    include_secrets: bool,
    download_assets: bool,
    screenshot: bool,
    save_session: str | None,
    output: str | None,
) -> None:
    """Record a user path: drive ordered steps in ONE persistent context, capturing
    a full page capture + network delta at each step. Secrets are referenced by env
    var (``{"env":"VAR"}`` / ``${VAR}``), never inlined."""
    raw = json.loads(Path(steps_path).read_text(encoding="utf-8"))
    steps = raw["steps"] if isinstance(raw, dict) else raw
    path_name = name or Path(steps_path).stem
    env = os.environ

    async def run():
        controller = _controller(
            out_dir, engine, headless, session, user_agent,
            redact=not include_secrets, download_assets=download_assets,
        )
        await controller.launch()
        recorded: list[PathStep] = []
        storage_end = None
        tech = None
        try:
            controller.set_entry_url(url)
            await controller.navigate(url)
            for i, step in enumerate(steps):
                label = step.get("label") or f"step-{i + 1}"
                action = build_action(step, env)
                url_before = controller.page_url
                net_mark = controller.response_count()
                con_mark = controller.console_count()
                error = None
                try:
                    await controller.perform(action)
                    aw = step.get("await_response")
                    if aw:
                        await controller.wait_for_api(
                            aw["path_contains"],
                            method=aw.get("method"),
                            since=net_mark,
                            timeout_ms=int(aw.get("timeout_ms", 20000)),
                        )
                    if step.get("settle_ms"):
                        await controller.settle(int(step["settle_ms"]))
                except Exception as exc:  # noqa: BLE001 — record & keep tracing the path
                    # ``optional`` steps (a cookie banner that may not be present) never
                    # fail the recording — with the short action timeout they cost ~6s at
                    # most and are simply skipped when the element is absent.
                    error = None if step.get("optional") else str(exc)
                network = await controller.harvest_network(since=net_mark)
                cap_name = f"{i + 1:02d}-{slug(label)}"
                page_capture = await controller.capture_page(
                    cap_name, network, screenshot=screenshot
                )
                cap_ref = (Path("steps") / f"{cap_name}.json").as_posix()
                (Path(out_dir) / "steps" / f"{cap_name}.json").parent.mkdir(
                    parents=True, exist_ok=True
                )
                (Path(out_dir) / cap_ref).write_text(
                    json.dumps(page_capture.to_dict(), indent=2), encoding="utf-8"
                )
                recorded.append(
                    PathStep(
                        index=i + 1,
                        label=label,
                        action=action,
                        url_before=url_before,
                        url_after=controller.page_url,
                        http=network,
                        console=controller.console_since(con_mark),
                        capture_ref=cap_ref,
                        screenshot_ref=page_capture.screenshot_ref,
                        error=error,
                    )
                )
                tech = page_capture.tech
            storage_end = await controller.capture_storage()
            if save_session:
                try:
                    await controller.save_session(save_session, user_agent=user_agent)
                except Exception as exc:  # noqa: BLE001
                    click.echo(f"warning: save-session failed: {exc}", err=True)
        finally:
            await controller.close()
        from .models import StorageSnapshot, TechFingerprint

        return PathRecording(
            name=path_name,
            entry_url=url,
            engine=BrowserEngine(engine),
            steps=recorded,
            storage_end=storage_end or StorageSnapshot(),
            tech=tech or TechFingerprint(),
        )

    recording = asyncio.run(run())
    payload = recording.to_dict()
    (Path(out_dir) / "path.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    _emit(payload, output)


@cli.command()
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True),
    help="Assembled results JSON (metadata + tech + backend + pages + paths).",
)
@click.option(
    "--output", type=click.Path(), default="./blueprint", help="Blueprint output directory."
)
def blueprint(input_path: str, output: str) -> None:
    """Render blueprint.md / blueprint.json / backend.json from assembled results."""
    results = json.loads(Path(input_path).read_text(encoding="utf-8"))
    paths = BlueprintGenerator(output_dir=output).render(results)
    click.echo(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))


@cli.command("verify-auth")
@click.option(
    "--backend",
    "backend_path",
    required=True,
    type=click.Path(exists=True),
    help="backend.json (or blueprint.json / results.json) holding endpoints[] + base_url.",
)
@click.option(
    "--session",
    required=True,
    type=click.Path(exists=True),
    help="Authenticated session bundle (from `trace --save-session`) — the with-auth baseline.",
)
@click.option(
    "--base-url",
    default=None,
    help="Origin to probe (e.g. https://app.example.com). Default: parsed from backend base_url.",
)
@click.option(
    "--sub",
    "subs",
    multiple=True,
    help="Path-param substitution key=value (repeatable), e.g. --sub id=<a-real-uuid>. "
    "Endpoints with unresolved {params} are skipped-and-reported.",
)
@click.option(
    "--include-mutating",
    is_flag=True,
    default=False,
    help="Also probe POST/PUT/PATCH/DELETE. These send REAL requests that WOULD execute "
    "on an unprotected endpoint — only on a test target you own. Default: skip (safe).",
)
@click.option("--timeout-ms", default=15000, help="Per-request timeout.")
@click.option("--user-agent", default=None, help="Override UA (defaults to the session's UA).")
@click.option("--output", type=click.Path(), default=None, help="Also write the report JSON here.")
def verify_auth(
    backend_path: str,
    session: str,
    base_url: str | None,
    subs: tuple[str, ...],
    include_mutating: bool,
    timeout_ms: int,
    user_agent: str | None,
    output: str | None,
) -> None:
    """Verify the inferred auth column: probe each observed endpoint with/without the
    session and report enforced / public / error-unauth, flagging every correction.

    The engine's ONE active check — read-only (GET) by default, and only against
    endpoints you already observed (never enumerates hidden routes). Use on a target
    you own to upgrade the blueprint's *inferred* auth to *verified*.
    """
    data = json.loads(Path(backend_path).read_text(encoding="utf-8"))
    backend = data.get("backend", data)  # accept backend.json or a full results/blueprint object
    endpoints = backend.get("endpoints") or []
    if not endpoints:
        raise click.ClickException("No endpoints[] found in --backend input.")
    origin = resolve_base_url(backend.get("base_url"), base_url)
    if not origin:
        raise click.ClickException("Could not resolve a base URL; pass --base-url explicitly.")

    storage_state, session_ua = _load_session(session)
    report = asyncio.run(
        probe_endpoints(
            base_url=origin,
            endpoints=endpoints,
            storage_state=storage_state,
            user_agent=user_agent or session_ua,
            subs=parse_subs(list(subs)),
            include_mutating=include_mutating,
            timeout_ms=timeout_ms,
        )
    )
    _emit(report, output)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
