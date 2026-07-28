from __future__ import annotations

import argparse
from pathlib import Path

from .analyzer import analyze
from .project_io import package_project, prepare_project
from .repairer import repair
from .reporting import build_report, write_report
from .rules import load_rules, rules_for_source_kind
from .scoring import assess_risk
from .text_io import read_text_best_effort, write_text_with_encoding
from .verifier import compile_tex, explain_compile_failure


def run_check(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    project = prepare_project(args.input, args.workdir or Path(args.input).parent / ".paperformat_check", rules)
    rules = rules_for_source_kind(rules, project.source_kind)
    text, _ = read_text_best_effort(project.main_tex_path)
    analysis = analyze(text, rules)
    risk = assess_risk(analysis)
    report = build_report(analysis, [], rules["name"], risk)

    if args.report:
        write_report(report, args.report)

    print(f"Issues found: {len(analysis.issues)}")
    print(f"Format compliance score: {risk.overall_score}/100")
    print(f"Main TeX file: {project.main_tex_path}")
    for issue in analysis.issues:
        print(f"- [{issue.severity}] {issue.rule_id}: {issue.message} (auto-fixable={issue.auto_fixable})")
    return 0


def run_repair(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    workdir = Path(args.workdir) if args.workdir else Path(args.output).parent / "repair_workspace"
    project = prepare_project(args.input, workdir, rules)
    rules = rules_for_source_kind(rules, project.source_kind)
    original_text, _ = read_text_best_effort(project.main_tex_path)
    analysis = analyze(original_text, rules)
    repaired_text, actions = repair(original_text, rules)
    repaired_analysis = analyze(repaired_text, rules)
    original_risk = assess_risk(analysis)
    repaired_risk = assess_risk(repaired_analysis)
    compile_status = None
    compile_output = ""

    write_text_with_encoding(project.main_tex_path, repaired_text, project.main_tex_encoding)
    write_text_with_encoding(args.output, repaired_text, project.main_tex_encoding)

    if args.compile:
        compile_ok, compile_output = compile_tex(
            project.main_tex_path,
            args.compile_outdir or Path(args.output).parent,
            args.tectonic,
        )
        compile_status = "success" if compile_ok else "failed"
        if args.compile_log:
            write_text_with_encoding(args.compile_log, compile_output)

    if args.project_zip:
        package_project(project.project_dir, args.project_zip)

    report = build_report(repaired_analysis, actions, rules["name"], repaired_risk, compile_status=compile_status)
    if args.report:
        write_report(report, args.report)

    print(f"Original issues: {len(analysis.issues)}")
    print(f"Remaining issues: {len(repaired_analysis.issues)}")
    print(f"Format score: {original_risk.overall_score}/100 -> {repaired_risk.overall_score}/100")
    print(f"Main TeX file: {project.main_tex_path}")
    print(f"Repaired file: {args.output}")
    if args.project_zip:
        print(f"Repaired project zip: {args.project_zip}")
    if args.report:
        print(f"Report file: {args.report}")
    if compile_status is not None:
        print(f"Compile verification: {compile_status}")
        if compile_status == "failed":
            print(explain_compile_failure(compile_output))
        if args.compile_log:
            print(f"Compile log: {args.compile_log}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaperFormat Agent MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Check a LaTeX file against the rule profile.")
    check_parser.add_argument("--input", required=True, help="Input .tex file")
    check_parser.add_argument("--rules", required=True, help="Rule profile JSON")
    check_parser.add_argument("--report", help="Optional Markdown report output path")
    check_parser.add_argument("--workdir", help="Optional temporary working directory")
    check_parser.set_defaults(func=run_check)

    repair_parser = subparsers.add_parser("repair", help="Repair a LaTeX file using the rule profile.")
    repair_parser.add_argument("--input", required=True, help="Input .tex file")
    repair_parser.add_argument("--rules", required=True, help="Rule profile JSON")
    repair_parser.add_argument("--output", required=True, help="Output repaired .tex file")
    repair_parser.add_argument("--report", help="Optional Markdown report output path")
    repair_parser.add_argument("--compile", action="store_true", help="Compile repaired file with Tectonic")
    repair_parser.add_argument("--compile-outdir", help="Compilation output directory")
    repair_parser.add_argument("--compile-log", help="Optional path to save compiler output")
    repair_parser.add_argument("--project-zip", help="Optional zip path for the repaired project")
    repair_parser.add_argument("--tectonic", help="Optional explicit path to tectonic executable")
    repair_parser.add_argument("--workdir", help="Optional temporary working directory")
    repair_parser.set_defaults(func=run_repair)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
