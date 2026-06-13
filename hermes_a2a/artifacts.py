"""Skill artifact bundling and installation for A2A skill_share."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home

TIER_REFERENCE = "reference"
TIER_SUMMARY = "summary"
TIER_FULL = "full"
VALID_TIERS = frozenset({TIER_REFERENCE, TIER_SUMMARY, TIER_FULL})

_BUNDLE_DIRS = ("scripts", "references", "templates")
_EXCLUDED_FILE_NAMES = frozenset({".env", ".env.example", ".env.local"})
_SECRET_NAME_RE = re.compile(r"(?:secret|credential|password|api[_-]?key)", re.I)


def _skills_root() -> Path:
    return get_hermes_home() / "skills"


def _find_skill_dir(name: str) -> Optional[Path]:
    from tools.skill_manager_tool import _find_skill

    found = _find_skill(name)
    if not found:
        return None
    return Path(found["path"])


def _parse_frontmatter(skill_md: str) -> Tuple[str, str]:
    if not skill_md.startswith("---"):
        return "", skill_md
    parts = skill_md.split("---", 2)
    if len(parts) < 3:
        return "", skill_md
    return parts[1].strip(), parts[2].strip()


def _frontmatter_field(frontmatter: str, field: str) -> str:
    for line in frontmatter.splitlines():
        if line.strip().lower().startswith(f"{field.lower()}:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def _should_exclude_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    if name in _EXCLUDED_FILE_NAMES:
        return True
    if name.startswith(".") and name not in {".gitkeep"}:
        return True
    if _SECRET_NAME_RE.search(rel_path):
        return True
    return False


def _collect_files(skill_dir: Path, tier: str) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    skill_md = skill_dir / "SKILL.md"
    if tier in {TIER_SUMMARY, TIER_FULL} and skill_md.is_file():
        rel = "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        files.append({"path": rel, "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii")})

    if tier == TIER_FULL:
        for subdir in _BUNDLE_DIRS:
            root = skill_dir / subdir
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(skill_dir)).replace("\\", "/")
                if _should_exclude_file(rel):
                    continue
                raw = path.read_bytes()
                files.append(
                    {
                        "path": rel,
                        "content_base64": base64.b64encode(raw).decode("ascii"),
                    }
                )
    return files


def _artifact_checksum(files: List[Dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda f: f.get("path", "")):
        digest.update(str(entry.get("path", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry.get("content_base64", "")).encode("ascii"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def bundle_skill(name: str, tier: str = TIER_SUMMARY) -> Dict[str, Any]:
    """Build a skill_share artifact payload for the given skill."""
    tier = (tier or TIER_SUMMARY).strip().lower()
    if tier not in VALID_TIERS:
        return {"success": False, "error": f"Invalid tier '{tier}'. Use reference, summary, or full."}

    skill_dir = _find_skill_dir(name)
    if skill_dir is None:
        return {"success": False, "error": f"Skill '{name}' not found."}

    skill_md_path = skill_dir / "SKILL.md"
    description = ""
    if skill_md_path.is_file():
        fm, _body = _parse_frontmatter(skill_md_path.read_text(encoding="utf-8"))
        description = _frontmatter_field(fm, "description")

    files = _collect_files(skill_dir, tier)
    artifact: Dict[str, Any] = {
        "files": files,
        "checksum": _artifact_checksum(files) if files else "sha256:" + hashlib.sha256(b"").hexdigest(),
    }
    if tier == TIER_REFERENCE:
        artifact["reference"] = {
            "name": name,
            "description": description,
            "source_peer_skill": name,
        }

    total_bytes = sum(len(base64.b64decode(f["content_base64"])) for f in files)
    return {
        "success": True,
        "skill_name": name,
        "tier": tier,
        "artifact": artifact,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "description": description,
    }


def artifact_preview(payload: Dict[str, Any]) -> str:
    """Human-readable summary of a skill_share payload."""
    skill_name = str(payload.get("skill_name") or "")
    tier = str(payload.get("tier") or "")
    message = str(payload.get("message") or "").strip()
    artifact = payload.get("artifact") or {}
    files = artifact.get("files") or []
    lines = [
        f"Skill: {skill_name}",
        f"Tier: {tier}",
    ]
    ref = artifact.get("reference") or {}
    if ref:
        desc = str(ref.get("description") or "").strip()
        if desc:
            lines.append(f"Description: {desc}")
    if files:
        lines.append("Files:")
        for entry in files:
            rel = str(entry.get("path") or "")
            try:
                size = len(base64.b64decode(str(entry.get("content_base64") or "")))
            except Exception:
                size = 0
            lines.append(f"  - {rel} ({size:,} bytes)")
    else:
        lines.append("Files: (reference only — no file transfer)")
    if message:
        lines.append(f"Message: {message}")
    checksum = str(artifact.get("checksum") or "")
    if checksum:
        lines.append(f"Checksum: {checksum}")
    return "\n".join(lines)


def verify_artifact_checksum(artifact: Dict[str, Any]) -> bool:
    files = artifact.get("files") or []
    expected = str(artifact.get("checksum") or "")
    if not expected:
        return False
    return _artifact_checksum(files) == expected


def install_skill_artifact(
    skill_name: str,
    artifact: Dict[str, Any],
    *,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """Install a received artifact into ~/.hermes/skills/<name>/."""
    if not verify_artifact_checksum(artifact):
        return {"success": False, "error": "Artifact checksum mismatch — refusing install."}

    ref = artifact.get("reference") or {}
    if ref and not (artifact.get("files") or []):
        return {
            "success": True,
            "installed": False,
            "reference_only": True,
            "message": (
                f"Reference skill '{skill_name}' recorded (no files to install). "
                f"Description: {ref.get('description', '')}"
            ),
        }

    files = artifact.get("files") or []
    skill_md_b64 = None
    extra_files: List[Dict[str, str]] = []
    for entry in files:
        rel = str(entry.get("path") or "").replace("\\", "/")
        if rel == "SKILL.md":
            skill_md_b64 = str(entry.get("content_base64") or "")
        else:
            extra_files.append(entry)

    if not skill_md_b64:
        return {"success": False, "error": "Artifact is missing SKILL.md."}

    try:
        skill_md = base64.b64decode(skill_md_b64).decode("utf-8")
    except Exception as exc:
        return {"success": False, "error": f"Invalid SKILL.md encoding: {exc}"}

    from tools.skill_manager_tool import apply_skill_pending, skill_manage

    create_payload = {
        "action": "create",
        "name": skill_name,
        "content": skill_md,
    }
    if category:
        create_payload["category"] = category

    create_result = json.loads(apply_skill_pending(create_payload))
    if not create_result.get("success"):
        return create_result

    for entry in extra_files:
        rel = str(entry.get("path") or "").replace("\\", "/")
        if _should_exclude_file(rel):
            continue
        try:
            content = base64.b64decode(str(entry.get("content_base64") or "")).decode("utf-8")
        except Exception:
            content = base64.b64decode(str(entry.get("content_base64") or "")).decode("utf-8", errors="replace")
        write_payload = {
            "action": "write_file",
            "name": skill_name,
            "file_path": rel,
            "file_content": content,
        }
        write_result = json.loads(apply_skill_pending(write_payload))
        if not write_result.get("success"):
            return write_result

    skill_dir = _find_skill_dir(skill_name)
    if skill_dir is not None:
        from tools.skill_manager_tool import _security_scan_skill

        scan_error = _security_scan_skill(skill_dir)
        if scan_error:
            skill_manage(action="delete", name=skill_name, absorbed_into="")
            return {"success": False, "error": scan_error}

    return {
        "success": True,
        "installed": True,
        "skill_name": skill_name,
        "message": f"Skill '{skill_name}' installed from peer artifact.",
    }
