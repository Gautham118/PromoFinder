"""python -m promofinder audit --input <dir> --out <dir>   (Stage 1 only; `run` arrives in Stage 5)."""
import sys

from .audit import main as audit_main


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "audit":
        return audit_main(sys.argv[2:])
    print("usage: python -m promofinder audit --input <dir_or_file> [--out outputs/audit]\n"
          "(the one-command `run` is a later stage)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
