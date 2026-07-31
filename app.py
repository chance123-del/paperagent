from __future__ import annotations

import json
import shutil
import tempfile
import threading
from urllib.parse import quote
from pathlib import Path

import gradio as gr

from paperformat_agent.analyzer import analyze
from paperformat_agent.annotations import load_annotations
from paperformat_agent.asset_manifest import build_asset_manifest, write_asset_manifest
from paperformat_agent.bibliography import add_bibliography_to_project, apply_bibliography, apply_numeric_markers, remove_embedded_reference_list
from paperformat_agent.feedback import apply_text_feedback, save_feedback_evidence
from paperformat_agent.exporter import export_docx_from_tex
from paperformat_agent.guidelines import apply_guideline_overrides, apply_requirement_text
from paperformat_agent.hybrid_insert import build_block, insert_block
from paperformat_agent.journal_resolver import JOURNAL_PROFILES, apply_journal_profile, profile_choices, resolve_journal
from paperformat_agent.models import RepairAction
from paperformat_agent.placeholders import apply_placeholder_assets, find_placeholders, parse_caption_lines, scan_assets, unpack_bundle
from paperformat_agent.project_io import find_main_tex, package_project, prepare_project
from paperformat_agent.reference_style import apply_reference_article_style
from paperformat_agent.repairer import repair
from paperformat_agent.reporting import build_report, write_report
from paperformat_agent.rules import default_rules, load_rules, rules_for_source_kind, summarize_rules
from paperformat_agent.scoring import assess_risk
from paperformat_agent.text_io import read_text_best_effort, write_text_with_encoding
from paperformat_agent.verifier import cancel_active_compilations, compile_tex, explain_compile_failure


BASE_DIR = Path(__file__).resolve().parent
RULE_DIR = BASE_DIR / "rules"
OUTPUT_DIR = BASE_DIR / "outputs"
RULE_NONE = "不使用（通用）"
STYLE_CURRENT = "沿用上方当前规则"
STYLE_JOURNAL = "单独指定期刊规则包"
STYLE_RULE = "单独指定基础格式规则"
STYLE_CUSTOM = "完全自定插入样式"
RUN_CONFIG = "run_config.json"
DELIVERY_GATE = "delivery_gate.json"
REVIEWER_PAGE = BASE_DIR / "web" / "reviewer.html"
ANNOTATION_TEMPLATE = BASE_DIR / "outputs" / "annotations_template" / "annotations.xlsx"


def _reviewer_html(pdf_path: str | None) -> str:
    if not pdf_path or not Path(pdf_path).exists():
        return "<div class='review-empty'>快速预览生成后，这里会出现可选中文字的 PDF 审阅器。</div>"
    page_url = "/gradio_api/file=" + quote(str(REVIEWER_PAGE.resolve()), safe="")
    pdf_url = "/gradio_api/file=" + quote(str(Path(pdf_path).resolve()), safe="")
    return (
        "<iframe class='pdf-reviewer' title='PDF 在线审阅' "
        f"src='{page_url}?pdf={quote(pdf_url, safe='')}'></iframe>"
    )


REVIEW_BRIDGE_JS = """
() => {
  const setupPages = () => {
    if (document.getElementById('paperformat-page-1')) return true;
    const input = document.getElementById('stage-input');
    const rules = document.getElementById('stage-rules');
    const assets = document.getElementById('stage-assets');
    const review = document.getElementById('stage-review');
    const exportStage = document.getElementById('stage-export');
    if (!input || !rules || !assets || !review || !exportStage) return false;

    const mount = input.parentElement;
    const makePage = (number) => {
      const page = document.createElement('section');
      page.id = `paperformat-page-${number}`;
      page.className = 'workflow-page';
      mount.insertBefore(page, input);
      return page;
    };
    const pages = [makePage(1), makePage(2), makePage(3)];
    const labels = {
      input: '<strong>01 上传材料</strong>原稿、格式依据与文献库',
      rules: '<strong>02 配置规则</strong>论文版式与参考文献格式',
      delivery: '<strong>03 预览与产出</strong>审阅并导出正式文件',
    };
    document.querySelectorAll('.workflow-step').forEach((button) => {
      if (labels[button.dataset.target]) button.innerHTML = labels[button.dataset.target];
    });
    pages[0].append(input);
    pages[1].append(rules, assets);
    pages[2].append(review, exportStage);
    return true;
  };

  const switchWorkflowStage = (target) => {
    if (!setupPages()) return;
    const targets = { input: 1, rules: 2, delivery: 3 };
    const pageNumber = targets[target];
    if (!pageNumber) return;
    document.querySelectorAll('.workflow-step').forEach((button) => {
      const active = button.dataset.target === target;
      button.classList.toggle('active', active);
      button.setAttribute('aria-current', active ? 'step' : 'false');
    });
    document.querySelectorAll('.workflow-page').forEach((page, index) => {
      page.classList.toggle('active', index + 1 === pageNumber);
    });
    document.getElementById(`paperformat-page-${pageNumber}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  document.addEventListener('click', (event) => {
    const button = event.target.closest('.workflow-step');
    if (button) switchWorkflowStage(button.dataset.target);
  });
  window.addEventListener('message', (event) => {
    if (!event.data || event.data.type !== 'paperformat-selection' || !event.data.text) return;
    const input = document.querySelector('#review_anchor textarea, #review_anchor input');
    if (!input) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    setter.call(input, event.data.text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.focus();
  });
  const startPages = () => {
    if (setupPages()) switchWorkflowStage('input');
    else setTimeout(startPages, 150);
  };
  setTimeout(startPages, 150);
}
"""

