import argparse
import logging
import sys

from . import config, runner


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="AI job radar")
    p.add_argument(
        "--mode",
        default="run",
        choices=["run", "dry", "test"],
        help="run = normal, dry = no telegram and no state save, test = check every source and send a report",
    )
    p.add_argument("--only", help="comma separated source names to run (for debugging)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.ERROR)

    cfg = config.load()
    if a.only:
        wanted = {x.strip().lower() for x in a.only.split(",")}
        cfg.sources = [s for s in cfg.sources if s["name"].lower() in wanted]
    return runner.run(cfg, a.mode)


if __name__ == "__main__":
    sys.exit(main())
