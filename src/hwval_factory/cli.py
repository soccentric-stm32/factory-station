#!/usr/bin/env python3
"""Command line for the Factory Data tool: no Qt, for CI and the factory station.

Author: Sandesh Ghimire <sandesh@soccentric.com>

  check    KIND CSV                 validate a CSV (format, rules, unit registry)
  generate KIND CSV --out DIR       write the files that are new or changed
  verify   DIR                      check every file of a folder against its manifest
  inspect  FILE                     list the fields of one file
  capi     DIR                      write factory_data.h/.c, the firmware C API
  units    list|set-status|next     the unit registry (units.csv)
  new-batch REPO PRODUCT BATCH      create a product/batch folder in a clone

KIND is sys, cal or diag.  Exit status: 0 all good, 1 problems found, 2 usage
or I/O error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hwval_factory.core import capi, service
from hwval_factory.core import fileformat as ff
from hwval_factory.core.generators import GENERATORS, CsvError
from hwval_factory.core.manifest import verify_folder
from hwval_factory.core.rules import Rules, RulesError
from hwval_factory.core.units import STATUSES, Registry, RegistryError
from hwval_factory.core.workspace import Workspace, WorkspaceError


def _uid(text: str | None) -> bytes | None:
    return ff.parse_uid(text) if text else None


def _show(issues) -> int:
    errors = 0
    for issue in issues:
        where = f"line {issue.line}" if issue.line else "file"
        who = f" [{issue.ident}]" if issue.ident else ""
        print(f"{issue.severity:7} {where}{who}: {issue.message}")
        errors += issue.severity == "error"
    return errors


def _context(args):
    rules = Rules.load(args.rules) if args.rules else None
    registry = Registry.load(args.units) if args.units else None
    return rules, registry


def cmd_check(args) -> int:
    gen, data = service.load(args.kind, args.csv)
    rules, registry = _context(args)
    issues = service.check(
        gen, data, revision=args.revision, uid=_uid(args.uid), rules=rules, registry=registry)
    errors = _show(issues)
    print(f"{len(data.ids)} records, {errors} errors, {len(issues) - errors} warnings")
    return 1 if errors else 0


def cmd_generate(args) -> int:
    gen, data = service.load(args.kind, args.csv)
    rules, registry = _context(args)
    uid = _uid(args.uid)
    issues = service.check(
        gen, data, revision=args.revision, uid=uid, rules=rules, registry=registry)
    blocked = {i.ident for i in issues if i.severity == "error" and i.ident}
    _show([i for i in issues if i.severity == "error"])
    plan = service.plan(
        gen, data, args.out, revision=args.revision, uid=uid, blocked=blocked)
    print(plan.summary())
    if plan.errors and not args.skip_errors:
        print("not generating: fix the errors, or pass --skip-errors", file=sys.stderr)
        return 1
    if args.dry_run:
        for name in plan.files:
            print(f"{plan.state[name]:9} {name}")
        return 0
    written, problems = service.write(
        args.out, plan.to_write(), args.kind, manifest=not args.no_manifest)
    for name in written:
        print(f"wrote {name}")
    for text in problems:
        print(f"FAILED {text}", file=sys.stderr)
    if registry is not None and args.kind == "sys":
        built = {plan.owner[n]: plan.files[n] for n in plan.files}
        try:
            changed = service.register(
                registry, data, built, product=args.product or "", batch=args.batch or "")
            registry.save()
        except RegistryError as exc:
            print(f"registry: {exc}", file=sys.stderr)
            return 1
        print(f"units.csv: {len(changed)} unit(s) added or changed")
    return 1 if problems else 0


def cmd_verify(args) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"{folder} is not a folder", file=sys.stderr)
        return 2
    bad = 0
    for verdict in verify_folder(folder):
        print(f"{'ok  ' if verdict.ok else 'FAIL'} {verdict.name}: {verdict.detail}")
        bad += not verdict.ok
    return 1 if bad else 0


def cmd_inspect(args) -> int:
    path = Path(args.file)
    parsed = ff.parse_file(path.read_bytes(), path.suffix[1:] or None)
    print(f"{parsed.kind} revision {parsed.revision} "
          f"{'bound to ' + parsed.uid.hex().upper() if parsed.bound else 'not bound'}")
    for name, value in ff.describe(parsed):
        print(f"  {name}: {value}")
    return 0


def cmd_capi(args) -> int:
    for path in capi.write(args.folder):
        print(f"wrote {path}")
    return 0


def cmd_units(args) -> int:
    registry = Registry.load(args.units)
    if args.action == "list":
        units = registry.find(args.find or "")
        if args.status:
            units = [u for u in units if u.status == args.status]
        for unit in sorted(units, key=lambda u: u.serial):
            print(f"{unit.serial:20} {unit.status:10} {unit.product:10} "
                  f"{unit.batch:8} {unit.mac:17} {unit.uid}")
        counts = ", ".join(f"{n} {s}" for s, n in registry.counts().items() if n)
        print(f"{len(units)} unit(s)" + (f"  ({counts})" if counts else ""))
        return 0
    if args.action == "set-status":
        unit = registry.set_status(args.serial, args.status, args.note or "")
        registry.save()
        print(f"{unit.serial}: {unit.status}")
        return 0
    print(registry.next_free(args.prefix, args.digits))
    return 0


def cmd_new_batch(args) -> int:
    workspace = Workspace(Path(args.repo))
    made = workspace.create_batch(args.product, args.batch)
    print(f"{workspace.base}")
    for name in made:
        print(f"  created {name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hwval-factory-data", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, with_kind=True):
        if with_kind:
            p.add_argument("kind", choices=sorted(GENERATORS))
            p.add_argument("csv")
        p.add_argument("--revision", type=int, default=1)
        p.add_argument("--uid", help="bind every file to this chip UID (24 hex digits)")
        p.add_argument("--rules", help="rules.csv of the product")
        p.add_argument("--units", help="units.csv of the repository")

    p = sub.add_parser("check")
    common(p)
    p.set_defaults(run=cmd_check)

    p = sub.add_parser("generate")
    common(p)
    p.add_argument("--out", required=True)
    p.add_argument("--dry-run", action="store_true", help="show new/changed/unchanged only")
    p.add_argument("--skip-errors", action="store_true", help="write the records without errors")
    p.add_argument("--no-manifest", action="store_true")
    p.add_argument("--product", help="product recorded in units.csv when the CSV has none")
    p.add_argument("--batch", help="batch recorded in units.csv when the CSV has none")
    p.set_defaults(run=cmd_generate)

    p = sub.add_parser("verify")
    p.add_argument("folder")
    p.set_defaults(run=cmd_verify)

    p = sub.add_parser("inspect")
    p.add_argument("file")
    p.set_defaults(run=cmd_inspect)

    p = sub.add_parser("capi")
    p.add_argument("folder")
    p.set_defaults(run=cmd_capi)

    p = sub.add_parser("units")
    p.add_argument("--units", required=True, help="units.csv")
    actions = p.add_subparsers(dest="action", required=True)
    q = actions.add_parser("list")
    q.add_argument("--find")
    q.add_argument("--status", choices=STATUSES)
    q = actions.add_parser("set-status")
    q.add_argument("serial")
    q.add_argument("status", choices=STATUSES)
    q.add_argument("--note")
    q = actions.add_parser("next")
    q.add_argument("prefix")
    q.add_argument("--digits", type=int, default=4)
    p.set_defaults(run=cmd_units)

    p = sub.add_parser("new-batch")
    p.add_argument("repo", help="the artifacts clone")
    p.add_argument("product")
    p.add_argument("batch")
    p.set_defaults(run=cmd_new_batch)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except (CsvError, ff.FileFormatError, RulesError, RegistryError, WorkspaceError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