APP_CSS = """
:root {
  --ink: #17211f;
  --muted: #65736e;
  --line: #dfe7e3;
  --canvas: #f5f7f6;
  --surface: #ffffff;
  --mint: #087a63;
  --mint-dark: #05604d;
  --mint-pale: #eaf6f1;
  --navy: #173832;
  --focus: rgba(8, 122, 99, 0.18);
}
body { background: var(--canvas) !important; }
.gradio-container {
  max-width: 1240px !important;
  padding: 0 28px 72px !important;
  color: var(--ink);
  font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif !important;
}
#masthead {
  position: relative;
  overflow: hidden;
  margin: 24px 0 18px;
  padding: 30px 34px 28px;
  border: 0;
  border-radius: 8px;
  background: var(--navy);
  color: #f5fbf8;
  box-shadow: 0 12px 30px rgba(22, 52, 46, 0.12);
}
#masthead::after { content: ""; position: absolute; width: 280px; height: 280px; border: 1px solid rgba(186, 225, 213, 0.18); border-radius: 50%; right: -76px; top: -178px; }
#masthead h1 { position: relative; margin: 0; font-size: 32px; line-height: 1.12; letter-spacing: 0; color: #fff; }
#masthead p { position: relative; max-width: 660px; margin: 10px 0 0; color: #c8ddd7; font-size: 14px; line-height: 1.6; }
.workflow {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px;
  margin: 0 0 22px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden;
  background: var(--line); box-shadow: 0 3px 12px rgba(34, 64, 55, 0.04);
}
.workflow-step { appearance: none; width: 100%; min-height: 78px; padding: 16px 18px; border: 0; border-radius: 0; background: var(--surface); color: var(--muted); text-align: left; font: inherit; cursor: pointer; font-size: 13px; line-height: 1.45; transition: background .16s ease, color .16s ease; }
.workflow-step:hover { background: #f6faf8; color: var(--ink); }
.workflow-step:focus-visible { outline: 3px solid var(--focus); outline-offset: -3px; }
.workflow-step strong { display: block; margin-bottom: 5px; color: var(--ink); font-size: 14px; }
.workflow-step.active { box-shadow: inset 0 3px 0 var(--mint); background: var(--mint-pale); }
.workflow-page { display: none; }
.project-package { margin-top: 16px; padding: 18px; border: 1px solid #b9d8cd; border-radius: 8px; background: #f6fbf8; }
.project-package h3 { margin: 0 0 5px; color: var(--navy); font-size: 16px; }
.project-package p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
.project-package .required { color: #a44620; font-weight: 700; }
.workflow-page.active { display: block; }
.workflow-page > .panel, .workflow-page > .results { margin-bottom: 0; }
#stage-input, #stage-rules, #stage-review, #stage-export { scroll-margin-top: 16px; }
.panel {
  margin: 0 0 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface);
  padding: 24px; box-shadow: 0 2px 8px rgba(28, 55, 47, 0.025); backdrop-filter: none;
}
.panel > .markdown h3 { margin: 0 0 18px !important; font-size: 18px !important; color: var(--ink) !important; }
.panel label span { font-size: 13px !important; color: #40514c !important; }
.panel input, .panel textarea, .panel select { border-color: #cbd9d3 !important; background: #fcfdfd !important; }
.panel input:focus, .panel textarea:focus {
  border-color: var(--mint) !important; box-shadow: 0 0 0 3px var(--focus) !important;
}
.advanced { margin-top: 14px; border: 1px solid var(--line) !important; border-radius: 7px !important; background: #fbfdfc !important; }
.advanced > button { color: #40534e !important; font-size: 13px !important; min-height: 42px !important; }
.insert-tools { border: 1px solid #9fd4c3 !important; background: #f7fcf9 !important; }
.insert-tools > button { min-height: 50px !important; color: #075f4c !important; font-size: 15px !important; font-weight: 700 !important; background: #e7f7f0 !important; }
.insert-tools > button::before { content: "准备素材"; display: inline-block; margin-right: 10px; padding: 2px 7px; border-radius: 4px; background: #087a63; color: #fff; font-size: 11px; font-weight: 700; }
.batch-tools { border: 1px solid #d7c18d !important; background: #fffdf7 !important; }
.batch-tools > button { min-height: 50px !important; color: #73540d !important; font-size: 15px !important; font-weight: 700 !important; background: #fff6dc !important; }
.batch-tools > button::before { content: "批量处理"; display: inline-block; margin-right: 10px; padding: 2px 7px; border-radius: 4px; background: #b17b16; color: #fff; font-size: 11px; font-weight: 700; }
.bibliography-tools { border: 1px solid #8bb7d8 !important; background: #f6fbff !important; }
.bibliography-tools > button { min-height: 50px !important; color: #1c537a !important; font-size: 15px !important; font-weight: 700 !important; background: #e8f3fb !important; }
.bibliography-tools > button::before { content: "最后设置"; display: inline-block; margin-right: 10px; padding: 2px 7px; border-radius: 4px; background: #2874a6; color: #fff; font-size: 11px; font-weight: 700; }
.action-guide { margin: 2px 0 14px; padding: 12px 14px; border-left: 4px solid var(--mint); border-radius: 0 6px 6px 0; background: var(--mint-pale); color: #1b4e42; font-size: 13px; line-height: 1.6; }
.action-guide strong { color: #075f4c; }
.batch-guide { border-left-color: #b17b16; background: #fff6dc; color: #5b470f; }
.bibliography-guide { border-left-color: #2874a6; background: #e8f3fb; color: #1c537a; }
.insert-tools #match { background: var(--mint) !important; color: #fff !important; border-color: var(--mint) !important; }
.batch-tools #match { background: #a56e0b !important; color: #fff !important; border-color: #a56e0b !important; }
.match-row { align-items: end !important; }
#match { min-height: 42px; border: 1px solid #9fc8ba !important; background: var(--mint-pale) !important; color: #06634e !important; font-weight: 650; }
#run { min-height: 50px; border: 0 !important; border-radius: 7px !important; background: var(--mint) !important; font-weight: 700; box-shadow: 0 6px 12px rgba(8, 122, 99, 0.18); }
#run:hover { background: var(--mint-dark) !important; }
#formal { min-height: 50px; border-radius: 7px !important; background: var(--navy) !important; color: #fff !important; font-weight: 700; }
.final-delivery { margin-top: 22px !important; border-top: 3px solid var(--mint) !important; background: #fbfefd !important; }
.final-delivery > .markdown { margin-bottom: 8px; }
.pdf-reviewer { width: 100%; height: 720px; border: 1px solid #c7d7d0; border-radius: 7px; background: #edf3ef; }
.review-empty { padding: 18px; border: 1px dashed #a9c3b9; border-radius: 7px; color: var(--muted); background: #f8fbf9; }
#results, #stage-review { margin-top: 26px; border-top: 2px solid var(--navy); padding-top: 20px; }
#stage-review > .markdown h2 { font-size: 22px !important; color: var(--ink) !important; }
button.secondary { border-radius: 7px !important; border-color: #b7cbc3 !important; color: #235348 !important; }
footer { display: none !important; }
@media (max-width: 720px) {
  .gradio-container { padding: 0 12px 40px !important; }
  #masthead { margin-top: 12px; padding: 24px 20px; }
  #masthead h1 { font-size: 26px; }
  .workflow { grid-template-columns: 1fr; }
  .workflow .active { border-top: 0; border-left: 3px solid var(--mint); padding: 15px 18px 15px 15px; }
  .panel { padding: 18px 16px; }
  .pdf-reviewer { height: 520px; }
}
"""

