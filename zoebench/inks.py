"""Alternate module name for the two-level ink separation workflow."""

from .separation import *  # noqa: F401,F403
from . import separation  # noqa: F401

# Re-export every public name explicitly for ``from zoebench.inks import ...``.
for _name in dir(separation):
    if not _name.startswith("_"):
        globals()[_name] = getattr(separation, _name)
