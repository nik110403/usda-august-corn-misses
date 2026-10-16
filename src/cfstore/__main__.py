import sys

from .store import build_store


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "store":
        print(build_store())
    elif cmd == "skeleton":
        from .skeleton import run
        run()
    elif cmd == "overlap":
        from .overlap import write
        print(write())
    elif cmd == "replication":
        from .replication import run
        sys.exit(run())
    elif cmd == "forecasts":
        from .models import run
        run()
    elif cmd == "evaluate":
        from .evaluate import run
        sys.exit(run())
    elif cmd == "preflight":
        from .evaluate import run_preflight
        sys.exit(run_preflight())
    elif cmd == "quickstats":
        from .quickstats import run
        sys.exit(run())
    elif cmd == "memo":
        from .memo import build
        print(build())
    elif cmd == "russia-store":
        from .russia import cmd_store
        print(cmd_store())
    elif cmd == "russia-forecasts":
        from .russia import cmd_forecasts
        print(cmd_forecasts())
    elif cmd == "russia-preflight":
        from .russia import run_preflight
        sys.exit(run_preflight())
    elif cmd == "russia":
        from .russia import run
        sys.exit(run())
    else:
        sys.exit("usage: python -m cfstore store|overlap|skeleton|replication|forecasts|preflight|evaluate|quickstats|russia-store|russia-forecasts|russia-preflight|russia|memo")


main()
