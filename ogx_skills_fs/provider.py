"""Provider spec discovered by OGX.

The resolver imports `<module>.provider` and calls `get_provider_spec()` when a
provider entry in the run config carries a `module:` key. Note that it imports
`f"{package_name}.provider"`, so this file has to be named exactly `provider.py`
at the package root.

Unlike the builtin skills provider this declares no `api_dependencies`: skills
are read from disk, so the Files API is not needed to store bundles.
"""

from ogx_api import InlineProviderSpec
from ogx_api.datatypes import Api


def get_provider_spec() -> InlineProviderSpec:
    return InlineProviderSpec(
        api=Api.skills,
        provider_type="inline::filesystem",
        module="ogx_skills_fs",
        config_class="ogx_skills_fs.config.FilesystemSkillsConfig",
        description="Read-only skills provider serving SKILL.md bundles from a mounted directory.",
        api_dependencies=[],
        is_external=True,
    )
