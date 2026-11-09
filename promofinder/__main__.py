"""python -m promofinder run|audit ..."""
import sys

from .audit import main as audit_main


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "audit":
        return audit_main(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        from .runner import main as run_main
        return run_main(sys.argv[2:])
    print("usage: python -m promofinder run --input <dir_or_file> [--provider P --model M --out DIR --only E001,E002 --fresh]\n"
          "       python -m promofinder audit --input <dir_or_file> [--out outputs/audit]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
