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
    else:
        sys.exit("usage: python -m cfstore store|overlap|skeleton|replication")


main()
