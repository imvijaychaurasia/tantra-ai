"""
Allow running the Tantra CLI as a module:
    python -m tantra [args...]

This is used by the bin/tantra wrapper script so the CLI works
on the host server without pip-installing the package.
"""
from tantra.cli import app

if __name__ == "__main__":
    app()
