"""Peer registry — local identity, inbound auth, and remote peers."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from hermes_constants import get_hermes_home

_REGISTRY_FILE_MODE = 0o600


def peers_dir() -> Path:
    path = get_hermes_home() / "peers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _default_registry() -> Dict[str, Any]:
    return {
        "local_id": "",
        "inbound_token_hash": "",
        "remotes": {},
        "trust": {},
    }


class PeerRegistry:
    """Load/save ``~/.hermes/peers/registry.yaml``."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path_override = path

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        return peers_dir() / "registry.yaml"

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return _default_registry()
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception:
            return _default_registry()
        if not isinstance(raw, dict):
            return _default_registry()
        data = _default_registry()
        data["local_id"] = str(raw.get("local_id") or "").strip()
        data["inbound_token_hash"] = str(raw.get("inbound_token_hash") or "").strip()
        remotes = raw.get("remotes") or {}
        data["remotes"] = remotes if isinstance(remotes, dict) else {}
        trust = raw.get("trust") or {}
        data["trust"] = trust if isinstance(trust, dict) else {}
        return data

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "local_id": str(data.get("local_id") or "").strip(),
            "inbound_token_hash": str(data.get("inbound_token_hash") or "").strip(),
            "remotes": data.get("remotes") or {},
            "trust": data.get("trust") or {},
        }
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, _REGISTRY_FILE_MODE)
        tmp.replace(self.path)
        os.chmod(self.path, _REGISTRY_FILE_MODE)

    def get_local_id(self) -> str:
        return self.load()["local_id"]

    def set_local_id(self, local_id: str) -> None:
        data = self.load()
        data["local_id"] = local_id.strip()
        self.save(data)

    def ensure_inbound_token(self) -> str:
        """Return the inbound bearer token, generating one if missing."""
        env_token = os.getenv("HERMES_A2A_INBOUND_TOKEN", "").strip()
        if env_token:
            data = self.load()
            data["inbound_token_hash"] = _hash_token(env_token)
            self.save(data)
            return env_token

        data = self.load()
        if data.get("inbound_token_hash"):
            raise RuntimeError(
                "Inbound token hash is set but the raw token is unknown. "
                "Set HERMES_A2A_INBOUND_TOKEN or run `hermes peer token --rotate`."
            )
        token = secrets.token_urlsafe(32)
        data["inbound_token_hash"] = _hash_token(token)
        if not data.get("local_id"):
            data["local_id"] = "hermes"
        self.save(data)
        return token

    def rotate_inbound_token(self) -> str:
        token = secrets.token_urlsafe(32)
        data = self.load()
        data["inbound_token_hash"] = _hash_token(token)
        self.save(data)
        return token

    def set_inbound_token(self, token: str) -> None:
        token = token.strip()
        if not token:
            raise ValueError("Token must not be empty")
        data = self.load()
        data["inbound_token_hash"] = _hash_token(token)
        self.save(data)

    def verify_inbound_token(self, token: str) -> bool:
        token = (token or "").strip()
        if not token:
            return False
        env_token = os.getenv("HERMES_A2A_INBOUND_TOKEN", "").strip()
        if env_token and secrets.compare_digest(token, env_token):
            return True
        data = self.load()
        expected = data.get("inbound_token_hash") or ""
        if not expected:
            return False
        return secrets.compare_digest(_hash_token(token), expected)

    def pair_remote(
        self,
        peer_id: str,
        url: str,
        token: Optional[str] = None,
    ) -> str:
        peer_id = peer_id.strip().lower()
        url = url.strip()
        if not peer_id:
            raise ValueError("peer id is required")
        if not url:
            raise ValueError("peer url is required")
        if token is None or not str(token).strip():
            token = secrets.token_urlsafe(32)
        else:
            token = str(token).strip()

        data = self.load()
        data.setdefault("remotes", {})[peer_id] = {
            "url": url,
            "token": token,
        }
        self.save(data)
        return token

    def get_remote(self, peer_id: str) -> Optional[Dict[str, str]]:
        peer_id = peer_id.strip().lower()
        remote = (self.load().get("remotes") or {}).get(peer_id)
        if not isinstance(remote, dict):
            return None
        url = str(remote.get("url") or "").strip()
        token = str(remote.get("token") or "").strip()
        if not url:
            return None
        return {"peer_id": peer_id, "url": url, "token": token}

    def list_remotes(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for peer_id, remote in sorted((self.load().get("remotes") or {}).items()):
            if not isinstance(remote, dict):
                continue
            url = str(remote.get("url") or "").strip()
            if not url:
                continue
            out.append({"peer_id": peer_id, "url": url})
        return out

    def revoke_remote(self, peer_id: str) -> bool:
        peer_id = peer_id.strip().lower()
        data = self.load()
        remotes = data.get("remotes") or {}
        if peer_id not in remotes:
            return False
        del remotes[peer_id]
        data["remotes"] = remotes
        self.save(data)
        return True

    def trust_is_always(self, peer_id: str, request_type: str) -> bool:
        peer_id = peer_id.strip().lower()
        trust = (self.load().get("trust") or {}).get(peer_id) or {}
        if not isinstance(trust, dict):
            return False
        return bool(trust.get(request_type) == "always")

    def set_trust(self, peer_id: str, request_type: str, mode: str) -> None:
        peer_id = peer_id.strip().lower()
        data = self.load()
        trust = data.setdefault("trust", {})
        peer_trust = trust.setdefault(peer_id, {})
        if not isinstance(peer_trust, dict):
            peer_trust = {}
            trust[peer_id] = peer_trust
        if mode == "always":
            peer_trust[request_type] = "always"
        else:
            peer_trust.pop(request_type, None)
        self.save(data)