_RUN_LOCK = threading.Lock()
_RUN_VERSION = 0


def _rule_choices() -> list[str]:
    return sorted(path.name for path in RULE_DIR.glob("*.json"))


def _rule_dropdown_choices() -> list[str]:
    return [RULE_NONE] + _rule_choices()


def _profile_dropdown_choices() -> list[str]:
    choices = profile_choices()
    generic_choice = next((choice for choice in choices if choice.startswith("generic:")), "generic: Generic journal")
    ordered = [choice for choice in choices if choice != generic_choice]
    return [RULE_NONE, generic_choice] + ordered


def _base_rules(rule_file: str) -> dict:
    if not rule_file or rule_file == RULE_NONE:
        return default_rules()
    return load_rules(RULE_DIR / rule_file)


def _profile_id(profile_choice: str) -> str:
    if not profile_choice or profile_choice == RULE_NONE:
        return "generic"
    return profile_choice.split(":", 1)[0]


def _rule_summary(rule_file: str) -> str:
    if not rule_file or rule_file == RULE_NONE:
        return summarize_rules(default_rules())
    return summarize_rules(load_rules(RULE_DIR / rule_file))


def _output_root(output_path: str) -> Path:
    root = Path(output_path).expanduser() if output_path.strip() else OUTPUT_DIR
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise gr.Error("输出目录必须是文件夹。")
    return root.resolve()


def _source_path(uploaded_file: str | None, local_path: str) -> Path:
    candidate = Path(local_path.strip()).expanduser() if local_path and local_path.strip() else Path(uploaded_file or "")
    if not candidate.exists() or not candidate.is_file():
        raise gr.Error("请上传论文文件，或填写有效的本地文件路径。")
    return candidate.resolve()


def _start_run() -> int:
    global _RUN_VERSION
    cancel_active_compilations()
    with _RUN_LOCK:
        _RUN_VERSION += 1
        return _RUN_VERSION


def _cancel_for_new_source() -> tuple[str, None, None, None, None, dict, None]:
    global _RUN_VERSION
    cancelled = cancel_active_compilations()
    with _RUN_LOCK:
        _RUN_VERSION += 1
    message = "" if not cancelled else "已结束上一份论文的编译进程，准备处理新文件。"
    return message, None, None, None, None, gr.update(value=None, visible=False), None


