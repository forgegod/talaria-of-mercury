"""Allow ``python -m talaria.cli`` as well as the ``talaria`` script."""

from talaria.cli import main

if __name__ == "__main__":
    raise SystemExit(main())