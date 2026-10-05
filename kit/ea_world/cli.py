"""`ea-world`: start the MCP server on stdio, or `ea-world reset` for a fresh interactive world."""

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ea-world",
        description="Maya Chen's mock workspace as an MCP server on stdio (normally started by .mcp.json or the harness).",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Run the MCP server on stdio (the default)")
    reset = sub.add_parser("reset", help="Start a fresh interactive run directory and point runs/CURRENT at it")
    reset.add_argument("--variant", default="", help="Comma-separated world variants")
    sub.add_parser("tools", help="List the tools the server serves")
    args = parser.parse_args(argv)

    if args.cmd in (None, "serve"):
        from .server import main as serve

        serve()
        return
    if args.cmd == "reset":
        from . import state

        run_dir = state.new_run_dir().resolve()
        state.init_run_dir(run_dir, args.variant)
        state.write_current(run_dir)
        print(f"New world: {run_dir}")
        print("Restart Claude Code (or the app) to use it.")
        return
    if args.cmd == "tools":
        from .server import tool_names

        for name in tool_names() + ["approval_prompt"]:
            print(name)
        return
    parser.print_help(sys.stderr)


if __name__ == "__main__":
    main()