def _save_run_config(run_dir: Path, rule_file: str, journal_profile: str, target_name: str, rules: dict) -> None:
    config = {
        "rule_file": rule_file or RULE_NONE,
        "journal_profile": journal_profile or RULE_NONE,
        "target_name": target_name.strip(),
        "resolved_rules": rules,
    }
    (run_dir / RUN_CONFIG).write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_run_config(run_dir: Path) -> dict:
    path = run_dir / RUN_CONFIG
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_delivery_gate(run_dir: Path, blockers: list[str], notices: list[str] | None = None) -> None:
    (run_dir / DELIVERY_GATE).write_text(
        json.dumps({"blockers": blockers, "notices": notices or []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_asset_manifest(bundle_dir: Path, tex_text: str, project_dir: Path) -> list[dict[str, object]]:
    placeholders = find_placeholders(tex_text)
    assets, ignored = scan_assets(bundle_dir)
    records = build_asset_manifest(bundle_dir, placeholders, assets, ignored)
    write_asset_manifest(records, project_dir)
    return records


def _load_delivery_gate(run_dir: Path) -> dict:
    path = run_dir / DELIVERY_GATE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"blockers": [], "notices": []}


def _resolve_insert_rules(
    run_dir: Path,
    style_mode: str,
    override_profile: str,
    override_rule: str,
    custom_figure_width: str,
    custom_figure_prefix: str,
    custom_table_prefix: str,
) -> dict:
    run_config = _load_run_config(run_dir)
    if style_mode == STYLE_CURRENT:
        return run_config.get("resolved_rules", default_rules())
    if style_mode == STYLE_JOURNAL:
        return apply_journal_profile(default_rules(), _profile_id(override_profile))
    if style_mode == STYLE_RULE:
        return _base_rules(override_rule)

    custom_rules = default_rules()
    if custom_figure_prefix.strip():
        custom_rules["caption_prefixes"]["figure"] = custom_figure_prefix.strip()
    if custom_table_prefix.strip():
        custom_rules["caption_prefixes"]["table"] = custom_table_prefix.strip()
    if custom_figure_width.strip():
        custom_rules["insertion_policy"]["figure_width"] = custom_figure_width.strip()
    custom_rules["name"] = "custom_insert_rules"
    return custom_rules


def _main_tex_for_run(run_dir: Path) -> Path:
    project_dir = run_dir / "project"
    conventional = project_dir / "main.tex"
    return conventional if conventional.exists() else find_main_tex(project_dir)


def _compiled_pdf_for(main_tex: Path, run_dir: Path) -> Path | None:
    preferred = run_dir / f"{main_tex.stem}.pdf"
    if preferred.exists():
        return preferred
    fallback = run_dir / "main.pdf"
    return fallback if fallback.exists() else None


def _compile_after_update(main_tex: Path, run_dir: Path, log_name: str) -> tuple[str | None, str | None]:
    ok, compile_output = compile_tex(main_tex, run_dir)
    write_text_with_encoding(run_dir / log_name, compile_output)
    candidate = _compiled_pdf_for(main_tex, run_dir)
    pdf_path = str(candidate) if ok and candidate else None
    note = None if ok else explain_compile_failure(compile_output)
    if ok and "Preview fallback:" in compile_output:
        note = "正式模板未在本地完整跑通，当前输出的是 article 预览版 PDF。"
    return pdf_path, note


def match_journal(journal_name: str) -> tuple[str, dict]:
    match = resolve_journal(journal_name)
    choice = f"{match.profile_id}: {match.profile_name}"
    message = "\n".join(
        [
            "#### 期刊匹配结果",
            "",
            f"- 匹配标题：`{match.canonical_title or '未提供'}`",
            f"- 出版方：`{match.publisher or '未识别'}`",
            f"- 推荐规则包：`{match.profile_name}`",
            f"- 置信度：`{match.confidence}`",
            f"- 来源：{match.source}",
            "",
            "建议再上传该期刊官方模板或格式要求，系统会在此基础上继续微调。",
        ]
    )
    return choice, gr.update(value=message, visible=True)


def run_agent(
    uploaded_file: str | None,
    local_path: str,
    rule_file: str,
    target_name: str,
    journal_profile: str,
    requirement_text: str,
    target_guide: str | None,
    reference_article: str | None,
    bibliography_file: str | None,
    initial_asset_bundle: str | None,
    initial_annotation_bundle: str | None,
    output_path: str,
    compile_pdf: bool,
):
    run_version = _start_run()
    source = _source_path(uploaded_file, local_path)
    destination_root = _output_root(output_path)
    run_dir = Path(tempfile.mkdtemp(prefix="paperformat_", dir=destination_root))

    rules = apply_journal_profile(_base_rules(rule_file), _profile_id(journal_profile))
    rules, guideline_changes = apply_guideline_overrides(rules, target_guide)
    rules, text_requirement_changes = apply_requirement_text(rules, requirement_text)
    rules, reference_changes = apply_reference_article_style(rules, reference_article)

    if target_guide:
        guide = Path(target_guide)
        shutil.copy2(guide, run_dir / f"target_guide{guide.suffix.lower()}")
    if reference_article:
        reference = Path(reference_article)
        shutil.copy2(reference, run_dir / f"reference_article{reference.suffix.lower()}")

    try:
        project = prepare_project(source, run_dir, rules)
        original_text, _ = read_text_best_effort(project.main_tex_path)
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc

    rules = rules_for_source_kind(rules, project.source_kind)

    try:
        bibliography_name = add_bibliography_to_project(project.project_dir, bibliography_file)
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc

    analysis_before = analyze(original_text, rules)
    repaired_text, actions = repair(original_text, rules)
    bibliography_path = project.project_dir / f"{bibliography_name}.bib" if bibliography_name else None
    repaired_text, citation_mapping, unresolved_citations = apply_numeric_markers(repaired_text, bibliography_path, actions)
    if citation_mapping:
        repaired_text = remove_embedded_reference_list(repaired_text, actions)
    repaired_text = apply_bibliography(repaired_text, bibliography_name, rules, actions)
    asset_summary: list[str] = []
    delivery_blockers: list[str] = list(project.source_notes)
    if initial_asset_bundle:
        try:
            bundle_dir = unpack_bundle(initial_asset_bundle, run_dir)
            annotations = load_annotations(initial_annotation_bundle, run_dir)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        _write_asset_manifest(bundle_dir, repaired_text, project.project_dir)
        repaired_text, matched, missing, duplicate = apply_placeholder_assets(
            repaired_text,
            bundle_dir,
            project.project_dir,
            rules,
            figure_captions=annotations.figures,
            table_captions=annotations.tables,
            caption_links=annotations.links,
        )
        asset_summary = [
            f"图表占位符：{len(matched)} 个已匹配",
            f"缺失素材：{len(missing)} 个",
            f"重复或忽略：{len(duplicate)} 个",
            f"图表注模板待确认：{len(annotations.warnings)} 项",
        ]
        delivery_blockers.extend(missing)
        delivery_blockers.extend(duplicate)
        delivery_blockers.extend(annotations.warnings)
        mapping_report = run_dir / "asset_mapping_report.md"
        mapping_lines = ["# Asset Mapping Report", "", "## Matched", ""]
        mapping_lines.extend(f"- {item}" for item in matched) if matched else mapping_lines.append("- No assets matched.")
        for title, entries in (("Missing", missing), ("Duplicate or ignored", duplicate), ("Annotation review required", annotations.warnings)):
            if entries:
                mapping_lines.extend(["", f"## {title}", "", *[f"- {item}" for item in entries]])
        mapping_report.write_text("\n".join(mapping_lines) + "\n", encoding="utf-8")
        shutil.copy2(mapping_report, project.project_dir / mapping_report.name)
        actions.append(RepairAction("placeholder_asset_mapping", f"Applied {len(matched)} exact placeholder asset matches."))
    else:
        unresolved_markers = [marker for marker, _ in find_placeholders(repaired_text)]
        if unresolved_markers:
            delivery_blockers.append("Unresolved manuscript placeholders: " + ", ".join(unresolved_markers))
    analysis_after = analyze(repaired_text, rules)
    risk_before, risk_after = assess_risk(analysis_before), assess_risk(analysis_after)

    write_text_with_encoding(project.main_tex_path, repaired_text, project.main_tex_encoding)
    source_tex = run_dir / "source.tex"
    write_text_with_encoding(source_tex, repaired_text, project.main_tex_encoding)
    _save_run_config(run_dir, rule_file, journal_profile, target_name, rules)
    _write_delivery_gate(run_dir, delivery_blockers, notices=project.source_notes)
    citation_report_path = run_dir / "citation_mapping.md"
    if bibliography_name:
        citation_lines = ["# Citation Mapping", "", "## Converted numeric markers", ""]
        citation_lines.extend(f"- [{number}] -> `{key}`" for number, key in citation_mapping) if citation_mapping else citation_lines.append("- No numeric citation markers were found.")
        if unresolved_citations:
            citation_lines.extend(["", "## Unresolved markers", "", *[f"- [{number}] has no matching entry in the uploaded library." for number in unresolved_citations]])
        citation_report_path.write_text("\n".join(citation_lines) + "\n", encoding="utf-8")
        shutil.copy2(citation_report_path, project.project_dir / citation_report_path.name)
    project_zip = package_project(project.project_dir, run_dir / "latex_source.zip")

    compile_status, compile_note, pdf_path = "未编译", None, None
    compile_log_path = run_dir / "compile.log"
    if compile_pdf:
        ok, compile_output = compile_tex(project.main_tex_path, run_dir)
        preview_used = "Preview fallback:" in compile_output
        compile_status = "预览版" if ok and preview_used else "成功" if ok else "失败"
        write_text_with_encoding(compile_log_path, compile_output)
        candidate_pdf = _compiled_pdf_for(project.main_tex_path, run_dir)
        pdf_path = str(candidate_pdf) if candidate_pdf else None
        if preview_used:
            compile_note = "期刊正式模板未在本地完整通过，当前 PDF 是安全预览版。"
        elif not ok:
            compile_note = explain_compile_failure(compile_output)

    report = build_report(analysis_after, actions, rules["name"], risk_after, compile_status=compile_status)
    report_path = run_dir / "format_report.md"
    write_report(report, report_path)
    summary = "\n".join(
        [
            "## 处理完成",
            "",
            f"- 论文文件：`{source.name}`（{project.source_kind}）",
            f"- 目标期刊：`{target_name.strip() or '未指定'}`",
            f"- 基础规则：`{rule_file if rule_file and rule_file != RULE_NONE else '通用'}`",
            f"- 期刊规则包：`{JOURNAL_PROFILES.get(_profile_id(journal_profile), JOURNAL_PROFILES['generic'])['name']}`",
            f"- 额外格式要求：`{', '.join(guideline_changes + text_requirement_changes) or '未检测到'}`",
            f"- 参考论文风格：`{', '.join(reference_changes) if reference_changes else '未检测到'}`",
            f"- 参考文献：`{bibliography_name + '.bib' if bibliography_name else '未提供'}`",
            f"- 项目图表包：`{'；'.join(asset_summary) if asset_summary else '未上传'}`",
            f"- 正式交付状态：`{'已阻止，需处理 ' + str(len(delivery_blockers)) + ' 项' if delivery_blockers else '可进入正式导出检查'}`",
            f"- 数字引用映射：`{len(citation_mapping)} 条已转换，{len(unresolved_citations)} 条未匹配`" if bibliography_name else "- 数字引用映射：`未启用（请上传 BibTeX 文献库）`",
            f"- 源文件待确认项：`{len(project.source_notes)}`",
            f"- 格式评分：`{risk_before.overall_score}/100 -> {risk_after.overall_score}/100`",
            f"- 自动修复数量：`{len(actions)}`",
            f"- PDF 编译：`{compile_status}`",
            f"- 输出目录：`{run_dir}`",
        ]
    )
    if compile_note:
        summary += "\n\n**编译说明：** " + compile_note
    with _RUN_LOCK:
        if run_version != _RUN_VERSION:
            raise gr.Error("当前任务已被新的论文上传覆盖。")
    review_html = _reviewer_html(pdf_path)
    # The first pass is deliberately preview-only: artifacts remain in the run directory
    # and are exposed only after the user requests formal delivery.
    pdf_output = gr.update(value=None, visible=False)
    return summary, None, None, str(report_path), str(compile_log_path) if compile_log_path.exists() else None, pdf_output, None, review_html, str(run_dir)


def run_formal_export(run_directory: str):
    """Compile the reviewed manuscript and publish PDF, DOCX and source files."""
    if not run_directory:
        raise gr.Error("请先生成快速预览，再执行正式导出。")
    run_dir = Path(run_directory)
    source_tex = run_dir / "source.tex"
    main_tex = _main_tex_for_run(run_dir)
    if not source_tex.exists() or not main_tex.exists():
        raise gr.Error("当前预览工程不存在，请重新生成快速预览。")
    delivery_gate = _load_delivery_gate(run_dir)
    blockers = delivery_gate.get("blockers", [])
    if blockers:
        preview = "；".join(str(item) for item in blockers[:3])
        raise gr.Error(f"正式导出已阻止：仍有 {len(blockers)} 个待确认项。先完成图表/图表注匹配后再导出。{preview}")

    cancel_active_compilations()
    ok, compile_output = compile_tex(main_tex, run_dir)
    log_path = run_dir / "formal_compile.log"
    write_text_with_encoding(log_path, compile_output)
    candidate_pdf = _compiled_pdf_for(main_tex, run_dir)
    pdf_path = str(candidate_pdf) if candidate_pdf else None
    docx_path = run_dir / "formatted_manuscript.docx"
    word_ok, word_note = export_docx_from_tex(main_tex, docx_path, run_dir / "project")
    project_zip = package_project(run_dir / "project", run_dir / "formal_latex_source.zip")

    status = "成功" if ok else "失败"
    if ok and "Preview fallback:" in compile_output:
        status = "预览替代版"
    notes = [f"- PDF 编译：`{status}`", f"- Word 导出：`{'成功' if word_ok else '失败'}`", f"- 输出目录：`{run_dir}`"]
    if not ok:
        notes.append(f"- 编译说明：{explain_compile_failure(compile_output)}")
    notes.append(f"- Word 说明：{word_note}")
    summary = "## 正式导出完成\n\n" + "\n".join(notes)
    return (
        summary,
        gr.update(value=str(source_tex), visible=True),
        gr.update(value=str(project_zip), visible=True),
        str(log_path),
        gr.update(value=pdf_path, visible=bool(pdf_path)),
        gr.update(value=str(docx_path) if word_ok and docx_path.exists() else None, visible=bool(word_ok and docx_path.exists())),
        _reviewer_html(pdf_path),
    )


def run_feedback(run_directory: str, feedback_text: str, feedback_images: list[str] | None, compile_pdf: bool):
    if not run_directory:
        raise gr.Error("请先生成排版工程，再提交反馈。")
    run_dir = Path(run_directory)
    source_tex = run_dir / "source.tex"
    main_tex = _main_tex_for_run(run_dir)
    if not source_tex.exists() or not main_tex.exists():
        raise gr.Error("上一轮生成的工程文件已不存在，请重新生成。")

    save_feedback_evidence(run_dir, feedback_text, feedback_images)
    text, changes = apply_text_feedback(read_text_best_effort(source_tex)[0], feedback_text)
    write_text_with_encoding(source_tex, text)
    write_text_with_encoding(main_tex, text)
    revised_zip = package_project(run_dir / "project", run_dir / "revised_latex_source.zip")
    pdf_path, note = (None, None)
    if compile_pdf:
        pdf_path, note = _compile_after_update(main_tex, run_dir, "feedback_compile.log")

    feedback_report = run_dir / "feedback_report.md"
    report_lines = ["# Feedback Revision", "", "## Submitted feedback", "", feedback_text.strip() or "No written feedback provided.", "", "## Applied changes", ""]
    report_lines.extend(f"- {change}" for change in changes) if changes else report_lines.append("- Feedback was recorded; no unambiguous automatic correction was identified.")
    feedback_report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    summary = "## 反馈修订完成\n\n" + ("\n".join(f"- {change}" for change in changes) if changes else "- 已保存反馈截图和文字说明。")
    if note:
        summary += "\n\n**编译说明：** " + note
    return summary, str(source_tex), str(revised_zip), str(feedback_report), gr.update(value=pdf_path, visible=bool(pdf_path))


def run_hybrid_insert(
    run_directory: str,
    kind: str,
    content: str,
    upload: str | None,
    caption: str,
    link_url: str,
    section: str,
    placement: str,
    anchor: str,
    style_mode: str,
    override_profile: str,
    override_rule: str,
    custom_figure_width: str,
    custom_figure_prefix: str,
    custom_table_prefix: str,
):
    if not run_directory:
        raise gr.Error("请先生成排版工程，再插入内容。")
    run_dir = Path(run_directory)
    source_tex = run_dir / "source.tex"
    main_tex = _main_tex_for_run(run_dir)
    rules = _resolve_insert_rules(run_dir, style_mode, override_profile, override_rule, custom_figure_width, custom_figure_prefix, custom_table_prefix)
    try:
        block = build_block(kind, content, link_url if kind == "Hyperlink" else upload, caption, run_dir / "project", rules)
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc
    updated = insert_block(read_text_best_effort(source_tex)[0], block, section, placement, anchor)
    write_text_with_encoding(source_tex, updated)
    write_text_with_encoding(main_tex, updated)
    project_zip = package_project(run_dir / "project", run_dir / "hybrid_revised_source.zip")
    pdf_path, note = _compile_after_update(main_tex, run_dir, "hybrid_compile.log")
    summary = f"## 已插入 {kind}\n\n- 规则来源：`{style_mode}`\n- 已重新编译工程。"
    if note:
        summary += "\n\n**编译说明：** " + note
    return summary, str(source_tex), str(project_zip), gr.update(value=pdf_path, visible=bool(pdf_path))


def run_placeholder_insert(
    run_directory: str,
    asset_bundle: str | None,
    annotation_bundle: str | None,
    figure_caption_text: str,
    table_caption_text: str,
    style_mode: str,
    override_profile: str,
    override_rule: str,
    custom_figure_width: str,
    custom_figure_prefix: str,
    custom_table_prefix: str,
):
    if not run_directory:
        raise gr.Error("请先生成排版工程，再执行占位符批量插入。")
    run_dir = Path(run_directory)
    source_tex = run_dir / "source.tex"
    main_tex = _main_tex_for_run(run_dir)
    if not source_tex.exists():
        raise gr.Error("当前工程缺少 source.tex，请重新生成。")

    bundle_dir = unpack_bundle(asset_bundle, run_dir)
    rules = _resolve_insert_rules(run_dir, style_mode, override_profile, override_rule, custom_figure_width, custom_figure_prefix, custom_table_prefix)
    figure_captions = parse_caption_lines(figure_caption_text)
    table_captions = parse_caption_lines(table_caption_text)
    try:
        annotations = load_annotations(annotation_bundle, run_dir)
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc
    # Workbook values win over the legacy free-text fields because the workbook
    # carries an explicit prefix policy and can be audited after delivery.
    figure_captions.update(annotations.figures)
    table_captions.update(annotations.tables)
    current_tex = read_text_best_effort(source_tex)[0]
    _write_asset_manifest(bundle_dir, current_tex, run_dir / "project")
    updated, matched, missing, duplicate = apply_placeholder_assets(
        current_tex,
        bundle_dir,
        run_dir / "project",
        rules,
        figure_captions=figure_captions,
        table_captions=table_captions,
        caption_links=annotations.links,
    )
    write_text_with_encoding(source_tex, updated)
    write_text_with_encoding(main_tex, updated)
    delivery_blockers = [*missing, *duplicate, *annotations.warnings]
    unresolved_markers = [marker for marker, _ in find_placeholders(updated)]
    if unresolved_markers:
        delivery_blockers.append("Unresolved manuscript placeholders: " + ", ".join(unresolved_markers))
    _write_delivery_gate(run_dir, delivery_blockers)
    project_zip = package_project(run_dir / "project", run_dir / "placeholder_revised_source.zip")
    pdf_path, note = _compile_after_update(main_tex, run_dir, "placeholder_compile.log")

    lines = ["## 占位符批量插入完成", "", f"- 规则来源：`{style_mode}`", f"- 成功匹配：`{len(matched)}`", f"- 缺失素材：`{len(missing)}`", f"- 重名或已忽略：`{len(duplicate)}`", ""]
    if matched:
        lines.extend(["### 已匹配", ""] + [f"- {item}" for item in matched])
    if missing:
        lines.extend(["", "### 未匹配", ""] + [f"- {item}" for item in missing])
    if duplicate:
        lines.extend(["", "### 重名/忽略", ""] + [f"- {item}" for item in duplicate])
    if annotations.warnings:
        lines.extend(["", "### 图表注模板待确认项", ""] + [f"- {item}" for item in annotations.warnings])
    if note:
        lines.extend(["", f"**编译说明：** {note}"])
    lines.extend(["", f"- 正式交付状态：`{'已阻止，需处理 ' + str(len(delivery_blockers)) + ' 项' if delivery_blockers else '可进入正式导出检查'}`"])
    return "\n".join(lines), str(source_tex), str(project_zip), gr.update(value=pdf_path, visible=bool(pdf_path))


def build_demo() -> gr.Blocks:
    rule_choices = _rule_dropdown_choices()
    profile_choices_ui = _profile_dropdown_choices()
    with gr.Blocks(title="PaperFormat Agent") as demo:
        gr.HTML(
            """
            <section id="masthead">
              <h1>PaperFormat Agent</h1>
              <p>科研论文格式智能体</p>
            </section>
            <section class="workflow" aria-label="论文处理流程">
              <button type="button" class="workflow-step active" data-target="input" aria-current="step"><strong>01 上传材料</strong>原稿、格式依据与文献库</button>
              <button type="button" class="workflow-step" data-target="rules"><strong>02 配置规则</strong>论文版式与参考文献格式</button>
              <button type="button" class="workflow-step" data-target="delivery"><strong>03 预览与产出</strong>审阅并导出正式文件</button>
            </section>
            """
        )

        with gr.Group(elem_id="stage-input", elem_classes=["panel"]):
            gr.Markdown("### 上传材料")
            gr.Markdown("原稿正文、数据与结论将保持不变；系统只转换格式，并将缺失的必要内容记录在格式报告中。")
            with gr.Row():
                uploaded = gr.File(label="上传论文", type="filepath", file_types=[".docx", ".pdf", ".md", ".markdown", ".tex", ".zip"])
                local_path = gr.Textbox(label="本地论文路径", placeholder=r"D:\Documents\my-paper.docx")
            gr.HTML("""
            <section class="project-package">
              <h3>项目材料包</h3>
              <p>首次生成即按占位符 <code>[Fig1]</code>、<code>[Table1]</code> 精确匹配资源；图表注、表注和链接只采用您在模板中提供的内容。<span class="required"> 不会自动编写题注、DOI 或参考文献。</span></p>
            </section>
            """)
            with gr.Row():
                initial_asset_bundle = gr.File(label="图表素材压缩包（ZIP，可选）", type="filepath", file_types=[".zip"])
                initial_annotation_bundle = gr.File(label="图表注/表注模板（XLSX 或 ZIP，可选）", type="filepath", file_types=[".xlsx", ".zip"])
                gr.DownloadButton(
                    "下载图表注模板",
                    value=str(ANNOTATION_TEMPLATE) if ANNOTATION_TEMPLATE.exists() else None,
                    interactive=ANNOTATION_TEMPLATE.exists(),
                )
            with gr.Accordion("格式与文献材料", open=True, elem_classes=["advanced"]):
                target_guide = gr.File(label="上传格式要求或官方模板（可选）", type="filepath", file_types=[".pdf", ".docx", ".md", ".markdown", ".txt"])
                reference_article = gr.File(label="上传公开参考论文（PDF / Word，可选）", type="filepath", file_types=[".pdf", ".docx"])
                bibliography_file = gr.File(label="上传个人参考文献库（BibTeX .bib，可选）", type="filepath", file_types=[".bib"])

        with gr.Group(elem_id="stage-rules", elem_classes=["panel"]):
            gr.Markdown("### 论文与参考文献规则")
            with gr.Row(elem_classes=["match-row"]):
                target_name = gr.Textbox(label="目标期刊名称（可选）", placeholder="例如：IEEE Transactions on ...", scale=5)
                match_button = gr.Button("匹配期刊", elem_id="match", scale=1)
            with gr.Row():
                journal_profile = gr.Dropdown(label="期刊规则包", choices=profile_choices_ui, value=RULE_NONE)
                rule_file = gr.Dropdown(label="基础格式规则", choices=rule_choices, value=RULE_NONE)
            journal_match = gr.Markdown("", visible=False)
            requirement_text = gr.Textbox(label="直接填写排版要求（可选）", lines=3, placeholder="例如：A4、页边距 2.5cm、1.5 倍行距、参考文献 IEEEtran；图表注与链接文字可使用中文")
            with gr.Accordion("规则检查", open=False, elem_classes=["advanced"]):
                rule_summary = gr.Markdown(value=_rule_summary(RULE_NONE))

        with gr.Group(elem_id="stage-assets", elem_classes=["panel"]):
            gr.Markdown("### 生成预览")
            output_path = gr.Textbox(label="输出目录", value=str(OUTPUT_DIR), placeholder=r"D:\PaperOutput")
            run_button = gr.Button("生成排版工程", variant="primary", elem_id="run")

        with gr.Group(elem_id="stage-review", elem_classes=["results"]):
            gr.Markdown("## 输出结果")
            summary = gr.Markdown()
            with gr.Row():
                report_file = gr.File(label="格式检查报告")
                compile_log = gr.File(label="编译日志")
            run_state = gr.State()
            run_button.value = "生成快速预览"
            with gr.Accordion("在线 PDF 审阅与选区锚点", open=True, elem_classes=["advanced"]):
                reviewer = gr.HTML(value=_reviewer_html(None))

            with gr.Accordion("反馈与修订", open=False, elem_classes=["advanced"]):
                feedback_text = gr.Textbox(label="问题说明或确认后的修改内容", lines=4, placeholder="例如：表 1 请使用上传的 Table1.xlsx；或粘贴已确认的替换文字。")
                feedback_images = gr.File(label="问题截图（可多选）", type="filepath", file_count="multiple", file_types=["image"])
                feedback_button = gr.Button("保存反馈并生成修订版", elem_id="match")
                feedback_summary = gr.Markdown()
                with gr.Row():
                    revised_tex = gr.File(label="修订后的 LaTeX 主文件")
                    revised_project = gr.File(label="修订后的源码包")
                    revised_pdf = gr.File(label="修订后的 PDF", visible=False)
                revised_report = gr.File(label="反馈修订报告")

            with gr.Group(elem_id="stage-export", elem_classes=["panel", "final-delivery"]):
                gr.Markdown("### 最终交付")
                gr.Markdown("确认预览与修订无误后，再生成可提交的正式文件。")
                formal_button = gr.Button("正式导出 PDF / Word / LaTeX 源码", elem_id="formal")
                with gr.Row():
                    pdf_file = gr.File(label="正式 PDF", visible=False)
                    word_file = gr.File(label="正式 Word（DOCX）", visible=False)
                with gr.Row():
                    tex_file = gr.File(label="LaTeX 主文件", visible=False)
                    project_file = gr.File(label="LaTeX 源码包", visible=False)

        run_button.click(
            run_agent,
            inputs=[uploaded, local_path, rule_file, target_name, journal_profile, requirement_text, target_guide, reference_article, bibliography_file, initial_asset_bundle, initial_annotation_bundle, output_path, gr.State(True)],
            outputs=[summary, tex_file, project_file, report_file, compile_log, pdf_file, word_file, reviewer, run_state],
        )
        formal_button.click(
            run_formal_export,
            inputs=run_state,
            outputs=[summary, tex_file, project_file, compile_log, pdf_file, word_file, reviewer],
        )
        uploaded.change(_cancel_for_new_source, outputs=[summary, tex_file, project_file, report_file, compile_log, pdf_file, run_state], queue=False)
        local_path.change(_cancel_for_new_source, outputs=[summary, tex_file, project_file, report_file, compile_log, pdf_file, run_state], queue=False)
        match_button.click(match_journal, inputs=target_name, outputs=[journal_profile, journal_match])
        feedback_button.click(
            run_feedback,
            inputs=[run_state, feedback_text, feedback_images, gr.State(True)],
            outputs=[feedback_summary, revised_tex, revised_project, revised_report, revised_pdf],
        )
        rule_file.change(_rule_summary, inputs=rule_file, outputs=rule_summary)
    return demo


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_demo().launch(
        css=APP_CSS,
        js=REVIEW_BRIDGE_JS,
        allowed_paths=[str(OUTPUT_DIR.resolve()), str(REVIEWER_PAGE.resolve()), str(ANNOTATION_TEMPLATE.resolve())],
        server_name="127.0.0.1",
        server_port=7861,
    )
