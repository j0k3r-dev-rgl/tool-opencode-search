# Analyzer auto-registration.
# Import each analyzer here so it registers itself into endpoints_core.common.ANALYZERS.
# Add new language analyzers by appending an import below.

from endpoints_core.analyzers import java  # noqa: F401
from endpoints_core.analyzers import typescript  # noqa: F401
