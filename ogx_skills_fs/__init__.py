from typing import Any

from .config import FilesystemSkillsConfig
from .impl import FilesystemSkillsImpl

__all__ = ["FilesystemSkillsConfig", "FilesystemSkillsImpl", "get_provider_impl"]


async def get_provider_impl(config: FilesystemSkillsConfig, deps: dict[Any, Any]) -> FilesystemSkillsImpl:
    impl = FilesystemSkillsImpl(config)
    await impl.initialize()
    return impl
