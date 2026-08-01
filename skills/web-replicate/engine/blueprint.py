"""Blueprint rendering — the replication analog of web-qa's ``reporting``.

Renders an assembled results object (the agent builds it from ``capture`` +
``trace`` outputs plus its own backend inference) into a rebuild-oriented
deliverable: ``blueprint.md`` (the human-readable reconstruction spec),
``blueprint.json`` (the full assembled record), and ``backend.json`` (just the
inferred backend contract, for feeding a codegen step).

Works on plain dicts so the agent hands back JSON without the engine needing to
reconstruct every dataclass — same posture as web-qa's ReportGenerator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class BlueprintGenerator:
    def __init__(self, output_dir: str = "./blueprint") -> None:
        self.output_dir = Path(output_dir)

    def render(self, results: Dict[str, Any]) -> Dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        blueprint_json = self.output_dir / "blueprint.json"
        blueprint_json.write_text(json.dumps(results, indent=2), encoding="utf-8")

        backend = results.get("backend", {})
        backend_json = self.output_dir / "backend.json"
        backend_json.write_text(json.dumps(backend, indent=2), encoding="utf-8")

        blueprint_md = self.output_dir / "blueprint.md"
        blueprint_md.write_text(self._markdown(results), encoding="utf-8")

        return {
            "markdown": blueprint_md,
            "json": blueprint_json,
            "backend": backend_json,
        }

    # -- markdown ---------------------------------------------------------

    def _markdown(self, r: Dict[str, Any]) -> str:
        meta = r.get("metadata", {})
        out: List[str] = []
        out.append("# Replication Blueprint\n")
        out.append(f"- **Target:** {meta.get('target_url', '?')}")
        out.append(f"- **Captured:** {meta.get('captured_at', '?')} · engine {meta.get('engine', '?')}")
        out.append(
            f"- **Coverage:** {len(r.get('pages', []))} page(s), "
            f"{len(r.get('paths', []))} user path(s)\n"
        )
        if meta.get("note"):
            out.append(f"> {meta['note']}\n")

        self._tech_section(out, r.get("tech", {}))
        self._backend_section(out, r.get("backend", {}))
        self._paths_section(out, r.get("paths", []))
        self._frontend_section(out, r.get("pages", []))
        self._artifacts_section(out, r)

        return "\n".join(out) + "\n"

    def _tech_section(self, out: List[str], tech: Dict[str, Any]) -> None:
        if not tech:
            return
        out.append("## Tech stack (observed)\n")
        fw = tech.get("frameworks") or []
        if fw:
            out.append(f"- **Frontend:** {', '.join(fw)}")
        if tech.get("build_tool"):
            out.append(f"- **Build tool:** {tech['build_tool']}")
        libs = tech.get("libraries") or []
        if libs:
            out.append(f"- **Libraries:** {', '.join(libs)}")
        for key in ("meta_generator", "server", "powered_by", "via"):
            if tech.get(key):
                out.append(f"- **{key.replace('_', ' ').title()}:** `{tech[key]}`")
        ev = tech.get("evidence") or []
        if ev:
            out.append(f"- _Evidence:_ {'; '.join(ev[:8])}")
        out.append("")

    def _backend_section(self, out: List[str], backend: Dict[str, Any]) -> None:
        if not backend:
            return
        out.append("## Backend model (inferred)\n")
        if backend.get("base_url"):
            out.append(f"- **API base:** `{backend['base_url']}`")
        if backend.get("auth"):
            out.append(f"- **Auth:** {backend['auth']}")
        for note in backend.get("notes", []) or []:
            out.append(f"- {note}")
        out.append("")

        endpoints = backend.get("endpoints", []) or []
        if endpoints:
            out.append("### Endpoints\n")
            out.append("| Method | Path | Purpose | Auth | Request → Response |")
            out.append("|---|---|---|---|---|")
            for e in endpoints:
                req = e.get("request_shape", "") or ""
                res = e.get("response_shape", "") or ""
                shape = f"{req} → {res}".strip(" →")
                out.append(
                    f"| `{e.get('method', '?')}` | `{e.get('path', '?')}` | "
                    f"{e.get('purpose', '')} | {e.get('auth_required', '?')} | {shape} |"
                )
            out.append("")

        models = backend.get("data_models", []) or []
        if models:
            out.append("### Data models\n")
            for m in models:
                out.append(f"- **{m.get('name', '?')}** — {', '.join(m.get('fields', []) or [])}")
            out.append("")

    def _paths_section(self, out: List[str], paths: List[Dict[str, Any]]) -> None:
        if not paths:
            return
        out.append("## User paths\n")
        for p in paths:
            out.append(f"### {p.get('name', '(unnamed path)')}")
            if p.get("entry_url"):
                out.append(f"Entry: `{p['entry_url']}`\n")
            steps = p.get("steps", []) or []
            for s in steps:
                act = s.get("action", {}) if isinstance(s.get("action"), dict) else {}
                verb = act.get("type", "?")
                target = act.get("selector") or act.get("url") or act.get("value") or ""
                intent = s.get("intent") or act.get("intent") or s.get("label", "")
                line = f"{s.get('index', '?')}. **{verb}** `{target}`"
                if intent:
                    line += f" — {intent}"
                out.append(line)
                calls = s.get("http", []) or []
                api = [c for c in calls if c.get("resource_type") in ("xhr", "fetch")]
                for c in api[:6]:
                    out.append(f"   - `{c.get('method')} {c.get('url')}` → {c.get('status')}")
                if s.get("error"):
                    out.append(f"   - ⚠️ error: {s['error']}")
            out.append("")

    def _frontend_section(self, out: List[str], pages: List[Dict[str, Any]]) -> None:
        if not pages:
            return
        out.append("## Frontend (per page)\n")
        for p in pages:
            out.append(f"### {p.get('title') or p.get('final_url', '?')}")
            out.append(f"- **URL:** `{p.get('final_url', p.get('url', '?'))}`")
            if p.get("html_ref"):
                out.append(f"- **Markup:** `{p['html_ref']}` ({p.get('html_bytes', 0)} bytes)")
            forms = p.get("forms", []) or []
            if forms:
                out.append(f"- **Forms:** {len(forms)} "
                           f"({', '.join(f.get('action') or '(js)' for f in forms)})")
            css = p.get("stylesheets", []) or []
            scripts = p.get("scripts", []) or []
            out.append(f"- **Assets:** {len(css)} stylesheet(s), {len(scripts)} script(s), "
                       f"{len(p.get('assets', []) or [])} media")
            store = p.get("storage", {}) or {}
            keys = list((store.get("local") or {}).keys())
            if keys:
                out.append(f"- **localStorage keys:** {', '.join(keys[:12])}")
            self._design_tokens_lines(out, p.get("design_tokens"))
            if p.get("screenshot_ref"):
                out.append(f"\n![{p.get('title', 'page')}]({p['screenshot_ref']})")
            out.append("")

    def _design_tokens_lines(self, out: List[str], tokens: Any) -> None:
        """Render the captured design system (palette / fonts / radii / CSS vars) — the
        raw material a faithful visual rebuild reproduces. Only emits what was found."""
        if not tokens:
            return
        palette = (
            (tokens.get("palette_text") or [])
            + (tokens.get("palette_bg") or [])
            + (tokens.get("palette_border") or [])
        )
        bits = []
        if palette:
            uniq = list(dict.fromkeys(palette))[:12]
            bits.append(f"colors {', '.join(uniq)}")
        if tokens.get("fonts"):
            bits.append(f"fonts {', '.join(tokens['fonts'][:4])}")
        if tokens.get("font_sizes"):
            bits.append(f"{len(tokens['font_sizes'])} font-size(s)")
        if tokens.get("radii"):
            bits.append(f"radii {', '.join(str(x) for x in tokens['radii'][:6])}")
        nvars = len(tokens.get("css_variables") or {})
        if nvars:
            bits.append(f"{nvars} CSS variable(s)")
        if tokens.get("color_scheme"):
            bits.append(f"scheme {tokens['color_scheme']}")
        if bits:
            out.append(f"- **Design tokens:** {'; '.join(bits)}")

    def _artifacts_section(self, out: List[str], r: Dict[str, Any]) -> None:
        out.append("## Captured artifacts\n")
        out.append("All raw material referenced above lives under this blueprint's "
                   "capture directory — rendered HTML (`pages/`), response bodies "
                   "(`bodies/`), inline CSS (`styles/`), framework state (`tech/`), "
                   "downloaded assets (`assets/`), and screenshots (`screenshots/`).")
        out.append("")
