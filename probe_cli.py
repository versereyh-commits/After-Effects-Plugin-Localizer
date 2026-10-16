"""Reproducible, non-GUI test entry point."""
import argparse
import json
from pathlib import Path
from core import inspect_plugin, resources, scan, plugin_roots, file_hash
from runtime import native_probe, run_catalog_probe

parser = argparse.ArgumentParser()
parser.add_argument("command",choices=["scan","native","catalog","pipl","live"])
parser.add_argument("--path")
parser.add_argument("--output")
parser.add_argument("--mutate",action="store_true")
parser.add_argument("--match",action="append",help="live：只输出这些内部标识（可重复）")
args = parser.parse_args()
if args.command == "native":
    result = native_probe()
elif args.command == "catalog":
    result = run_catalog_probe(Path(args.path),args.mutate,folder=Path(args.output) if args.output else None)
    result = {k:v for k,v in result.items() if k not in ("catalog","nativeProbe")}
elif args.command == "live":
    # Read-only evidence of AE's real catalog: opens AE without write access and never refreshes its UI.
    from live_names import read_live, NameSession
    live = read_live()
    effects = live.pop("effects")
    live["effectCount"] = len(effects)
    live["effects"] = [e for e in effects if not args.match or e["matchName"] in args.match]
    live["changedByThisTool"] = {k:{"originalName":v["originalName"],"newName":v["newName"]} for k,v in NameSession().journal()["entries"].items()}
    result = live
    if args.output:
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
elif args.command == "pipl":
    result = [{"resource":p,"head":v[:100].hex()} for p,v in resources(Path(args.path)) if str(p[0]).upper()=="PIPL"]
else:
    from dataclasses import asdict
    plugins,errors = scan([Path(args.path)] if args.path else plugin_roots())
    result = {"plugins":[asdict(p) for p in plugins],"errors":errors}
    if args.output:
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        result = {"count":len(plugins),"errors":len(errors),"output":args.output}
print(json.dumps(result,ensure_ascii=False,indent=2))
