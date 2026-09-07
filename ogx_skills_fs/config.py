from pathlib import Path

from pydantic import BaseModel, Field


class FilesystemSkillsConfig(BaseModel):
    """Configuration for the filesystem-backed skills provider."""

    skills_dir: Path = Field(
        description=(
            "Directory containing skill bundles. Each immediate subdirectory is one "
            "skill and must contain a SKILL.md at its top level."
        ),
    )

    @classmethod
    def sample_run_config(cls, __distro_dir__: str) -> dict:
        return {"skills_dir": "/skills"}
