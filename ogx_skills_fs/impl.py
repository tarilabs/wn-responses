"""A read-only Skills provider that serves skills straight off the filesystem.

The builtin (`inline::builtin`) skills provider only accepts skills uploaded to
/v1alpha/skills at runtime -- its `initialize()` is a no-op and its config has
no notion of a source directory. That forces an out-of-band bootstrap step
(a Job, an initContainer on the client, or client-side registration).

This provider removes that step: point it at a directory, and every skill in it
exists the moment the server finishes starting. Mount a ConfigMap there and the
skill is part of the declarative deployment.

Two deliberate differences from the builtin provider:

* Skill IDs are derived from the directory name (`pizza-topping` ->
  `skill-pizza-topping`) rather than a random UUID, so clients can hardcode the
  ID instead of discovering it by listing.
* The directory is rescanned on every call, so editing the ConfigMap updates the
  skill without restarting the pod (once kubelet syncs the volume).
"""

import io
import zipfile
from pathlib import Path

from fastapi import Response, UploadFile
from ogx.providers.inline.skills.builtin.manifest import parse_skill_manifest
from ogx_api.skills import (
    ListSkillsRequest,
    ListSkillsResponse,
    ListSkillVersionsRequest,
    ListSkillVersionsResponse,
    Skill,
    SkillDeleteResponse,
    Skills,
    SkillUpdateRequest,
    SkillVersion,
    SkillVersionCreateRequest,
    SkillVersionDeleteResponse,
)

from .config import FilesystemSkillsConfig

_SKILL_MD = "SKILL.md"
_READ_ONLY = (
    "Skills are read-only in the filesystem provider. Change the mounted "
    "skills directory (e.g. the ConfigMap) instead of calling the API."
)


def _is_hidden(path: Path, root: Path) -> bool:
    """Skip ConfigMap bookkeeping entries.

    A ConfigMap volume mount is not a plain directory: it contains a `..data`
    symlink pointing at a timestamped `..2026_01_01_000000.0/` directory, plus
    a symlink per key. Walking it naively yields every file twice -- once via
    the symlink and once via the real timestamped directory.
    """
    return any(part.startswith("..") for part in path.relative_to(root).parts)


class FilesystemSkillsImpl(Skills):
    def __init__(self, config: FilesystemSkillsConfig):
        self.config = config

    async def initialize(self) -> None:
        if not self.config.skills_dir.is_dir():
            raise ValueError(f"skills_dir does not exist or is not a directory: {self.config.skills_dir}")

    async def shutdown(self) -> None:
        pass

    def _skill_dirs(self) -> dict[str, Path]:
        root = self.config.skills_dir
        out: dict[str, Path] = {}
        for entry in sorted(root.iterdir()):
            if entry.name.startswith("..") or not entry.is_dir():
                continue
            if (entry / _SKILL_MD).is_file():
                out[f"skill-{entry.name}"] = entry
        return out

    def _load(self, skill_id: str) -> tuple[Skill, Path]:
        dirs = self._skill_dirs()
        path = dirs.get(skill_id)
        if path is None:
            raise ValueError(f"Failed to find skill: '{skill_id}' does not exist")

        manifest = parse_skill_manifest((path / _SKILL_MD).read_text())
        if not manifest.name:
            raise ValueError(f"Failed to load skill '{skill_id}': SKILL.md frontmatter must include 'name'")

        version = manifest.version or "1"
        # created_at has to be stable across restarts, so use the manifest's
        # mtime rather than time.time().
        created_at = int((path / _SKILL_MD).stat().st_mtime)
        skill = Skill(
            id=skill_id,
            created_at=created_at,
            default_version=version,
            description=manifest.description or "",
            latest_version=version,
            name=manifest.name,
        )
        return skill, path

    def _zip(self, path: Path) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(path.rglob("*")):
                if f.is_file() and not _is_hidden(f, path):
                    # SKILL.md must land at the archive root; the responses
                    # provider looks it up by exact name.
                    zf.write(f, f.relative_to(path).as_posix())
        return buf.getvalue()

    def _version_of(self, skill: Skill) -> SkillVersion:
        return SkillVersion(
            id=f"skillver-{skill.id}-{skill.default_version}",
            created_at=skill.created_at,
            description=skill.description,
            name=skill.name,
            skill_id=skill.id,
            version=skill.default_version,
        )

    async def list_skills(self, request: ListSkillsRequest) -> ListSkillsResponse:
        skills = [self._load(sid)[0] for sid in self._skill_dirs()]
        skills.sort(key=lambda s: s.created_at, reverse=request.order != "asc")

        start = 0
        if request.after:
            for i, s in enumerate(skills):
                if s.id == request.after:
                    start = i + 1
                    break
        page = skills[start : start + request.limit]
        return ListSkillsResponse(
            data=page,
            has_more=start + request.limit < len(skills),
            first_id=page[0].id if page else None,
            last_id=page[-1].id if page else None,
        )

    async def get_skill(self, skill_id: str) -> Skill:
        return self._load(skill_id)[0]

    async def get_skill_content(self, skill_id: str) -> Response:
        skill, path = self._load(skill_id)
        return self._zip_response(skill, path)

    async def get_skill_version_content(self, skill_id: str, version: str) -> Response:
        skill, path = self._load(skill_id)
        if version != skill.default_version:
            raise ValueError(f"Failed to find skill version: '{skill_id}' version '{version}' does not exist")
        return self._zip_response(skill, path)

    def _zip_response(self, skill: Skill, path: Path) -> Response:
        return Response(
            content=self._zip(path),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{skill.id}_v{skill.default_version}.zip"'},
        )

    async def list_skill_versions(self, skill_id: str, request: ListSkillVersionsRequest) -> ListSkillVersionsResponse:
        skill = self._load(skill_id)[0]
        page = [self._version_of(skill)]
        return ListSkillVersionsResponse(
            data=page, has_more=False, first_id=page[0].id, last_id=page[0].id
        )

    async def get_skill_version(self, skill_id: str, version: str) -> SkillVersion:
        skill = self._load(skill_id)[0]
        if version != skill.default_version:
            raise ValueError(f"Failed to find skill version: '{skill_id}' version '{version}' does not exist")
        return self._version_of(skill)

    async def create_skill(self, file: UploadFile) -> Skill:
        raise NotImplementedError(_READ_ONLY)

    async def update_skill(self, skill_id: str, request: SkillUpdateRequest) -> Skill:
        raise NotImplementedError(_READ_ONLY)

    async def delete_skill(self, skill_id: str) -> SkillDeleteResponse:
        raise NotImplementedError(_READ_ONLY)

    async def create_skill_version(
        self, skill_id: str, request: SkillVersionCreateRequest, file: UploadFile
    ) -> SkillVersion:
        raise NotImplementedError(_READ_ONLY)

    async def delete_skill_version(self, skill_id: str, version: str) -> SkillVersionDeleteResponse:
        raise NotImplementedError(_READ_ONLY)
