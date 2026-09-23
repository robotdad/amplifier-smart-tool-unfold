"""Credential aliases only; never select a provider or model from the environment."""

import os

CREDENTIALS = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
}


def credential(provider, environment=None):
    """First nonempty alias wins. Return its name and value, never persist the value."""
    environment = os.environ if environment is None else environment
    for name in CREDENTIALS[provider]:
        if environment.get(name):
            return name, environment[name]
    return None, None


def worker_environment(provider):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "USER", "TMPDIR", "SYSTEMROOT", "VIRTUAL_ENV", "PYTHONPATH"}
    }
    _, value = credential(provider)
    if value:
        # Normalize the chosen alias, disclosing no other provider's credentials.
        environment[CREDENTIALS[provider][0]] = value
    return environment
