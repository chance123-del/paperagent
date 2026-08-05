from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from html import escape
from urllib.parse import quote
from pathlib import Path

import gradio as gr

# Windows may expose a system proxy to HTTPX even when shell proxy variables are
# empty. Keep Gradio's local startup checks on the loopback interface while
# allowing external API requests (for example DeepSeek) to use normal settings.
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

from paperformat_agent.analyzer import analyze
from paperformat_agent.agent_orchestrator import PaperDeliveryAgent
from paperformat_agent.annotations import load_annotations
from paperformat_agent.asset_manifest import build_asset_manifest, write_asset_manifest
from paperformat_agent.bibliography import add_bibliography_to_project, apply_bibliography, apply_numeric_markers, remove_embedded_reference_list
from paperformat_agent.feedback import apply_text_feedback, save_feedback_evidence
from paperformat_agent.formulas import apply_formulas, load_formulas, write_formula_manifest
from paperformat_agent.exporter import export_docx_from_tex
from paperformat_agent.guidelines import apply_guideline_overrides, apply_requirement_text
from paperformat_agent.hybrid_insert import build_block, insert_block
from paperformat_agent.journal_resolver import JOURNAL_PROFILES, apply_journal_profile, profile_choices, resolve_journal
from paperformat_agent.llm_rule_extractor import (
    analysis_to_rows,
    analyze_rule_document,
    apply_selected_rule_rows,
    render_analysis_markdown,
)
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
TEMPLATE_DIR = BASE_DIR / "templates"
RULE_NONE = "不使用（通用）"
STYLE_CURRENT = "沿用上方当前规则"
STYLE_JOURNAL = "单独指定期刊规则包"
STYLE_RULE = "单独指定基础格式规则"
STYLE_CUSTOM = "完全自定插入样式"
RUN_CONFIG = "run_config.json"
DELIVERY_GATE = "delivery_gate.json"
REVIEWER_PAGE = BASE_DIR / "web" / "reviewer.html"
ANNOTATION_TEMPLATE = BASE_DIR / "outputs" / "annotations_template" / "annotations.xlsx"
FORMULA_TEMPLATE = TEMPLATE_DIR / "formulas.template.json"


def _reviewer_html(pdf_path: str | None) -> str:
    if not pdf_path or not Path(pdf_path).exists():
        return (
            "<div class='review-empty'>"
            "<span class='review-empty-icon' aria-hidden='true'>PDF</span>"
            "<div><strong>预览尚未生成</strong>"
            "<p>完成前两步并生成快速预览后，可在这里逐页审阅排版结果。</p></div>"
            "</div>"
        )
    page_url = "/gradio_api/file=" + quote(str(REVIEWER_PAGE.resolve()), safe="")
    pdf_url = "/gradio_api/file=" + quote(str(Path(pdf_path).resolve()), safe="")
    return (
        "<iframe class='pdf-reviewer' title='PDF 在线审阅' "
        f"src='{page_url}?pdf={quote(pdf_url, safe='')}'></iframe>"
    )


REVIEW_BRIDGE_JS = """
() => {
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
}
"""

APP_CSS = """
:root {
  --ink: #101828;
  --ink-soft: #344054;
  --muted: #667085;
  --muted-light: #98a2b3;
  --line: #e4e7ec;
  --line-strong: #d0d5dd;
  --canvas: #f5f7fb;
  --surface: #ffffff;
  --surface-soft: #f9fafb;
  --primary: #155eef;
  --primary-dark: #004eeb;
  --primary-soft: #eff4ff;
  --primary-line: #c7d7fe;
  --indigo: #6941c6;
  --indigo-soft: #f4f3ff;
  --success: #067647;
  --success-soft: #ecfdf3;
  --warning: #b54708;
  --warning-soft: #fffaeb;
  --focus: rgba(21, 94, 239, 0.18);
  --shadow-xs: 0 1px 2px rgba(16, 24, 40, 0.05);
  --shadow-md: 0 8px 24px rgba(16, 24, 40, 0.08);
}
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background:
    radial-gradient(circle at 8% 0%, rgba(21, 94, 239, 0.07), transparent 25rem),
    radial-gradient(circle at 92% 14%, rgba(105, 65, 198, 0.045), transparent 22rem),
    var(--canvas) !important;
}
.gradio-container {
  width: 100% !important;
  max-width: 1320px !important;
  padding: 0 clamp(10px, 2.5vw, 32px) 80px !important;
  color: var(--ink);
  font-family: Inter, "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif !important;
  line-height: 1.5;
}
.gradio-container .main { padding: 16px clamp(0px, 2vw, 24px) !important; }
#masthead {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
  margin: 22px 0 16px;
  padding: 22px 26px;
  border: 1px solid #dfe4ed;
  border-radius: 16px;
  background:
    linear-gradient(115deg, rgba(255,255,255,0.98) 0%, rgba(250,252,255,0.96) 66%, rgba(244,247,255,0.95) 100%);
  box-shadow: 0 8px 28px rgba(16, 24, 40, 0.07);
}
#masthead::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: linear-gradient(90deg, var(--primary), #528bff 50%, var(--indigo));
}
#masthead::after {
  content: "";
  position: absolute;
  width: 180px;
  height: 180px;
  right: -70px;
  top: -118px;
  border: 1px solid rgba(21, 94, 239, 0.11);
  border-radius: 50%;
  box-shadow: 0 0 0 28px rgba(21, 94, 239, 0.018), 0 0 0 58px rgba(105, 65, 198, 0.012);
  pointer-events: none;
}
.brand-lockup { position: relative; z-index: 1; display: flex; align-items: center; gap: 14px; min-width: 0; }
.brand-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  border-radius: 11px;
  color: #fff;
  background: linear-gradient(145deg, #155eef, #0040c1);
  box-shadow: 0 6px 14px rgba(21, 94, 239, 0.22);
  font-size: 14px;
  font-weight: 800;
  letter-spacing: -0.04em;
}
.brand-copy { min-width: 0; }
.brand-eyebrow {
  margin: 0 0 2px;
  color: var(--primary);
  font-size: 11px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
#masthead h1 {
  margin: 0;
  color: var(--ink);
  font-size: 21px;
  font-weight: 720;
  line-height: 1.25;
  letter-spacing: -0.02em;
}
#masthead .brand-description {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.trust-row { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.system-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 30px;
  padding: 0 11px;
  border: 1px solid #abefc6;
  border-radius: 999px;
  background: var(--success-soft);
  color: var(--success);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.system-status::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #17b26a;
  box-shadow: 0 0 0 3px rgba(23, 178, 106, 0.12);
}
.trust-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-soft);
  color: var(--ink-soft);
  font-size: 12px;
  font-weight: 600;
}
.trust-pill::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  box-shadow: 0 0 0 3px rgba(6, 118, 71, 0.1);
}
.workflow {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 0 0 18px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: var(--shadow-xs);
}
.workflow-step {
  appearance: none;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 11px;
  width: 100%;
  min-height: 68px;
  padding: 10px 13px;
  border: 1px solid transparent;
  border-radius: 11px;
  background: transparent;
  color: var(--muted);
  text-align: left;
  font: inherit;
  cursor: pointer;
  transition: background .16s ease, border-color .16s ease, box-shadow .16s ease;
}
.workflow-step:hover { background: var(--surface-soft); border-color: var(--line); }
.workflow-step:focus-visible { outline: 3px solid var(--focus); outline-offset: -3px; }
.workflow-index {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: #fff;
  color: var(--muted);
  font-size: 12px;
  font-weight: 750;
}
.workflow-label { min-width: 0; }
.workflow-step strong { display: block; margin: 0 0 2px; color: var(--ink-soft); font-size: 13px; font-weight: 700; }
.workflow-step small { display: block; overflow: hidden; color: var(--muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.workflow-step.active {
  border-color: var(--primary-line);
  background: var(--primary-soft);
  box-shadow: 0 1px 3px rgba(21, 94, 239, 0.08);
}
.workflow-step.active .workflow-index { border-color: var(--primary); background: var(--primary); color: #fff; }
.workflow-step.active strong { color: #00359e; }
.workflow-step.complete .workflow-index { border-color: #abefc6; background: var(--success-soft); color: var(--success); }
.workflow-page { display: none; }
.workflow-page.active { display: block; }
.workflow-page > .panel, .workflow-page > .results { margin: 0; }
#workflow-tabs {
  overflow: visible !important;
  border: 0 !important;
  background: transparent !important;
}
#workflow-tabs > .tab-wrapper {
  height: auto !important;
  min-height: 70px !important;
  margin: 0 0 18px !important;
  overflow: visible !important;
}
#workflow-tabs .tab-container[role="tablist"] {
  display: grid !important;
  grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
  gap: 8px !important;
  width: 100% !important;
  max-width: none !important;
  height: auto !important;
  min-height: 70px !important;
  margin: 0 !important;
  padding: 8px !important;
  border: 1px solid var(--line) !important;
  border-radius: 16px !important;
  overflow: visible !important;
  background: rgba(255, 255, 255, 0.82) !important;
  box-shadow: var(--shadow-xs) !important;
}
#workflow-tabs button[role="tab"] {
  position: relative;
  display: flex !important;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  min-height: 52px !important;
  margin: 0 !important;
  padding: 0 16px !important;
  border: 1px solid transparent !important;
  border-radius: 10px !important;
  color: var(--muted) !important;
  background: transparent !important;
  font-size: 13px !important;
  font-weight: 700 !important;
  transition: transform .16s ease, background .16s ease, border-color .16s ease, box-shadow .16s ease;
}
#workflow-tabs button[role="tab"]::before {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--muted);
  font-size: 10px;
  font-weight: 800;
}
#workflow-tabs button[role="tab"]::after { display: none !important; }
#workflow-tabs button[role="tab"]:nth-child(1)::before { content: "01"; }
#workflow-tabs button[role="tab"]:nth-child(2)::before { content: "02"; }
#workflow-tabs button[role="tab"]:nth-child(3)::before { content: "03"; }
#workflow-tabs button[role="tab"]:nth-child(4)::before { content: "04"; }
#workflow-tabs button[role="tab"]:hover { transform: translateY(-1px); border-color: var(--line) !important; background: var(--surface-soft) !important; }
#workflow-tabs button[role="tab"]:focus { outline: 3px solid var(--focus) !important; outline-offset: 2px !important; }
#workflow-tabs button[role="tab"][aria-selected="true"] {
  border-color: var(--primary-line) !important;
  color: #00359e !important;
  background: var(--primary-soft) !important;
  box-shadow: 0 1px 3px rgba(21, 94, 239, 0.08) !important;
}
#workflow-tabs button[role="tab"][aria-selected="true"]::before {
  border-color: var(--primary);
  background: var(--primary);
  color: #fff;
  box-shadow: 0 4px 10px rgba(21, 94, 239, 0.2);
}
#workflow-tabs .tabitem { padding: 0 !important; border: 0 !important; background: transparent !important; }
#stage-input, #stage-rules, #stage-assets, #stage-review, #stage-export { scroll-margin-top: 18px; }
.panel {
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}
#stage-input > #stage-input,
#stage-rules > #stage-rules,
#stage-review > #stage-review {
  padding: 28px !important;
  border: 1px solid #dfe4ed !important;
  border-radius: 16px !important;
  background: var(--surface) !important;
  box-shadow: 0 10px 30px rgba(16, 24, 40, 0.055) !important;
}
#stage-assets > #stage-assets {
  margin-top: 14px !important;
  padding: 20px 22px !important;
  border: 1px solid var(--primary-line) !important;
  border-radius: 14px !important;
  background: linear-gradient(135deg, #f7f9ff, var(--primary-soft)) !important;
}
#stage-export > #stage-export {
  margin-top: 14px !important;
  padding: 24px !important;
  border: 1px solid var(--line) !important;
  border-radius: 14px !important;
  background: var(--surface) !important;
  box-shadow: var(--shadow-xs) !important;
}
.panel .styler, .results .styler { background: transparent !important; }
.panel .block.padded.hide-container,
.results .block.padded.hide-container {
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
}
.section-heading {
  display: flex;
  align-items: flex-start;
  gap: 13px;
  margin: 0 0 22px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--line);
}
.section-kicker {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 9px;
  border: 1px solid #d9e4ff;
  background: linear-gradient(145deg, #f6f8ff, var(--primary-soft));
  color: var(--primary);
  font-size: 12px;
  font-weight: 800;
}
.section-heading h2 { margin: 0; color: var(--ink); font-size: 18px; font-weight: 720; line-height: 1.35; letter-spacing: -0.01em; }
.section-heading p { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
.field-intro { margin: 2px 0 10px; }
.field-intro strong { display: block; color: var(--ink-soft); font-size: 13px; font-weight: 700; }
.field-intro span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }
.optional-badge, .required-badge {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  margin-left: 6px;
  padding: 0 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  vertical-align: 1px;
}
.field-intro .optional-badge, .field-intro .required-badge { display: inline-flex; margin-top: 0; }
.required-badge { background: var(--primary-soft); color: var(--primary); }
.optional-badge { background: #f2f4f7; color: var(--muted); }
.project-package {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin: 22px 0 14px;
  padding: 15px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-soft);
}
.project-package-icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 8px;
  background: #fff;
  color: var(--primary);
  box-shadow: 0 0 0 1px var(--line);
  font-size: 11px;
  font-weight: 800;
}
.project-package h3 { margin: 0 0 3px; color: var(--ink-soft); font-size: 13px; font-weight: 700; }
.project-package p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.55; }
.project-package .required { color: var(--warning); font-weight: 650; }
.panel .row { gap: 16px !important; background: transparent !important; }
.panel .form, .results .form { gap: 1px !important; border-radius: 10px !important; background: var(--line) !important; }
.panel label span, .results label span { color: var(--ink-soft) !important; font-size: 12px !important; font-weight: 650 !important; }
.panel input, .panel textarea, .panel select, .results input, .results textarea {
  min-height: 44px !important;
  border-color: var(--line-strong) !important;
  border-radius: 9px !important;
  background: #fff !important;
  color: var(--ink) !important;
  box-shadow: var(--shadow-xs) !important;
}
.panel textarea { padding-top: 11px !important; }
.panel input:focus, .panel textarea:focus, .panel select:focus {
  border-color: var(--primary) !important;
  box-shadow: 0 0 0 3px var(--focus) !important;
}
#source-upload, #asset-bundle, #annotation-bundle, #guide-upload, #reference-upload, #bib-upload {
  overflow: hidden;
  border: 1px dashed var(--line-strong) !important;
  border-radius: 12px !important;
  background: #fbfcff !important;
  box-shadow: none !important;
}
#source-upload { min-height: 178px; border-color: #9db5f8 !important; background: #f8faff !important; }
#source-upload:hover, #asset-bundle:hover, #annotation-bundle:hover, #guide-upload:hover, #reference-upload:hover, #bib-upload:hover {
  border-color: var(--primary) !important;
  background: var(--primary-soft) !important;
}
.source-sidecar {
  align-self: stretch;
  gap: 13px !important;
  padding: 17px !important;
  border: 1px solid var(--line) !important;
  border-radius: 12px !important;
  background: linear-gradient(180deg, #fff, var(--surface-soft)) !important;
}
.sidecar-head { display: flex; align-items: center; gap: 10px; }
.sidecar-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border: 1px solid var(--primary-line);
  border-radius: 9px;
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 9px;
  font-weight: 800;
}
.sidecar-head strong { display: block; color: var(--ink-soft); font-size: 12px; }
.sidecar-head small { display: block; margin-top: 2px; color: var(--muted); font-size: 10px; }
.format-support {
  margin-top: auto;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #fff;
}
.format-support > span { display: block; margin-bottom: 7px; color: var(--muted); font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
.format-support > div { display: flex; flex-wrap: wrap; gap: 5px; }
.format-support code {
  padding: 3px 6px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface-soft);
  color: var(--ink-soft);
  font-size: 9px;
  font-weight: 700;
}
.format-support small { display: block; margin-top: 8px; color: var(--muted); font-size: 10px; }
.rule-priority {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 18px;
  padding: 10px 12px;
  border: 1px solid #e4e1ff;
  border-radius: 9px;
  background: var(--indigo-soft);
  color: #5925dc;
  font-size: 10px;
}
.rule-priority > span { margin-right: 3px; color: var(--muted); font-weight: 650; }
.rule-priority strong { font-size: 10px; font-weight: 750; }
.rule-priority i { color: #9b8afb; font-style: normal; font-size: 14px; }
.advanced {
  margin-top: 16px;
  overflow: hidden;
  border: 1px solid var(--line) !important;
  border-radius: 11px !important;
  background: var(--surface) !important;
}
.advanced > button {
  min-height: 46px !important;
  padding: 0 14px !important;
  color: var(--ink-soft) !important;
  background: var(--surface-soft) !important;
  font-size: 12px !important;
  font-weight: 650 !important;
}
.advanced[open] > button { border-bottom: 1px solid var(--line) !important; }
.match-row { align-items: end !important; }
#match {
  min-height: 44px;
  border: 1px solid var(--primary-line) !important;
  border-radius: 9px !important;
  background: var(--primary-soft) !important;
  color: #00359e !important;
  font-weight: 700;
}
#match:hover { border-color: var(--primary) !important; background: #e0eaff !important; }
.stage-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}
.stage-actions-copy { color: var(--muted); font-size: 12px; }
.stage-next {
  appearance: none;
  min-height: 40px;
  padding: 0 15px;
  border: 1px solid var(--primary);
  border-radius: 9px;
  background: var(--primary);
  color: #fff;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 10px rgba(21, 94, 239, 0.18);
}
.stage-next:hover { background: var(--primary-dark); }
.stage-next.secondary {
  border-color: var(--line-strong);
  background: #fff;
  color: var(--ink-soft);
  box-shadow: var(--shadow-xs);
}
.generate-layout { display: flex; align-items: center; justify-content: space-between; gap: 22px; }
.generate-copy h3 { margin: 0 0 3px; color: var(--ink); font-size: 15px; }
.generate-copy p { margin: 0; color: var(--muted); font-size: 12px; }
.generation-pipeline { display: flex; align-items: center; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.generation-pipeline span {
  padding: 3px 7px;
  border: 1px solid var(--primary-line);
  border-radius: 999px;
  background: rgba(255,255,255,.72);
  color: #175cd3;
  font-size: 9px;
  font-weight: 700;
}
.generation-pipeline i { color: #84a7f8; font-size: 10px; font-style: normal; }
.mission-hero {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
  margin-bottom: 16px;
  padding: 34px 36px;
  border: 1px solid #173f72;
  border-radius: 18px;
  background:
    radial-gradient(circle at 84% 14%, rgba(45, 212, 191, .18), transparent 18rem),
    linear-gradient(125deg, #0b2345 0%, #123a67 58%, #0d5260 100%);
  color: #fff;
  box-shadow: 0 16px 34px rgba(11, 35, 69, .18);
}
.mission-hero::after {
  content: "";
  position: absolute;
  inset: 0;
  opacity: .16;
  background-image: radial-gradient(rgba(255,255,255,.8) .7px, transparent .7px);
  background-size: 18px 18px;
  pointer-events: none;
}
.mission-copy, .mission-seal { position: relative; z-index: 1; }
.mission-label { color: #7dd3fc; font-size: 10px; font-weight: 800; letter-spacing: .14em; }
.mission-copy h2 { margin: 8px 0 8px; color: #fff; font-size: 27px; line-height: 1.25; letter-spacing: -.02em; }
.mission-copy > p { max-width: 720px; margin: 0; color: #cbdcf0; font-size: 13px; line-height: 1.7; }
.mission-tags { display: flex; gap: 8px; margin-top: 18px; flex-wrap: wrap; }
.mission-tags span {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border: 1px solid rgba(255,255,255,.16);
  border-radius: 999px;
  background: rgba(255,255,255,.07);
  color: #e7f0fa;
  font-size: 10px;
  font-weight: 650;
  backdrop-filter: blur(8px);
}
.mission-tags i { width: 6px; height: 6px; margin-right: 7px; border-radius: 50%; background: #32d583; box-shadow: 0 0 0 3px rgba(50,213,131,.14); }
.mission-seal {
  display: grid;
  place-items: center;
  width: 112px;
  height: 112px;
  flex: 0 0 112px;
  border: 1px solid rgba(255,255,255,.22);
  border-radius: 50%;
  background: rgba(255,255,255,.07);
  box-shadow: inset 0 0 0 8px rgba(255,255,255,.025);
  text-align: center;
}
.mission-seal strong { display: block; margin-top: 20px; color: #fff; font-size: 21px; line-height: 1; }
.mission-seal span { align-self: start; color: #a9e9de; font-size: 9px; font-weight: 700; }
.competition-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 30px;
  padding: 0 11px;
  border: 1px solid #fedf89;
  border-radius: 999px;
  background: #fffaeb;
  color: #93370d;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .03em;
}
.competition-badge::before { content: "AI"; color: #b54708; font-size: 9px; }
.pitch-hero {
  min-height: 286px;
  padding: 38px 40px;
  border-color: #1f4d80;
  background:
    radial-gradient(circle at 78% 18%, rgba(45,212,191,.2), transparent 19rem),
    radial-gradient(circle at 96% 90%, rgba(247,195,67,.12), transparent 16rem),
    linear-gradient(126deg, #071d39 0%, #0e3158 55%, #0b5159 100%);
}
.pitch-hero::before {
  content: "";
  position: absolute;
  inset: 0;
  opacity: .16;
  background-image:
    linear-gradient(rgba(255,255,255,.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.08) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: linear-gradient(90deg, transparent, #000 55%, #000);
  pointer-events: none;
}
.pitch-hero .mission-copy { max-width: 770px; }
.pitch-hero .mission-label { color: #67e8f9; }
.pitch-hero .mission-copy h2 { max-width: 720px; font-size: clamp(27px, 3.2vw, 40px); line-height: 1.18; }
.pitch-hero .mission-copy > p { max-width: 750px; color: #c8d9eb; font-size: 14px; }
.judge-route {
  position: relative;
  z-index: 2;
  width: 248px;
  flex: 0 0 248px;
  padding: 18px;
  border: 1px solid rgba(255,255,255,.18);
  border-radius: 15px;
  background: rgba(5,24,46,.45);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.06);
  backdrop-filter: blur(14px);
}
.judge-route > span { color: #7dd3fc; font-size: 9px; font-weight: 850; letter-spacing: .13em; }
.judge-route > strong { display: block; margin: 4px 0 12px; color: #fff; font-size: 15px; }
.judge-route ol { margin: 0; padding: 0; list-style: none; counter-reset: route; }
.judge-route li {
  position: relative;
  min-height: 34px;
  padding: 0 0 10px 31px;
  color: #d8e7f4;
  font-size: 10px;
  line-height: 1.45;
  counter-increment: route;
}
.judge-route li:last-child { padding-bottom: 0; }
.judge-route li::before {
  content: "0" counter(route);
  position: absolute;
  left: 0;
  top: -1px;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 1px solid rgba(125,211,252,.38);
  border-radius: 7px;
  color: #67e8f9;
  font-size: 8px;
  font-weight: 850;
}
.judge-route li::after { content: ""; position: absolute; left: 11px; top: 24px; bottom: 1px; width: 1px; background: rgba(125,211,252,.2); }
.judge-route li:last-child::after { display: none; }
.rubric-strip {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: -2px 0 16px;
  padding: 11px 14px;
  border: 1px solid #dbe4f0;
  border-radius: 12px;
  background: rgba(255,255,255,.88);
  box-shadow: var(--shadow-xs);
}
.rubric-strip strong { margin-right: 3px; color: #0b2e59; font-size: 10px; }
.rubric-strip span { padding: 4px 8px; border-radius: 999px; background: #f2f4f7; color: var(--ink-soft); font-size: 9px; font-weight: 700; }
.pitch-section {
  margin-bottom: 16px;
  padding: 22px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255,255,255,.94);
  box-shadow: var(--shadow-xs);
}
.pitch-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 14px; }
.pitch-heading span { color: var(--primary); font-size: 9px; font-weight: 850; letter-spacing: .13em; }
.pitch-heading h3 { margin: 3px 0 0; color: var(--ink); font-size: 17px; letter-spacing: -.01em; }
.pitch-heading p { max-width: 520px; margin: 0; color: var(--muted); font-size: 10px; text-align: right; }
.pitch-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 10px; }
.pitch-card { position: relative; overflow: hidden; min-height: 134px; padding: 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); }
.pitch-card::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--pitch-accent); }
.pitch-card.problem { --pitch-accent: #f79009; }
.pitch-card.solution { --pitch-accent: #2e90fa; }
.pitch-card.proof { --pitch-accent: #12b76a; }
.pitch-card small { color: var(--pitch-accent); font-size: 8px; font-weight: 850; letter-spacing: .12em; }
.pitch-card strong { display: block; margin: 6px 0 5px; color: var(--ink); font-size: 13px; }
.pitch-card p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.65; }
.architecture-flow {
  display: grid;
  grid-template-columns: repeat(5,minmax(0,1fr));
  gap: 18px;
  margin-top: 12px;
  padding: 13px;
  border: 1px solid #d9e4ff;
  border-radius: 11px;
  background: #f8faff;
}
.architecture-flow span { position: relative; display: grid; place-items: center; min-height: 46px; padding: 7px; border: 1px solid var(--primary-line); border-radius: 9px; background: #fff; color: #0b3b77; font-size: 9px; font-weight: 750; text-align: center; }
.architecture-flow span:not(:last-child)::after { content: "→"; position: absolute; right: -15px; color: #84a7f8; font-size: 12px; }
.demo-launch {
  align-items: center !important;
  margin: 0 0 16px !important;
  padding: 18px 20px !important;
  border: 1px solid #9adbcf !important;
  border-radius: 14px !important;
  background: linear-gradient(110deg, #ecfdf3 0%, #f8fbff 66%, #eff4ff 100%) !important;
  box-shadow: var(--shadow-xs) !important;
}
.demo-launch > div { min-width: 0; }
.demo-launch-copy span { color: #067647; font-size: 9px; font-weight: 850; letter-spacing: .12em; }
.demo-launch-copy h3 { margin: 3px 0 2px; color: var(--ink); font-size: 15px; }
.demo-launch-copy p { margin: 0; color: var(--muted); font-size: 10px; }
#demo-run { min-height: 44px; border: 0 !important; border-radius: 9px !important; background: #0b5f55 !important; color: #fff !important; font-size: 11px !important; font-weight: 800 !important; box-shadow: 0 6px 14px rgba(11,95,85,.18); }
#demo-run:hover { background: #084c45 !important; transform: translateY(-1px); }
.overview-live {
  margin-bottom: 16px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255,255,255,.92);
  box-shadow: var(--shadow-xs);
}
.overview-live-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.overview-live-head > div span { display: block; color: var(--muted); font-size: 10px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; }
.overview-live-head > div strong { display: block; margin-top: 3px; color: var(--ink); font-size: 14px; }
.task-state { padding: 5px 9px; border-radius: 999px; font-size: 10px; font-weight: 750; }
.task-state.idle { background: #f2f4f7; color: var(--muted); }
.task-state.ready { background: var(--success-soft); color: var(--success); }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.metric-card {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 15px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #fff;
}
.metric-icon { display: grid; place-items: center; width: 36px; height: 36px; flex: 0 0 36px; border-radius: 9px; font-size: 8px; font-weight: 850; }
.metric-card small { display: block; color: var(--muted); font-size: 9px; font-weight: 700; }
.metric-card strong { display: block; margin-top: 1px; color: var(--ink); font-size: 21px; line-height: 1.15; }
.metric-card strong.metric-text { overflow: hidden; font-size: 13px; line-height: 1.6; text-overflow: ellipsis; white-space: nowrap; }
.metric-card p { margin: 3px 0 0; color: var(--muted-light); font-size: 9px; }
.metric-blue .metric-icon { background: var(--primary-soft); color: var(--primary); }
.metric-indigo .metric-icon { background: var(--indigo-soft); color: var(--indigo); }
.metric-green .metric-icon { background: var(--success-soft); color: var(--success); }
.metric-gold .metric-icon { background: var(--warning-soft); color: var(--warning); }
.launch-panel { margin-top: 0 !important; }
.launch-action { margin-top: 16px !important; }
.page-title {
  display: flex;
  align-items: flex-start;
  gap: 13px;
  margin-bottom: 16px;
  padding: 24px 26px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
  box-shadow: var(--shadow-xs);
}
.page-title > span { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 38px; border-radius: 10px; background: #0b2e59; color: #fff; font-size: 11px; font-weight: 800; }
.page-title h2 { margin: 0; color: var(--ink); font-size: 20px; letter-spacing: -.02em; }
.page-title p { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.capability-row { gap: 14px !important; margin-bottom: 14px !important; }
.capability-card {
  position: relative;
  overflow: hidden;
  min-height: 252px;
  padding: 22px !important;
  border: 1px solid var(--line) !important;
  border-radius: 15px !important;
  background: #fff !important;
  box-shadow: 0 8px 24px rgba(16,24,40,.05) !important;
}
.capability-card > .capability-card {
  min-height: 0;
  padding: 0 !important;
  border: 0 !important;
  border-radius: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}
.capability-card .styler,
.capability-card .block,
.capability-card .html-container { background: transparent !important; }
.capability-card::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--card-accent); }
.capability-card > .capability-card::before { display: none; }
.capability-card.tone-green { --card-accent: #12b76a; --card-soft: #ecfdf3; --card-ink: #067647; }
.capability-card.tone-gold { --card-accent: #e59700; --card-soft: #fffaeb; --card-ink: #b54708; }
.capability-card.tone-blue { --card-accent: #2e90fa; --card-soft: #eff8ff; --card-ink: #175cd3; }
.capability-card.tone-red { --card-accent: #f04438; --card-soft: #fef3f2; --card-ink: #b42318; }
.capability-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.capability-head span { color: var(--card-ink); font-size: 9px; font-weight: 850; letter-spacing: .12em; }
.capability-head em { padding: 4px 7px; border-radius: 999px; background: var(--card-soft); color: var(--card-ink); font-size: 9px; font-style: normal; font-weight: 750; }
.capability-card h3 { margin: 0 0 6px; color: var(--ink); font-size: 17px; }
.capability-card > .styler > .block p, .capability-card > .block p, .capability-card p { color: var(--muted); font-size: 11px; line-height: 1.65; }
.capability-feedback { margin: 14px 0 12px; padding: 11px 12px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-soft); }
.capability-feedback span { display: block; color: var(--ink-soft); font-size: 10px; font-weight: 750; }
.capability-feedback p { margin: 3px 0 0 !important; color: var(--muted) !important; font-size: 10px !important; }
.capability-feedback.success { border-color: #abefc6; background: var(--success-soft); }
.capability-feedback.success span { color: var(--success); }
.capability-feedback.warning { border-color: #fedf89; background: var(--warning-soft); }
.capability-feedback.warning span { color: var(--warning); }
.capability-action { min-height: 40px !important; margin-top: auto !important; border-radius: 8px !important; color: var(--card-ink) !important; border-color: color-mix(in srgb, var(--card-accent) 35%, white) !important; background: var(--card-soft) !important; font-size: 11px !important; font-weight: 750 !important; }
.insert-console { margin-top: 4px !important; padding: 20px !important; border: 1px solid #b2ddff !important; border-radius: 15px !important; background: linear-gradient(135deg, #f5fbff 0%, #ffffff 72%) !important; box-shadow: var(--shadow-xs) !important; }
.insert-console > .insert-console { margin: 0 !important; padding: 0 !important; border: 0 !important; border-radius: 0 !important; background: transparent !important; box-shadow: none !important; }
.insert-console-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.insert-console-head span { color: #175cd3; font-size: 9px; font-weight: 850; letter-spacing: .12em; }
.insert-console-head h3 { margin: 4px 0 0; color: var(--ink); font-size: 16px; }
.insert-console-head p { margin: 5px 0 0; color: var(--muted); font-size: 10px; }
.insert-console-head em { flex: 0 0 auto; padding: 5px 8px; border-radius: 999px; background: #eff8ff; color: #175cd3; font-size: 8px; font-style: normal; font-weight: 800; }
.insert-console .insert-action { min-height: 42px !important; border: 0 !important; border-radius: 8px !important; background: #175cd3 !important; color: #fff !important; font-size: 11px !important; font-weight: 750 !important; }
.insertion-feedback { min-height: 0 !important; margin-top: 10px !important; }
.comparison-shell { padding: 20px; border: 1px solid var(--line); border-radius: 16px; background: #fff; box-shadow: var(--shadow-xs); }
.compare-score { display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px; padding: 16px 18px; border-radius: 12px; background: linear-gradient(110deg, #0b2e59, #164a77); color: #fff; }
.compare-score > span { color: #b9cee3; font-size: 10px; font-weight: 700; }
.compare-score strong { color: #fff; font-size: 25px; line-height: 1; }
.compare-score strong i { margin: 0 8px; color: #6ce9d4; font-style: normal; font-size: 15px; }
.compare-score small { margin-left: auto; color: #b9cee3; font-size: 10px; }
.compare-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.compare-columns article { padding: 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); }
.compare-columns header { display: flex; align-items: center; gap: 8px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
.compare-columns header strong { color: var(--ink-soft); font-size: 12px; }
.compare-columns header em { margin-left: auto; color: var(--muted); font-size: 9px; font-style: normal; }
.compare-dot { width: 8px; height: 8px; border-radius: 50%; }
.compare-dot.before { background: #f79009; }
.compare-dot.after { background: #12b76a; }
.compare-columns ul { margin: 10px 0 0; padding: 0; list-style: none; }
.compare-columns li { display: flex; align-items: flex-start; gap: 8px; padding: 7px 0; border-bottom: 1px solid #edf0f4; }
.compare-columns li:last-child { border-bottom: 0; }
.compare-columns li p { margin: 0; color: var(--ink-soft); font-size: 10px; line-height: 1.5; }
.severity { flex: 0 0 auto; padding: 2px 5px; border-radius: 4px; background: #fee4e2; color: #b42318; font-size: 8px; font-weight: 750; text-transform: uppercase; }
.severity.warning { background: #fef0c7; color: #b54708; }
.empty-item, .more-item { color: var(--muted) !important; font-size: 10px; }
.preview-compare { display: grid; grid-template-columns: minmax(0, 1fr) 34px minmax(0, 1fr); align-items: center; gap: 12px; margin-top: 12px; }
.preview-compare > article { padding: 13px; border: 1px solid var(--line); border-radius: 12px; background: #f8fafc; }
.preview-compare header { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 9px; color: var(--ink-soft); font-size: 10px; font-weight: 750; }
.preview-compare header em { color: var(--muted); font-size: 8px; font-style: normal; font-weight: 650; }
.preview-arrow { display: grid; place-items: center; width: 30px; height: 30px; border: 1px solid var(--primary-line); border-radius: 50%; background: var(--primary-soft); color: var(--primary); font-size: 14px; font-weight: 850; }
.paper-snapshot { position: relative; overflow: hidden; min-height: 118px; padding: 18px 16px; border: 1px solid #d0d5dd; border-radius: 4px; background: #fff; box-shadow: 0 5px 14px rgba(16,24,40,.06); }
.paper-snapshot::before { content: ""; display: block; width: 38%; height: 7px; margin: 0 auto 12px; border-radius: 3px; background: #475467; }
.paper-snapshot b, .paper-snapshot i, .paper-snapshot span { display: block; height: 4px; margin-top: 7px; border-radius: 2px; background: #d0d5dd; }
.paper-snapshot b { width: 54%; height: 5px; margin-top: 0; background: #667085; }
.paper-snapshot i { width: 42%; height: 28px; border: 1px dashed #f79009; background: #fffaeb; }
.paper-snapshot span:nth-of-type(2n) { width: 82%; }
.paper-snapshot.original { transform: rotate(-.3deg); border-color: #fdb022; }
.paper-snapshot.repaired { border-color: #75e0a7; }
.paper-snapshot.repaired::after { content: "已规范"; position: absolute; right: 10px; bottom: 9px; padding: 3px 6px; border-radius: 999px; background: #ecfdf3; color: #067647; font-size: 7px; font-weight: 850; }
.paper-snapshot.repaired i { width: 48%; border-style: solid; border-color: #75e0a7; background: #ecfdf3; }
.preview-empty { opacity: .62; }
.subsection-title { margin: 18px 0 10px; }
.subsection-title span { color: var(--primary); font-size: 9px; font-weight: 850; letter-spacing: .12em; }
.subsection-title h3 { margin: 3px 0 0; color: var(--ink); font-size: 15px; }
.activity-log { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; margin: 0; padding: 0; list-style: none; }
.activity-log li { position: relative; min-height: 62px; padding: 12px 13px 12px 32px; border: 1px solid var(--line); border-radius: 10px; background: #fff; }
.activity-log li::before { content: ""; position: absolute; left: 14px; top: 18px; width: 7px; height: 7px; border: 2px solid #fff; border-radius: 50%; background: var(--primary); box-shadow: 0 0 0 2px var(--primary-line); }
.activity-log span { display: block; color: var(--primary); font-size: 9px; font-weight: 750; }
.activity-log p { margin: 3px 0 0; color: var(--ink-soft); font-size: 10px; line-height: 1.5; }
.delivery-dashboard { padding: 20px; border: 1px solid var(--line); border-radius: 16px; background: #fff; box-shadow: var(--shadow-xs); }
.delivery-status-line { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 18px; border-radius: 12px; background: #0b2e59; color: #fff; }
.delivery-status-line span { display: block; color: #b9cee3; font-size: 9px; font-weight: 700; }
.delivery-status-line strong { display: block; margin-top: 3px; color: #fff !important; font-size: 16px; }
.delivery-status-line em { padding: 5px 8px; border-radius: 999px; font-size: 9px; font-style: normal; font-weight: 800; }
.delivery-status-line em.idle { background: rgba(255,255,255,.1); color: #d0d5dd; }
.delivery-status-line em.warning { background: #fffaeb; color: #b54708; }
.delivery-status-line em.success { background: #ecfdf3; color: #067647; }
.delivery-overview-grid { display: grid; grid-template-columns: 1fr 1.35fr .7fr; gap: 10px; margin-top: 12px; }
.delivery-overview-grid article { min-width: 0; padding: 15px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-soft); }
.delivery-overview-grid small { display: block; color: var(--muted); font-size: 9px; font-weight: 700; }
.delivery-overview-grid strong { display: block; overflow: hidden; margin-top: 5px; color: var(--ink); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.delivery-overview-grid p { margin: 4px 0 0; color: var(--muted-light); font-size: 9px; }
.preflight-check { margin-top: 12px; padding: 15px; border: 1px solid var(--line); border-radius: 11px; }
.preflight-check > strong { color: var(--ink-soft); font-size: 11px; }
.preflight-check ul { display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; margin: 10px 0 0; padding: 0; list-style: none; }
.preflight-check li { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 9px; }
.preflight-check li span { display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; font-weight: 800; }
.preflight-check li.pass span { background: var(--success-soft); color: var(--success); }
.preflight-check li.pending span { background: #f2f4f7; color: var(--muted); }
.delivery-action { margin-top: 14px !important; }
#run {
  min-width: 190px;
  min-height: 46px;
  border: 0 !important;
  border-radius: 9px !important;
  background: var(--primary) !important;
  color: #fff !important;
  font-weight: 750;
  box-shadow: 0 6px 14px rgba(21, 94, 239, 0.2);
  transition: transform .16s ease, box-shadow .16s ease, background .16s ease;
}
#run:hover { transform: translateY(-1px); background: var(--primary-dark) !important; box-shadow: 0 9px 20px rgba(21, 94, 239, 0.24); }
.results { background: transparent !important; }
.result-heading { margin-bottom: 18px; }
.result-heading .status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 8px;
  border-radius: 50%;
  background: var(--muted-light);
}
.artifact-row { margin: 12px 0 16px !important; }
.artifact-row > div {
  overflow: hidden;
  border: 1px solid var(--line) !important;
  border-radius: 10px !important;
  background: var(--surface-soft) !important;
}
.artifact-row .empty.large { min-height: 108px !important; height: 108px !important; }
.artifact-row .empty.large::after {
  content: "等待预览结果";
  margin-left: 8px;
  color: var(--muted-light);
  font-size: 10px;
  font-weight: 600;
}
.pdf-reviewer {
  width: 100%;
  height: 720px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #eef1f6;
}
.review-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-height: 260px;
  padding: 24px;
  border: 1px dashed var(--line-strong);
  border-radius: 12px;
  color: var(--muted);
  background: var(--surface-soft);
  text-align: left;
}
.review-empty-icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 58px;
  border: 1px solid var(--primary-line);
  border-radius: 9px;
  background: #fff;
  color: var(--primary);
  font-size: 11px;
  font-weight: 800;
  box-shadow: var(--shadow-xs);
}
.review-empty strong { display: block; margin-bottom: 4px; color: var(--ink-soft); font-size: 14px; }
.review-empty p { max-width: 360px; margin: 0; font-size: 12px; line-height: 1.55; }
.delivery-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; margin-bottom: 16px; }
.delivery-head h3 { margin: 0 0 4px; color: var(--ink); font-size: 16px; }
.delivery-head p { margin: 0; color: var(--muted); font-size: 12px; }
.delivery-badge { padding: 5px 9px; border-radius: 999px; background: var(--warning-soft); color: var(--warning); font-size: 10px; font-weight: 750; white-space: nowrap; }
#formal {
  min-height: 46px;
  border: 1px solid #101828 !important;
  border-radius: 9px !important;
  background: #101828 !important;
  color: #fff !important;
  font-weight: 750;
  transition: transform .16s ease, background .16s ease, box-shadow .16s ease;
}
#formal:hover { transform: translateY(-1px); background: #1d2939 !important; box-shadow: 0 8px 18px rgba(16, 24, 40, .2); }
button.secondary { border-radius: 9px !important; border-color: var(--line-strong) !important; color: var(--ink-soft) !important; }

/* Institutional workspace theme */
:root {
  --ink: #172033;
  --ink-soft: #344054;
  --muted: #667085;
  --muted-light: #98a2b3;
  --line: #dfe4ea;
  --line-strong: #c8d0da;
  --canvas: #f4f6f8;
  --surface-soft: #f7f8fa;
  --primary: #17375e;
  --primary-dark: #102943;
  --primary-soft: #edf2f7;
  --primary-line: #cbd7e4;
  --success: #126b57;
  --success-soft: #edf7f3;
  --warning: #9a5b13;
  --warning-soft: #fbf5e9;
  --shadow-xs: 0 1px 2px rgba(16,24,40,.04);
  --shadow-md: 0 10px 30px rgba(16,24,40,.06);
}
body { background: var(--canvas) !important; }
.gradio-container { max-width: 1420px !important; padding-top: 0 !important; }
#masthead {
  min-height: 74px;
  margin: 18px 0 14px;
  padding: 15px 22px;
  border: 0;
  border-radius: 10px;
  background: #10273f;
  box-shadow: 0 8px 22px rgba(16,39,63,.13);
}
#masthead::before, #masthead::after { display: none; }
.brand-mark {
  width: 38px;
  height: 38px;
  flex-basis: 38px;
  border: 1px solid rgba(255,255,255,.16);
  border-radius: 8px;
  background: #e9eef4;
  color: #10273f;
  box-shadow: none;
  font-size: 12px;
}
.brand-eyebrow { margin-bottom: 1px; color: #8fb0cd; font-size: 9px; letter-spacing: .16em; }
#masthead h1 { color: #fff; font-size: 17px; font-weight: 680; letter-spacing: .01em; }
#masthead .brand-description { margin-top: 2px; color: #aebfce; font-size: 10px; }
.header-meta {
  min-height: 27px;
  padding: 0 10px;
  border-right: 1px solid rgba(255,255,255,.13);
  color: #b7c7d5;
  font-size: 10px;
  line-height: 27px;
}
.system-status {
  min-height: 28px;
  padding: 0 10px;
  border-color: rgba(100,211,177,.28);
  background: rgba(18,107,87,.22);
  color: #a8e3d2;
  font-size: 9px;
}
.system-status::before { width: 5px; height: 5px; background: #5dd6b3; box-shadow: none; }
#workflow-tabs > .tab-wrapper { min-height: 58px !important; margin-bottom: 16px !important; }
#workflow-tabs .tab-container[role="tablist"] {
  min-height: 58px !important;
  padding: 4px !important;
  border-color: #dbe1e8 !important;
  border-radius: 10px !important;
  background: #fff !important;
  box-shadow: none !important;
}
#workflow-tabs button[role="tab"] {
  min-height: 48px !important;
  padding: 0 15px !important;
  border-radius: 7px !important;
  font-size: 12px !important;
  font-weight: 620 !important;
}
#workflow-tabs button[role="tab"]::before {
  width: 24px;
  height: 24px;
  flex-basis: 24px;
  border: 0;
  border-radius: 6px;
  background: #eef1f4;
  color: #667085;
  font-size: 8px;
}
#workflow-tabs button[role="tab"][aria-selected="true"] {
  border-color: transparent !important;
  background: #eef2f6 !important;
  color: #102f51 !important;
  box-shadow: inset 0 -2px #17375e !important;
}
#workflow-tabs button[role="tab"][aria-selected="true"]::before { background: #17375e; box-shadow: none; }
.workspace-cover {
  display: grid;
  grid-template-columns: minmax(0,1fr) 310px;
  gap: 28px;
  margin-bottom: 14px;
  padding: 30px 32px;
  border: 1px solid #d9dfe7;
  border-radius: 12px;
  background: #fff;
  box-shadow: var(--shadow-xs);
}
.workspace-path { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 10px; }
.workspace-path span { color: #315b85; font-weight: 700; }
.workspace-path i { color: #b3bdc8; font-style: normal; }
.workspace-path strong { color: var(--muted); font-weight: 600; }
.workspace-cover h2 { margin: 13px 0 8px; color: #142235; font-size: clamp(25px,2.4vw,34px); font-weight: 650; letter-spacing: -.025em; }
.workspace-cover-main > p { max-width: 790px; margin: 0; color: #5d6979; font-size: 13px; line-height: 1.8; }
.workspace-meta { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 0; margin-top: 25px; border-top: 1px solid #e5e9ee; }
.workspace-meta > div { min-width: 0; padding: 15px 16px 0 0; }
.workspace-meta > div + div { padding-left: 16px; border-left: 1px solid #e5e9ee; }
.workspace-meta small { display: block; color: #8a94a3; font-size: 9px; }
.workspace-meta strong { display: block; overflow: hidden; margin-top: 4px; color: #27364a; font-size: 11px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.workspace-status-panel { padding: 17px 18px; border: 1px solid #d7dee7; border-radius: 9px; background: #f7f8fa; }
.status-panel-head { display: flex; align-items: center; gap: 8px; padding-bottom: 13px; border-bottom: 1px solid #dfe4ea; }
.status-panel-head span { width: 7px; height: 7px; border-radius: 50%; background: #2f8f73; box-shadow: 0 0 0 3px #dff1ea; }
.status-panel-head strong { color: #203246; font-size: 11px; }
.workspace-status-panel dl { margin: 6px 0 0; }
.workspace-status-panel dl > div { display: flex; justify-content: space-between; gap: 16px; padding: 9px 0; border-bottom: 1px solid #e5e9ee; }
.workspace-status-panel dl > div:last-child { border-bottom: 0; }
.workspace-status-panel dt { color: #7b8796; font-size: 9px; }
.workspace-status-panel dd { margin: 0; color: #344054; font-size: 9px; font-weight: 620; text-align: right; }
.formal-overview-grid { display: grid; grid-template-columns: minmax(0,1.75fr) minmax(280px,.75fr); gap: 14px; margin-bottom: 14px; }
.formal-card { padding: 20px 22px; border: 1px solid #dfe4ea; border-radius: 11px; background: #fff; box-shadow: var(--shadow-xs); }
.formal-card > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 17px; }
.formal-card header span { color: #55718f; font-size: 8px; font-weight: 800; letter-spacing: .15em; }
.formal-card header h3 { margin: 3px 0 0; color: #1f2f43; font-size: 14px; font-weight: 650; }
.formal-card header em { padding: 4px 7px; border-radius: 5px; background: #f1f3f6; color: #667085; font-size: 8px; font-style: normal; }
.formal-workflow { display: grid; grid-template-columns: repeat(5,minmax(0,1fr)); }
.formal-workflow > div { position: relative; min-width: 0; padding: 0 16px; }
.formal-workflow > div:first-child { padding-left: 0; }
.formal-workflow > div + div { border-left: 1px solid #e1e6ec; }
.formal-workflow b { display: block; color: #66809a; font-size: 9px; font-weight: 750; }
.formal-workflow strong { display: block; margin-top: 8px; color: #27364a; font-size: 11px; font-weight: 650; }
.formal-workflow small { display: block; margin-top: 4px; color: #8a94a3; font-size: 8px; line-height: 1.45; }
.governance-card ul { margin: 0; padding: 0; list-style: none; }
.governance-card li { display: flex; align-items: flex-start; gap: 10px; padding: 9px 0; border-top: 1px solid #e8ebef; }
.governance-card li:first-child { border-top: 0; padding-top: 0; }
.governance-card li i { display: grid; place-items: center; width: 24px; height: 24px; flex: 0 0 24px; border-radius: 6px; background: #edf2f7; color: #355d82; font-size: 8px; font-style: normal; font-weight: 800; }
.governance-card li strong { display: block; color: #344054; font-size: 10px; }
.governance-card li small { display: block; margin-top: 2px; color: #7d8896; font-size: 8px; }
.review-alignment { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 14px; padding: 13px 18px; border: 1px solid #d9dfe7; border-radius: 9px; background: #f9fafb; }
.review-alignment > div span { display: block; color: #7e8997; font-size: 8px; }
.review-alignment > div strong { display: block; margin-top: 3px; color: #314257; font-size: 10px; }
.review-alignment ul { display: flex; gap: 7px; margin: 0; padding: 0; list-style: none; }
.review-alignment li { padding: 4px 8px; border: 1px solid #dfe4ea; border-radius: 5px; background: #fff; color: #667085; font-size: 8px; }
.overview-live { padding: 17px 18px; border-radius: 11px; box-shadow: none; }
.metric-grid { gap: 0; border: 1px solid #e1e5ea; border-radius: 9px; overflow: hidden; }
.metric-card { padding: 14px 16px; border: 0; border-radius: 0; }
.metric-card + .metric-card { border-left: 1px solid #e1e5ea; }
.metric-icon { width: 31px; height: 31px; flex-basis: 31px; border-radius: 6px; background: #f0f3f6 !important; color: #46637e !important; }
.metric-card strong { font-size: 18px; }
.formal-demo-launch { padding: 15px 18px !important; border-color: #dce2e9 !important; border-radius: 10px !important; background: #fff !important; box-shadow: none !important; }
.formal-demo-launch .demo-launch-copy span { color: #607a95; }
.formal-demo-launch .demo-launch-copy h3 { font-size: 13px; font-weight: 650; }
#demo-run { min-height: 40px; border: 1px solid #b8c4d1 !important; background: #fff !important; color: #17375e !important; box-shadow: none; }
#demo-run:hover { border-color: #17375e !important; background: #f2f5f8 !important; transform: none; }
#stage-input > #stage-input, #stage-rules > #stage-rules, #stage-review > #stage-review { border-radius: 11px !important; box-shadow: none !important; }
#stage-assets > #stage-assets { border-color: #d3dce6 !important; border-radius: 10px !important; background: #f2f5f8 !important; }
.section-kicker { border-color: #d5dee8; border-radius: 6px; background: #edf2f7; color: #315b85; }
.section-heading h2 { font-weight: 650; }
#run { border-radius: 7px !important; background: #17375e !important; box-shadow: none; }
#run:hover { background: #102943 !important; box-shadow: none; transform: none; }
.page-title { border-radius: 11px; box-shadow: none; }
.page-title > span { border-radius: 7px; background: #17375e; }
.capability-card { min-height: 232px; border-radius: 11px !important; box-shadow: none !important; }
.capability-card::before { inset: 0 0 auto 0; width: auto; height: 2px; }
.insert-console, .comparison-shell, .delivery-dashboard { border-radius: 11px !important; box-shadow: none !important; }

/* Compact, function-first layout */
#masthead { min-height: 62px; margin: 12px 0 10px; padding: 11px 18px; }
#masthead .brand-description { display: none; }
#workflow-tabs > .tab-wrapper { min-height: 52px !important; margin-bottom: 12px !important; }
#workflow-tabs .tab-container[role="tablist"] { min-height: 52px !important; }
#workflow-tabs button[role="tab"] { min-height: 42px !important; }
.compact-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 30px;
  margin-bottom: 12px;
  padding: 20px 24px;
  border: 1px solid #dce2e8;
  border-radius: 10px;
  background: #fff;
}
.compact-overview > div { min-width: 0; }
.compact-overview > div > span { color: #55718f; font-size: 9px; font-weight: 750; }
.compact-overview h2 { margin: 5px 0 4px; color: #17283b; font-size: 22px; font-weight: 650; letter-spacing: -.015em; }
.compact-overview p { margin: 0; color: #667085; font-size: 11px; }
.compact-overview ul { display: flex; flex: 0 0 auto; margin: 0; padding: 0; list-style: none; }
.compact-overview li { min-width: 145px; padding: 4px 16px; border-left: 1px solid #e2e6eb; }
.compact-overview li small { display: block; color: #8b95a2; font-size: 8px; }
.compact-overview li strong { display: block; margin-top: 4px; color: #344054; font-size: 9px; font-weight: 650; }
.overview-live { margin-bottom: 12px; padding: 13px 14px; }
.overview-live-head { margin-bottom: 10px; }
.metric-card { padding: 11px 13px; }
.metric-card p { display: none; }
.metric-card strong { font-size: 16px; }
.metric-card strong.metric-text { font-size: 11px; }
.formal-demo-launch { margin-bottom: 12px !important; padding: 11px 14px !important; }
.formal-demo-launch .demo-launch-copy h3 { margin: 0; font-size: 12px; }
.formal-demo-launch .demo-launch-copy p { margin-top: 2px; font-size: 9px; }
#demo-run { min-height: 36px; }
#stage-input > #stage-input, #stage-rules > #stage-rules, #stage-review > #stage-review { padding: 20px !important; }
.section-heading { margin-bottom: 16px; padding-bottom: 14px; }
.section-heading p { font-size: 11px; }
#source-upload { min-height: 108px; }
#asset-bundle > button, #annotation-bundle > button, #formula-bundle > button, #guide-upload > button, #reference-upload > button, #bib-upload > button,
#asset-bundle > .wrap, #annotation-bundle > .wrap, #formula-bundle > .wrap, #guide-upload > .wrap, #reference-upload > .wrap, #bib-upload > .wrap {
  height: 92px !important;
  min-height: 92px !important;
}
.source-sidecar { padding: 13px !important; }
.format-support { padding: 9px; }
.advanced { margin-top: 10px; }
.advanced > button { min-height: 40px !important; }
#stage-assets > #stage-assets { margin-top: 10px !important; padding: 15px 17px !important; }
.generation-pipeline { margin-top: 7px; }
.page-title { margin-bottom: 12px; padding: 15px 18px; }
.page-title > span { width: 32px; height: 32px; flex-basis: 32px; }
.page-title h2 { font-size: 17px; }
.page-title p { margin-top: 2px; font-size: 10px; }
.capability-row { gap: 10px !important; margin-bottom: 10px !important; }
.capability-card { min-height: 178px; padding: 16px !important; }
.capability-head { margin-bottom: 8px; }
.capability-card h3 { margin-bottom: 3px; font-size: 14px; }
.capability-card > .styler > .block p, .capability-card > .block p, .capability-card p { font-size: 9px; line-height: 1.5; }
.capability-feedback { margin: 9px 0 8px; padding: 8px 9px; }
.capability-feedback p { font-size: 8px !important; }
.capability-action { min-height: 34px !important; }
.compact-tool.insert-console { overflow: hidden !important; max-width: 100%; margin-top: 0 !important; padding: 0 !important; border-color: #dfe4ea !important; background: #fff !important; }
.compact-tool > button { max-width: 100%; min-height: 42px !important; }
.compact-tool-note { margin: 0 0 10px !important; color: #667085 !important; font-size: 9px !important; }
.ai-rule-panel { margin-top: 12px !important; padding: 15px !important; border: 1px solid #cfdbea !important; border-radius: 9px !important; background: #f8fafc !important; }
.ai-rule-panel > .ai-rule-panel { margin: 0 !important; padding: 0 !important; border: 0 !important; background: transparent !important; }
.ai-rule-head span { color: #315b85; font-size: 8px; font-weight: 800; letter-spacing: .12em; }
.ai-rule-head h3 { margin: 4px 0 3px; color: #17283b; font-size: 14px; }
.ai-rule-head p { margin: 0 0 10px; color: #667085; font-size: 10px; }
.ai-rule-actions { align-items: center !important; gap: 10px !important; }
#identify-rules { min-height: 39px !important; border: 1px solid #17375e !important; border-radius: 7px !important; background: #17375e !important; color: #fff !important; }
#ai-rule-table { overflow: hidden !important; margin-top: 8px !important; border: 1px solid #dce3eb !important; border-radius: 7px !important; background: #fff !important; }
.template-actions { gap: 8px !important; margin-top: 4px !important; }
.template-actions button { min-height: 34px !important; border-color: #d5dde6 !important; background: #fff !important; color: #344054 !important; font-size: 10px !important; }
.comparison-shell { padding: 15px; }
.compare-score { margin-bottom: 10px; padding: 12px 14px; }
.compare-columns { gap: 8px; }
.compare-columns article { padding: 12px; }
.preview-compare { margin-top: 8px; }
.compact-results { margin-top: 8px; }
.compact-results > button { min-height: 38px !important; }
.artifact-row { margin: 8px 0 !important; }
.pdf-reviewer { height: 620px; }
.delivery-dashboard { padding: 15px; }
.delivery-status-line { padding: 12px 14px; }
.delivery-overview-grid { margin-top: 8px; }
.preflight-check { margin-top: 8px; padding: 12px; }
#stage-export > #stage-export { margin-top: 10px !important; padding: 18px !important; }
footer { display: none !important; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; }
}
@media (max-width: 860px) {
  .gradio-container { padding: 0 16px 48px !important; }
  #masthead { align-items: flex-start; margin-top: 12px; padding: 18px; }
  .compact-overview { align-items: flex-start; flex-direction: column; gap: 16px; }
  .compact-overview ul { width: 100%; }
  .compact-overview li { min-width: 0; flex: 1; }
  .trust-row { display: none; }
  #masthead .brand-description { white-space: normal; }
  .workflow { grid-template-columns: 1fr; }
  .workflow-step { min-height: 58px; }
  #stage-input > #stage-input,
  #stage-rules > #stage-rules,
  #stage-review > #stage-review { padding: 20px 16px !important; }
  .generate-layout, .stage-actions, .delivery-head { align-items: stretch; flex-direction: column; }
  .rule-priority { align-items: flex-start; flex-wrap: wrap; }
  #run { width: 100%; }
  .pdf-reviewer { height: 520px; }
  .mission-hero { padding: 26px 22px; }
  .mission-seal { display: none; }
  .pitch-hero { align-items: stretch; flex-direction: column; }
  .judge-route { width: auto; flex-basis: auto; }
  .rubric-strip { align-items: flex-start; flex-wrap: wrap; }
  .pitch-heading { align-items: flex-start; flex-direction: column; }
  .pitch-heading p { max-width: none; text-align: left; }
  .pitch-grid { grid-template-columns: 1fr; }
  .architecture-flow { grid-template-columns: 1fr; gap: 7px; }
  .architecture-flow span:not(:last-child)::after { content: "↓"; right: auto; bottom: -11px; }
  .demo-launch { align-items: stretch !important; flex-direction: column; }
  #demo-run { width: 100%; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .capability-row { flex-direction: column; }
  .capability-row > .capability-card { width: 100% !important; max-width: none !important; flex: 1 1 100% !important; }
  .compare-columns, .preview-compare, .delivery-overview-grid { grid-template-columns: 1fr; }
  .preview-arrow { transform: rotate(90deg); }
  .preflight-check ul { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .activity-log { grid-template-columns: 1fr; }
  .workspace-cover { grid-template-columns: 1fr; padding: 24px; }
  .workspace-status-panel { width: auto; }
  .workspace-meta { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .workspace-meta > div:nth-child(3) { padding-left: 0; border-left: 0; }
  .formal-overview-grid { grid-template-columns: 1fr; }
  .formal-workflow { grid-template-columns: 1fr; gap: 0; }
  .formal-workflow > div, .formal-workflow > div:first-child { padding: 10px 0; }
  .formal-workflow > div + div { border-top: 1px solid #e1e6ec; border-left: 0; }
  .review-alignment { align-items: flex-start; flex-direction: column; }
  .review-alignment ul { flex-wrap: wrap; }
  .metric-card + .metric-card { border-left: 0; }
}
@media (max-width: 520px) {
  .gradio-container { padding: 0 10px 36px !important; }
  #masthead { border-radius: 12px; }
  .compact-overview { padding: 16px; }
  .compact-overview h2 { font-size: 20px; }
  .compact-overview ul { display: grid; grid-template-columns: 1fr; }
  .compact-overview li { padding: 9px 0; border-top: 1px solid #e2e6eb; border-left: 0; }
  .compact-overview li:first-child { border-top: 0; }
  .brand-mark { width: 38px; height: 38px; flex-basis: 38px; }
  #masthead h1 { font-size: 18px; }
  .workflow { border-radius: 12px; }
  #workflow-tabs .tab-container[role="tablist"] { gap: 4px !important; padding: 5px !important; border-radius: 12px !important; }
  #workflow-tabs button[role="tab"] { gap: 3px !important; min-height: 46px !important; padding: 0 3px !important; }
  #workflow-tabs button[role="tab"]::before { width: 20px; height: 20px; flex-basis: 20px; border-radius: 6px; font-size: 7px; }
  #workflow-tabs .tab-container[role="tablist"] { min-height: 62px !important; }
  #workflow-tabs > .tab-wrapper { min-height: 62px !important; }
  #workflow-tabs button[role="tab"] { font-size: 8px !important; }
  .section-heading { gap: 10px; }
  .review-empty { min-height: 220px; }
  .mission-hero { padding: 22px 18px; }
  .pitch-hero .mission-copy h2 { font-size: 26px; }
  .mission-copy h2 { font-size: 21px; }
  .mission-tags { gap: 6px; }
  .mission-tags span { width: 100%; }
  .metric-grid, .preflight-check ul { grid-template-columns: 1fr; }
  .page-title { align-items: flex-start; padding: 18px; }
  .page-title > span { width: 34px; height: 34px; flex-basis: 34px; }
  .comparison-shell, .delivery-dashboard { padding: 16px; }
  .workspace-cover { padding: 20px 18px; }
  .workspace-cover h2 { font-size: 24px; }
  .workspace-meta { grid-template-columns: 1fr; }
  .workspace-meta > div, .workspace-meta > div + div { padding: 11px 0; border-top: 1px solid #e5e9ee; border-left: 0; }
  .workspace-meta > div:first-child { border-top: 0; }
  .review-alignment { padding: 13px 14px; }
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


def identify_rule_document(rule_source: str | None):
    if not rule_source:
        raise gr.Error("请先上传期刊规则、格式指南、模板说明或参考论文。")
    try:
        analysis = analyze_rule_document(rule_source)
    except (ValueError, RuntimeError) as exc:
        raise gr.Error(str(exc)) from exc
    rows = analysis_to_rows(analysis)
    return render_analysis_markdown(analysis), rows, analysis, False


def _reset_rule_document_analysis():
    return (
        "上传文件后点击“AI 识别规则”，系统会先判断它是官方规则、官方模板还是示例论文。",
        [],
        None,
        False,
    )


def _output_root(output_path: str | None) -> Path:
    output_path = output_path or ""
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


def _cancel_for_new_source():
    global _RUN_VERSION
    cancelled = cancel_active_compilations()
    with _RUN_LOCK:
        _RUN_VERSION += 1
    message = "" if not cancelled else "已结束上一份论文的编译进程，准备处理新文件。"
    return (
        message,
        None,
        None,
        None,
        None,
        gr.update(value=None, visible=False),
        gr.update(value=_reviewer_html(None)),
        None,
        _metric_dashboard_html(),
        _capability_feedback_html("等待任务", "执行处理任务后显示结构审计结果"),
        _capability_feedback_html("等待任务", "上传素材包后可生成映射证据"),
        _capability_feedback_html("等待任务", "上传 BibTeX 后执行可审计文献匹配"),
        _capability_feedback_html("等待任务", "处理完成后可再次复核修复结果"),
        _initial_comparison_html(),
        "<ol class='activity-log empty-log'><li><span>等待任务</span><p>执行处理任务后，这里会记录结构审计、引用治理、素材编排、规则修复和预览编译。</p></li></ol>",
        _delivery_dashboard_html(),
        "",
    )


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
    if ok and "Built-in PDF fallback:" in compile_output:
        note = "完整 LaTeX 运行库不可用，已使用无需联网的本地兼容渲染器生成 PDF。"
    elif ok and "Preview fallback:" in compile_output:
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


def _metric_dashboard_html(
    project_name: str = "等待导入论文",
    task_status: str = "尚未开始",
    repairs: int = 0,
    citations: int = 0,
    assets: int = 0,
    export_status: str = "等待预览",
) -> str:
    status_class = "ready" if task_status != "尚未开始" else "idle"
    return f"""
    <section class="overview-live">
      <div class="overview-live-head">
        <div><span>当前任务</span><strong>{escape(project_name)}</strong></div>
        <span class="task-state {status_class}">{escape(task_status)}</span>
      </div>
      <div class="metric-grid">
        <article class="metric-card metric-blue"><span class="metric-icon">FX</span><div><small>版式规范修复</small><strong>{repairs}</strong><p>项规则已自动处理</p></div></article>
        <article class="metric-card metric-indigo"><span class="metric-icon">CITE</span><div><small>引用映射成功</small><strong>{citations}</strong><p>条文献标记已关联</p></div></article>
        <article class="metric-card metric-green"><span class="metric-icon">FIG</span><div><small>图表插入完成</small><strong>{assets}</strong><p>个素材已精确匹配</p></div></article>
        <article class="metric-card metric-gold"><span class="metric-icon">OUT</span><div><small>交付状态</small><strong class="metric-text">{escape(export_status)}</strong><p>PDF / DOCX / LaTeX</p></div></article>
      </div>
    </section>
    """


def _capability_feedback_html(status: str, detail: str, tone: str = "idle") -> str:
    return (
        f"<div class='capability-feedback {tone}'>"
        f"<span>{escape(status)}</span><p>{escape(detail)}</p></div>"
    )


def _initial_comparison_html() -> str:
    return """
    <section class="comparison-shell empty-comparison">
      <div class="compare-score"><span>规范度评分</span><strong>--</strong><small>执行处理任务后生成质量审计结果</small></div>
      <div class="compare-columns">
        <article><header><span class="compare-dot before"></span><strong>处理前</strong></header><p>等待分析原始稿件的问题与风险。</p></article>
        <article><header><span class="compare-dot after"></span><strong>处理后</strong></header><p>等待生成修复后的问题列表与页面预览。</p></article>
      </div>
      <div class="preview-compare preview-empty">
        <article><header>原稿页面快照</header><div class="paper-snapshot"><span></span><span></span><span></span><span></span></div></article>
        <div class="preview-arrow">→</div>
        <article><header>修订后页面快照</header><div class="paper-snapshot repaired"><span></span><span></span><span></span><span></span></div></article>
      </div>
    </section>
    """


def _comparison_html(analysis_before, analysis_after, risk_before, risk_after, citations: int) -> str:
    def issue_list(issues, empty_text: str) -> str:
        if not issues:
            return f"<li class='empty-item'>{escape(empty_text)}</li>"
        rows = []
        for issue in issues[:6]:
            severity = escape(issue.severity)
            rows.append(
                f"<li><span class='severity {severity}'>{severity}</span>"
                f"<p>{escape(issue.message)}</p></li>"
            )
        if len(issues) > 6:
            rows.append(f"<li class='more-item'>另有 {len(issues) - 6} 项，详见格式报告</li>")
        return "".join(rows)

    delta = risk_after.overall_score - risk_before.overall_score
    delta_text = f"+{delta}" if delta >= 0 else str(delta)
    return f"""
    <section class="comparison-shell">
      <div class="compare-score">
        <span>规范度评分</span>
        <strong>{risk_before.overall_score}<i>→</i>{risk_after.overall_score}</strong>
        <small>{delta_text} 分 · 引用映射 {citations} 条</small>
      </div>
      <div class="compare-columns">
        <article class="compare-before">
          <header><span class="compare-dot before"></span><strong>处理前问题</strong><em>{len(analysis_before.issues)} 项</em></header>
          <ul>{issue_list(analysis_before.issues, "原始稿件未发现规则问题")}</ul>
        </article>
        <article class="compare-after">
          <header><span class="compare-dot after"></span><strong>处理后问题</strong><em>{len(analysis_after.issues)} 项</em></header>
          <ul>{issue_list(analysis_after.issues, "已通过当前规则检查")}</ul>
        </article>
      </div>
      <div class="preview-compare">
        <article>
          <header><span>原稿版式快照</span><em>{len(analysis_before.issues)} 项待处理</em></header>
          <div class="paper-snapshot original"><b></b><span></span><span></span><i></i><span></span><span></span></div>
        </article>
        <div class="preview-arrow">→</div>
        <article>
          <header><span>修订后版式快照</span><em>{len(analysis_after.issues)} 项待确认</em></header>
          <div class="paper-snapshot repaired"><b></b><span></span><span></span><i></i><span></span><span></span></div>
        </article>
      </div>
    </section>
    """


def _activity_log_html(
    actions: list[RepairAction],
    compile_status: str,
    citations: int,
    assets: int,
    formulas: int = 0,
    agent_status: str = "running",
) -> str:
    agent_labels = {
        "running": "执行中",
        "needs_confirmation": "等待确认",
        "ready_for_review": "等待审阅",
        "verification_failed": "验证失败",
        "delivered": "已交付",
    }
    items = [
        ("文档结构", f"完成结构解析与章节规则检查，共执行 {len(actions)} 项自动修复"),
        ("参考文献", f"完成 {citations} 条引用映射"),
        ("科研素材", f"完成 {assets} 个图表占位符、{formulas} 个公式占位符匹配"),
        ("预览编译", f"PDF 编译状态：{compile_status}"),
        ("编排决策", f"审计轨迹状态：{agent_labels.get(agent_status, agent_status)}"),
    ]
    action_rows = "".join(
        f"<li><span>{escape(title)}</span><p>{escape(detail)}</p></li>" for title, detail in items
    )
    repair_rows = "".join(
        f"<li><span>自动修复</span><p>{escape(action.description)}</p></li>" for action in actions[:5]
    )
    return f"<ol class='activity-log'>{action_rows}{repair_rows}</ol>"


def _delivery_dashboard_html(run_dir: Path | None = None, exported: bool = False) -> str:
    blockers = _load_delivery_gate(run_dir).get("blockers", []) if run_dir else []
    agent = PaperDeliveryAgent.load(run_dir) if run_dir else None
    agent_status = agent.trace.get("status", "running") if agent else "not_started"
    agent_status_label = {
        "not_started": "待启动",
        "running": "执行中",
        "needs_confirmation": "等待确认",
        "ready_for_review": "等待审阅",
        "verification_failed": "验证失败",
        "delivered": "已交付",
    }.get(agent_status, agent_status)
    status = "交付文件已生成" if exported else ("存在待确认项" if blockers else "等待正式导出")
    tone = "success" if exported else ("warning" if blockers else "idle")
    output = str(run_dir) if run_dir else "将在任务启动后确定"
    analyzed = run_dir is not None
    risk_value = f"{len(blockers)} 项" if analyzed else "待分析"
    risk_caption = "当前无阻断项" if analyzed and not blockers else ("需确认后才可正式导出" if blockers else "完成处理任务后生成")
    checklist = [
        ("摘要格式", analyzed and not blockers),
        ("图表编号连续", analyzed and not any("placeholder" in str(item).lower() for item in blockers)),
        ("参考文献完整", analyzed and not any("citation" in str(item).lower() for item in blockers)),
        ("智能编排留痕", bool(agent)),
        ("Word / PDF 一致性", exported),
    ]
    checks = "".join(
        f"<li class='{'pass' if ok else 'pending'}'><span>{'✓' if ok else '·'}</span>{escape(label)}</li>"
        for label, ok in checklist
    )
    return f"""
    <section class="delivery-dashboard">
      <div class="delivery-status-line"><div><span>正式交付状态</span><strong>{escape(status)}</strong></div><em class="{tone}">{'READY' if exported else 'PRECHECK'}</em></div>
      <div class="delivery-overview-grid">
        <article><small>交付格式</small><strong>PDF · DOCX · LaTeX</strong><p>三种格式统一生成</p></article>
        <article><small>输出目录</small><strong class="path-value">{escape(output)}</strong><p>本地可追溯交付目录</p></article>
        <article><small>风险提醒</small><strong>{escape(risk_value)}</strong><p>{escape(risk_caption)} · 轨迹 {escape(agent_status_label)}</p></article>
      </div>
      <div class="preflight-check"><strong>提交前确认</strong><ul>{checks}</ul></div>
    </section>
    """


def _require_run(run_directory: str) -> Path:
    if not run_directory:
        raise gr.Error("请先在项目中心执行处理任务。")
    run_dir = Path(run_directory)
    if not (run_dir / "source.tex").exists():
        raise gr.Error("当前任务工程不存在，请重新启动处理。")
    return run_dir


def inspect_structure(run_directory: str) -> str:
    run_dir = _require_run(run_directory)
    rules = _load_run_config(run_dir).get("resolved_rules", default_rules())
    analysis = analyze(read_text_best_effort(run_dir / "source.tex")[0], rules)
    risk = assess_risk(analysis)
    tone = "success" if not analysis.issues else "warning"
    return _capability_feedback_html("检查完成", f"剩余 {len(analysis.issues)} 项问题，当前规范度 {risk.overall_score}/100", tone)


def inspect_assets(run_directory: str) -> str:
    run_dir = _require_run(run_directory)
    gate = _load_delivery_gate(run_dir)
    report = run_dir / "asset_mapping_report.md"
    tone = "success" if report.exists() and not gate.get("blockers") else "warning"
    detail = "素材映射报告已生成" if report.exists() else "未上传图表素材包，当前仅检查占位符"
    return _capability_feedback_html("素材检查完成", f"{detail}；待确认 {len(gate.get('blockers', []))} 项", tone)


def inspect_citations(run_directory: str) -> str:
    run_dir = _require_run(run_directory)
    report = run_dir / "citation_mapping.md"
    count = report.read_text(encoding="utf-8").count(" -> `") if report.exists() else 0
    tone = "success" if report.exists() else "warning"
    return _capability_feedback_html("文献检查完成", f"已识别 {count} 条引用映射" if report.exists() else "尚未上传 BibTeX 文献库", tone)


def inspect_repairs(run_directory: str) -> str:
    run_dir = _require_run(run_directory)
    rules = _load_run_config(run_dir).get("resolved_rules", default_rules())
    analysis = analyze(read_text_best_effort(run_dir / "source.tex")[0], rules)
    tone = "success" if not analysis.issues else "warning"
    return _capability_feedback_html("复核完成", f"自动修复结果已保存，仍有 {len(analysis.issues)} 项需要人工确认", tone)


def run_agent(
    uploaded_file: str | None,
    local_path: str,
    rule_file: str,
    target_name: str,
    journal_profile: str,
    requirement_text: str,
    target_guide: str | None,
    reference_article: str | None,
    llm_rule_analysis: dict | None,
    llm_rule_rows,
    llm_rules_confirmed: bool,
    bibliography_file: str | None,
    initial_asset_bundle: str | None,
    initial_annotation_bundle: str | None,
    formula_bundle: str | None,
    output_path: str,
    compile_pdf: bool,
):
    local_path = local_path or ""
    rule_file = rule_file or RULE_NONE
    target_name = target_name or ""
    journal_profile = journal_profile or RULE_NONE
    requirement_text = requirement_text or ""
    output_path = output_path or str(OUTPUT_DIR)
    selected_rule_rows = llm_rule_rows.values.tolist() if hasattr(llm_rule_rows, "values") else (llm_rule_rows or [])
    run_version = _start_run()
    source = _source_path(uploaded_file, local_path)
    destination_root = _output_root(output_path)
    run_dir = Path(tempfile.mkdtemp(prefix="paperformat_", dir=destination_root))
    agent = PaperDeliveryAgent(run_dir, source.name, target_name.strip())

    rules = apply_journal_profile(_base_rules(rule_file), _profile_id(journal_profile))
    rules, reference_changes = apply_reference_article_style(rules, reference_article)
    document_type = (llm_rule_analysis or {}).get("document_type", "unknown")
    ai_rule_changes: list[str] = []
    guideline_changes: list[str] = []
    if llm_rules_confirmed and llm_rule_analysis:
        if document_type == "sample_article":
            rules, ai_rule_changes = apply_selected_rule_rows(rules, selected_rule_rows)
            rules, text_requirement_changes = apply_requirement_text(rules, requirement_text)
        else:
            rules, text_requirement_changes = apply_requirement_text(rules, requirement_text)
            rules, ai_rule_changes = apply_selected_rule_rows(rules, selected_rule_rows)
    else:
        rules, text_requirement_changes = apply_requirement_text(rules, requirement_text)
        rules, guideline_changes = apply_guideline_overrides(rules, target_guide)

    if target_guide:
        guide = Path(target_guide)
        shutil.copy2(guide, run_dir / f"target_guide{guide.suffix.lower()}")
    if llm_rule_analysis:
        (run_dir / "ai_rule_analysis.json").write_text(
            json.dumps(llm_rule_analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (run_dir / "ai_rule_selection.json").write_text(
            json.dumps(
                {
                    "confirmed": bool(llm_rules_confirmed),
                    "document_type": document_type,
                    "selected_rows": selected_rule_rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if reference_article:
        reference = Path(reference_article)
        shutil.copy2(reference, run_dir / f"reference_article{reference.suffix.lower()}")

    try:
        project = prepare_project(source, run_dir, rules)
        original_text, _ = read_text_best_effort(project.main_tex_path)
    except ValueError as exc:
        agent.block_step("intake", str(exc))
        raise gr.Error(str(exc)) from exc
    agent.complete_step(
        "intake",
        f"Parsed {project.source_kind} source into an isolated LaTeX workspace.",
        [str(project.main_tex_path.relative_to(run_dir))],
    )

    rules = rules_for_source_kind(rules, project.source_kind)
    agent.complete_step(
        "rules",
        f"Resolved '{rules['name']}' with {len(ai_rule_changes + guideline_changes + text_requirement_changes + reference_changes)} supplemental evidence item(s).",
        ["ai_rule_analysis.json"] if llm_rule_analysis else [],
    )

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
    agent.complete_step(
        "repair",
        f"Analyzed {len(analysis_before.issues)} issue(s) and applied {len(actions)} reversible formatting repair(s).",
    )
    asset_summary: list[str] = []
    asset_matches = 0
    formula_matches = 0
    delivery_blockers: list[str] = list(project.source_notes)
    try:
        formulas = load_formulas(formula_bundle, run_dir)
    except (ValueError, json.JSONDecodeError) as exc:
        agent.block_step("assets", str(exc))
        raise gr.Error(str(exc)) from exc
    repaired_text, matched_formulas, missing_formulas = apply_formulas(repaired_text, formulas)
    formula_matches = len(matched_formulas)
    write_formula_manifest(formulas, project.project_dir)
    delivery_blockers.extend(formulas.warnings)
    delivery_blockers.extend(missing_formulas)
    if matched_formulas:
        actions.append(RepairAction("formula_mapping", f"Inserted {len(matched_formulas)} confirmed formula mappings."))
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
        asset_matches = len(matched)
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
        agent.complete_step(
            "assets",
            f"Matched {len(matched)} figure/table asset(s) and {formula_matches} confirmed formula(s).",
            missing + duplicate + annotations.warnings + formulas.warnings + missing_formulas,
        )
    else:
        unresolved_markers = [marker for marker, _ in find_placeholders(repaired_text)]
        if unresolved_markers:
            delivery_blockers.append("Unresolved manuscript placeholders: " + ", ".join(unresolved_markers))
        agent.complete_step(
            "assets",
            f"Scanned manuscript placeholders and matched {formula_matches} confirmed formula(s).",
            unresolved_markers + formulas.warnings + missing_formulas,
        )
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
    compile_status, compile_note, pdf_path = "未编译", None, None
    compile_log_path = run_dir / "compile.log"
    if compile_pdf:
        if project.source_kind == "pdf" and project.source_notes:
            compile_status = "原始 PDF 保真预览"
            pdf_path = str(source)
            compile_note = "检测到公式区域或无法可靠还原的表格。为避免错误重排，当前保留原始 PDF 预览；正式交付前请提供 DOCX、LaTeX 或表格源文件。"
            agent.complete_step("verify", "Preserved the original PDF because conversion risks were detected.", project.source_notes)
        else:
            ok, compile_output = compile_tex(project.main_tex_path, run_dir)
            preview_used = "Preview fallback:" in compile_output or "Built-in PDF fallback:" in compile_output
            compile_status = "预览版" if ok and preview_used else "成功" if ok else "失败"
            write_text_with_encoding(compile_log_path, compile_output)
            candidate_pdf = _compiled_pdf_for(project.main_tex_path, run_dir)
            pdf_path = str(candidate_pdf) if candidate_pdf else None
            if "Built-in PDF fallback:" in compile_output:
                compile_note = "完整 LaTeX 运行库不可用，已生成无需联网的本地兼容 PDF。"
            elif preview_used:
                compile_note = "期刊正式模板未在本地完整通过，当前 PDF 是安全预览版。"
            elif not ok:
                compile_note = explain_compile_failure(compile_output)
            if ok:
                agent.complete_step("verify", f"LaTeX compilation {compile_status}.", [compile_log_path.name])
            else:
                agent.block_step("verify", "LaTeX compilation failed.", [compile_log_path.name])
    else:
        agent.complete_step("verify", "Preview compilation was not requested for this run.")

    agent.finish(delivery_blockers, compile_status)
    shutil.copy2(agent.persist(), project.project_dir / agent.trace_path.name)
    package_project(project.project_dir, run_dir / "latex_source.zip")

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
            f"- AI 规则识别：`{document_type if llm_rule_analysis else '未启用'}；{'已确认' if llm_rules_confirmed else '未确认'}`",
            f"- AI 已采用规则：`{', '.join(ai_rule_changes) if ai_rule_changes else '无'}`",
            f"- 额外格式要求：`{', '.join(guideline_changes + text_requirement_changes) or '未检测到'}`",
            f"- 参考论文风格：`{', '.join(reference_changes) if reference_changes else '未检测到'}`",
            f"- 参考文献：`{bibliography_name + '.bib' if bibliography_name else '未提供'}`",
            f"- 项目图表包：`{'；'.join(asset_summary) if asset_summary else '未上传'}`",
            f"- 公式合集：`{formula_matches} 条已插入，{len(missing_formulas) + len(formulas.warnings)} 条待确认`",
            f"- 正式交付状态：`{'已阻止，需处理 ' + str(len(delivery_blockers)) + ' 项' if delivery_blockers else '可进入正式导出检查'}`",
            f"- 数字引用映射：`{len(citation_mapping)} 条已转换，{len(unresolved_citations)} 条未匹配`" if bibliography_name else "- 数字引用映射：`未启用（请上传 BibTeX 文献库）`",
            f"- 格式评分：`{risk_before.overall_score}/100 -> {risk_after.overall_score}/100`",
            f"- 自动修复数量：`{len(actions)}`",
            f"- PDF 编译：`{compile_status}`",
            f"- 智能编排决策：`{agent.trace['status']}`",
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
    overview_metrics = _metric_dashboard_html(
        source.name,
        "预览处理完成",
        len(actions),
        len(citation_mapping),
        asset_matches,
        "预览已生成" if pdf_path else compile_status,
    )
    structure_status = _capability_feedback_html(
        "结构检查完成",
        f"处理前 {len(analysis_before.issues)} 项，处理后 {len(analysis_after.issues)} 项",
        "success" if not analysis_after.issues else "warning",
    )
    asset_status = _capability_feedback_html(
        "素材处理完成",
        f"已匹配 {asset_matches} 个图表素材，待确认 {len(delivery_blockers)} 项",
        "success" if not delivery_blockers else "warning",
    )
    citation_status = _capability_feedback_html(
        "文献映射完成" if bibliography_name else "等待文献库",
        f"已映射 {len(citation_mapping)} 条，未匹配 {len(unresolved_citations)} 条" if bibliography_name else "上传 BibTeX 后可建立可审计引用映射",
        "success" if bibliography_name and not unresolved_citations else "warning",
    )
    repair_status = _capability_feedback_html(
        "自动修复完成",
        f"执行 {len(actions)} 项确定性修复，规范度 {risk_before.overall_score} → {risk_after.overall_score}",
        "success",
    )
    comparison = _comparison_html(analysis_before, analysis_after, risk_before, risk_after, len(citation_mapping))
    activity = _activity_log_html(actions, compile_status, len(citation_mapping), asset_matches, formula_matches, agent.trace["status"])
    delivery_dashboard = _delivery_dashboard_html(run_dir, exported=False)
    return (
        summary,
        None,
        None,
        str(report_path),
        str(compile_log_path) if compile_log_path.exists() else None,
        pdf_output,
        None,
        review_html,
        str(run_dir),
        overview_metrics,
        structure_status,
        asset_status,
        citation_status,
        repair_status,
        comparison,
        activity,
        delivery_dashboard,
    )


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

    status = "成功" if ok else "失败"
    if ok and "Built-in PDF fallback:" in compile_output:
        status = "本地兼容版"
    elif ok and "Preview fallback:" in compile_output:
        status = "预览替代版"
    notes = [f"- PDF 编译：`{status}`", f"- Word 导出：`{'成功' if word_ok else '失败'}`", f"- 输出目录：`{run_dir}`"]
    if not ok:
        notes.append(f"- 编译说明：{explain_compile_failure(compile_output)}")
    notes.append(f"- Word 说明：{word_note}")
    agent = PaperDeliveryAgent.load(run_dir)
    if agent:
        agent.mark_formal_export(ok and word_ok, [status, word_note])
        shutil.copy2(agent.persist(), run_dir / "project" / agent.trace_path.name)
    project_zip = package_project(run_dir / "project", run_dir / "formal_latex_source.zip")
    completion_heading = "正式导出完成" if ok and word_ok else "正式导出部分完成"
    summary = f"## {completion_heading}\n\n" + "\n".join(notes)
    return (
        summary,
        gr.update(value=str(source_tex), visible=True),
        gr.update(value=str(project_zip), visible=True),
        str(log_path),
        gr.update(value=pdf_path, visible=bool(pdf_path)),
        gr.update(value=str(docx_path) if word_ok and docx_path.exists() else None, visible=bool(word_ok and docx_path.exists())),
        _reviewer_html(pdf_path),
        _delivery_dashboard_html(run_dir, exported=bool(ok and word_ok)),
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


def run_smart_insert(
    run_directory: str,
    kind: str,
    content: str,
    link_url: str,
    section: str,
    placement: str,
    anchor: str,
):
    """Expose safe formula and hyperlink insertion as a focused enterprise workflow."""
    kind = kind or "Formula"
    content = content or ""
    link_url = link_url or ""
    section = section or ""
    placement = placement or "Section end"
    anchor = anchor or ""
    result = run_hybrid_insert(
        run_directory,
        kind,
        content,
        None,
        "",
        link_url,
        section,
        placement,
        anchor,
        STYLE_CURRENT,
        RULE_NONE,
        RULE_NONE,
        "",
        "",
        "",
    )
    pdf_update = result[3] if isinstance(result[3], dict) else {}
    pdf_path = pdf_update.get("value")
    label = "公式" if kind == "Formula" else "超链接"
    return (
        result[0],
        _reviewer_html(pdf_path),
        _capability_feedback_html(f"{label}插入完成", "已写入当前 LaTeX 工程并重新生成本地 PDF 预览", "success"),
    )


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
    previous_gate = _load_delivery_gate(run_dir)
    asset_tokens = ("placeholder", "素材", "图表注", "未找到匹配", "duplicate", "忽略文件")
    persistent_blockers = [
        str(item) for item in previous_gate.get("blockers", [])
        if not any(token in str(item).lower() for token in asset_tokens)
    ]
    delivery_blockers = [*persistent_blockers, *missing, *duplicate, *annotations.warnings]
    unresolved_markers = [marker for marker, _ in find_placeholders(updated)]
    if unresolved_markers:
        delivery_blockers.append("Unresolved manuscript placeholders: " + ", ".join(unresolved_markers))
    _write_delivery_gate(run_dir, delivery_blockers, notices=previous_gate.get("notices", []))
    pdf_path, note = _compile_after_update(main_tex, run_dir, "placeholder_compile.log")
    agent = PaperDeliveryAgent.load(run_dir)
    if agent:
        agent.complete_step("assets", f"Rechecked figure/table assets; {len(delivery_blockers)} blocking item(s) remain.", delivery_blockers)
        if pdf_path:
            agent.complete_step("verify", "Rebuilt the preview after asset insertion.", ["placeholder_compile.log"])
        else:
            agent.block_step("verify", "Preview rebuild failed after asset insertion.", ["placeholder_compile.log"])
        agent.finish(delivery_blockers, "成功" if pdf_path else "失败")
        shutil.copy2(agent.persist(), run_dir / "project" / agent.trace_path.name)
    project_zip = package_project(run_dir / "project", run_dir / "placeholder_revised_source.zip")

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


def run_competition_demo(output_path: str):
    """Run a bundled, reproducible case for a stable three-minute live demo."""
    manuscript = BASE_DIR / "samples" / "case_focus_study.md"
    bibliography = BASE_DIR / "samples" / "case_focus_references.bib"
    if not manuscript.exists() or not bibliography.exists():
        raise gr.Error("内置演示案例不完整，请检查 samples 目录。")
    return run_agent(
        None,
        str(manuscript),
        "thesis_cn_basic.json",
        "研究生学术论文规范化交付",
        RULE_NONE,
        "A4 版式，页边距 2.5cm，1.5 倍行距；中文图题使用“图”，表题使用“表”。",
        None,
        None,
        None,
        [],
        False,
        str(bibliography),
        None,
        None,
        None,
        output_path or str(OUTPUT_DIR),
        True,
    )


def build_demo() -> gr.Blocks:
    rule_choices = _rule_dropdown_choices()
    profile_choices_ui = _profile_dropdown_choices()
    with gr.Blocks(title="研文智规") as demo:
        gr.HTML(
            """
            <section id="masthead">
              <div class="brand-lockup">
                <div class="brand-mark" aria-hidden="true">SF</div>
                <div class="brand-copy">
                  <p class="brand-eyebrow">SCHOLARFLOW ENTERPRISE</p>
                  <h1>研文智规</h1>
                  <p class="brand-description">科研文稿规范与质量治理系统</p>
                </div>
              </div>
              <div class="trust-row" aria-label="系统特性">
                <span class="header-meta">私有化部署</span>
                <span class="header-meta">Enterprise 1.0</span>
                <span class="system-status">服务正常</span>
              </div>
            </section>
            """
        )

        with gr.Tabs(selected="overview", elem_id="workflow-tabs"):
            with gr.Tab("项目中心", id="overview"):
                gr.HTML(
                    """
                    <section class="compact-overview">
                      <div>
                        <span>科研成果交付工作台</span>
                        <h2>从论文稿件到合规交付</h2>
                        <p>检查结构、版式、引用和素材，生成可复核的 PDF、DOCX 与 LaTeX 交付文件。</p>
                      </div>
                      <ul>
                        <li><small>输入</small><strong>DOCX / PDF / MD / TEX / ZIP</strong></li>
                        <li><small>处理</small><strong>本地运行 · 全程留痕</strong></li>
                        <li><small>输出</small><strong>PDF / DOCX / LaTeX</strong></li>
                      </ul>
                    </section>
                    """
                )
                overview_metrics = gr.HTML(value=_metric_dashboard_html())
                with gr.Row(elem_classes=["demo-launch", "formal-demo-launch"]):
                    gr.HTML('<div class="demo-launch-copy"><h3>没有稿件？运行内置标准案例</h3><p>真实执行检查、修复、引用映射和 PDF 预览。</p></div>', scale=4)
                    demo_button = gr.Button("运行标准案例", elem_id="demo-run", scale=1)
                with gr.Group(elem_id="stage-input", elem_classes=["panel", "launch-panel"]):
                    gr.HTML(
                        """
                        <header class="section-heading">
                          <span class="section-kicker">01</span>
                          <div><h2>新建交付任务</h2><p>上传稿件，按需配置规则与附加材料。</p></div>
                        </header>
                        <div class="field-intro">
                          <strong>论文主稿 <span class="required-badge">必需</span></strong>
                          <span>上传文件与本地路径任选其一，原稿正文、数据和结论不会被改写。</span>
                        </div>
                        """
                    )
                    with gr.Row():
                        uploaded = gr.File(
                            label="上传论文文件",
                            type="filepath",
                            file_types=[".docx", ".pdf", ".md", ".markdown", ".tex", ".zip"],
                            elem_id="source-upload",
                            scale=3,
                        )
                        with gr.Column(scale=2, elem_classes=["source-sidecar"]):
                            gr.HTML('<div class="sidecar-head"><span class="sidecar-icon">PATH</span><div><strong>本地文件模式</strong><small>适合大文件或固定工作目录</small></div></div>')
                            local_path = gr.Textbox(label="本地文件路径", placeholder=r"D:\Documents\my-paper.docx", elem_id="source-path")
                            gr.HTML('<div class="format-support"><span>支持格式</span><div><code>DOCX</code><code>PDF</code><code>MD</code><code>TEX</code><code>ZIP</code></div><small>扫描型 PDF 请先完成 OCR。</small></div>')

                    with gr.Accordion("任务规则与目标期刊", open=False, elem_classes=["advanced", "task-config"]):
                        gr.HTML('<div class="rule-priority"><span>规则合并优先级</span><strong>官方指南</strong><i>›</i><strong>补充要求</strong><i>›</i><strong>期刊规则包</strong><i>›</i><strong>基础规则</strong></div>')
                        with gr.Row(elem_classes=["match-row"]):
                            target_name = gr.Textbox(label="期刊或学校名称", placeholder="例如：IEEE Transactions on ...", scale=5)
                            match_button = gr.Button("匹配期刊", elem_id="match", scale=1)
                        with gr.Row():
                            journal_profile = gr.Dropdown(label="期刊规则包", choices=profile_choices_ui, value=RULE_NONE)
                            rule_file = gr.Dropdown(label="基础格式规则", choices=rule_choices, value=RULE_NONE)
                        journal_match = gr.Markdown("", visible=False)
                        requirement_text = gr.Textbox(label="补充排版要求", lines=3, placeholder="例如：A4、页边距 2.5cm、1.5 倍行距、参考文献使用 IEEEtran")
                        with gr.Accordion("查看当前规则摘要", open=False, elem_classes=["advanced"]):
                            rule_summary = gr.Markdown(value=_rule_summary(RULE_NONE))

                    with gr.Accordion("期刊规则识别与附加材料", open=False, elem_classes=["advanced", "task-config"]):
                        with gr.Row():
                            initial_asset_bundle = gr.File(label="图表素材 ZIP", type="filepath", file_types=[".zip"], elem_id="asset-bundle")
                            initial_annotation_bundle = gr.File(label="图表题注 XLSX / ZIP", type="filepath", file_types=[".xlsx", ".zip"], elem_id="annotation-bundle")
                            formula_bundle = gr.File(label="公式合集 JSON / ZIP", type="filepath", file_types=[".json", ".zip"], elem_id="formula-bundle")
                        with gr.Row(elem_classes=["template-actions"]):
                            gr.DownloadButton("下载题注模板", value=str(ANNOTATION_TEMPLATE) if ANNOTATION_TEMPLATE.exists() else None, interactive=ANNOTATION_TEMPLATE.exists())
                            gr.DownloadButton("下载公式模板", value=str(FORMULA_TEMPLATE) if FORMULA_TEMPLATE.exists() else None, interactive=FORMULA_TEMPLATE.exists())
                        with gr.Row():
                            target_guide = gr.File(label="期刊规则 / 格式指南 / 示例论文（AI 识别）", type="filepath", file_types=[".pdf", ".docx", ".md", ".markdown", ".txt"], elem_id="guide-upload")
                            reference_article = gr.File(label="附加参考论文（可选）", type="filepath", file_types=[".pdf", ".docx"], elem_id="reference-upload")
                            bibliography_file = gr.File(label="BibTeX 文献库", type="filepath", file_types=[".bib"], elem_id="bib-upload")
                        with gr.Group(elem_classes=["ai-rule-panel"]):
                            gr.HTML('<div class="ai-rule-head"><div><span>DEEPSEEK RULE INTELLIGENCE</span><h3>识别格式规则</h3><p>先判断文件性质，再提取带原文证据的候选规则；确认后才会交给 Python 执行。</p></div></div>')
                            with gr.Row(elem_classes=["ai-rule-actions"]):
                                identify_rule_button = gr.Button("AI 识别规则", variant="secondary", elem_id="identify-rules", scale=1)
                                llm_rules_confirmed = gr.Checkbox(label="确认采用表格中已勾选的候选规则", value=False, scale=3)
                            llm_rule_review = gr.Markdown("上传文件后点击“AI 识别规则”，系统会先判断它是官方规则、官方模板还是示例论文。")
                            llm_rule_rows = gr.Dataframe(
                                headers=["采用", "规则", "字段", "识别值", "依据", "置信度", "原文证据"],
                                datatype=["bool", "str", "str", "str", "str", "number", "str"],
                                type="array",
                                interactive=True,
                                wrap=True,
                                column_widths=[65, 140, 190, 150, 90, 80, 420],
                                max_height=360,
                                elem_id="ai-rule-table",
                            )
                            llm_rule_analysis = gr.State()

                    with gr.Group(elem_id="stage-assets", elem_classes=["panel", "launch-action"]):
                        gr.HTML('<div class="generate-copy"><h3>执行智能编排</h3><p>选择输出目录后开始检查、修复并生成预览。</p></div>')
                        with gr.Row(elem_classes=["generate-layout"]):
                            output_path = gr.Textbox(label="输出目录", value=str(OUTPUT_DIR), placeholder=r"D:\PaperOutput", scale=5)
                            run_button = gr.Button("执行处理任务", variant="primary", elem_id="run", scale=1)
                    run_state = gr.State()

            with gr.Tab("智能编排", id="processing"):
                gr.HTML('<header class="page-title"><span>02</span><div><h2>智能编排</h2><p>四项核心能力，可独立执行与复核。</p></div></header>')
                with gr.Row(elem_classes=["capability-row"]):
                    with gr.Group(elem_classes=["capability-card", "tone-green"]):
                        gr.HTML('<div class="capability-head"><span>STRUCTURE</span><em>结构</em></div><h3>结构规范审计</h3><p>检查章节层级、摘要、关键词、图表题注和必要组成项。</p>')
                        structure_status = gr.HTML(value=_capability_feedback_html("等待任务", "执行处理任务后显示结构审计结果"))
                        structure_button = gr.Button("重新检查结构", elem_classes=["capability-action"])
                    with gr.Group(elem_classes=["capability-card", "tone-gold"]):
                        gr.HTML('<div class="capability-head"><span>ASSETS</span><em>素材</em></div><h3>科研素材编排</h3><p>按占位符匹配图表、公式和链接，并检查题注与资源完整性。</p>')
                        asset_status = gr.HTML(value=_capability_feedback_html("等待任务", "上传素材包后可生成映射证据"))
                        asset_button = gr.Button("检查素材映射", elem_classes=["capability-action"])
                with gr.Row(elem_classes=["capability-row"]):
                    with gr.Group(elem_classes=["capability-card", "tone-blue"]):
                        gr.HTML('<div class="capability-head"><span>CITATIONS</span><em>文献</em></div><h3>引文一致性治理</h3><p>建立正文标记与 BibTeX 条目的映射，提示缺失与未解析引用。</p>')
                        citation_status = gr.HTML(value=_capability_feedback_html("等待任务", "上传 BibTeX 后执行可审计文献匹配"))
                        citation_button = gr.Button("检查文献映射", elem_classes=["capability-action"])
                    with gr.Group(elem_classes=["capability-card", "tone-red"]):
                        gr.HTML('<div class="capability-head"><span>REPAIR</span><em>修复</em></div><h3>版式规则修复</h3><p>仅执行确定性版式修复，保留正文内容，并标记需要人工确认的风险。</p>')
                        repair_status = gr.HTML(value=_capability_feedback_html("等待任务", "处理完成后可再次复核修复结果"))
                        repair_button = gr.Button("复核修复结果", elem_classes=["capability-action"])
                with gr.Accordion("公式与超链接精准插入", open=False, elem_classes=["advanced", "insert-console", "compact-tool"]):
                    gr.HTML('<p class="compact-tool-note">按章节或文本锚点插入内容，并重建 PDF 预览。</p>')
                    with gr.Row():
                        insert_kind = gr.Dropdown(label="内容类型", choices=[("公式", "Formula"), ("超链接", "Hyperlink")], value="Formula", scale=2)
                        insert_placement = gr.Dropdown(
                            label="插入位置",
                            choices=[("章节末尾", "Section end"), ("章节开头", "Section start"), ("锚点之后", "After anchor"), ("锚点之前", "Before anchor")],
                            value="Section end",
                            scale=2,
                        )
                    insert_content = gr.Textbox(
                        label="公式源码 / 链接文本",
                        lines=3,
                        placeholder=r"公式示例：E = mc^2；链接示例：查看补充材料",
                    )
                    with gr.Row():
                        insert_url = gr.Textbox(label="链接 URL（仅超链接）", placeholder="https://example.org/source", scale=2)
                        insert_section = gr.Textbox(label="目标章节", placeholder="例如：结果与讨论", scale=1)
                        insert_anchor = gr.Textbox(label="文本锚点（可选）", placeholder="精确定位的一段原文", scale=1)
                    insert_button = gr.Button("执行插入并重建预览", elem_classes=["insert-action"])
                    insertion_summary = gr.Markdown("", elem_classes=["insertion-feedback"])

            with gr.Tab("质量审计", id="comparison"):
                gr.HTML('<header class="page-title"><span>03</span><div><h2>质量审计</h2><p>查看处理前后变化、规范评分与问题证据。</p></div></header>')
                comparison_view = gr.HTML(value=_initial_comparison_html())
                with gr.Accordion("处理明细与报告", open=False, elem_classes=["advanced", "compact-results"]):
                    summary = gr.Markdown(elem_classes=["run-summary"])
                    with gr.Row(elem_classes=["artifact-row", "evidence-row"]):
                        report_file = gr.File(label="格式检查报告")
                        compile_log = gr.File(label="编译日志")
                with gr.Accordion("操作日志", open=False, elem_classes=["advanced", "compact-results"]):
                    activity_view = gr.HTML(value="<ol class='activity-log empty-log'><li><span>等待任务</span><p>处理完成后显示操作记录。</p></li></ol>")
                with gr.Accordion("PDF 页面预览", open=False, elem_classes=["advanced", "preview-accordion", "compact-results"]):
                    reviewer = gr.HTML(value=_reviewer_html(None))

            with gr.Tab("交付中心", id="formal-delivery"):
                gr.HTML('<header class="page-title"><span>04</span><div><h2>交付中心</h2><p>执行提交前检查并生成正式交付文件。</p></div></header>')
                delivery_dashboard = gr.HTML(value=_delivery_dashboard_html())
                with gr.Group(elem_id="stage-export", elem_classes=["panel", "final-delivery", "delivery-action"]):
                    gr.HTML('<div class="delivery-head"><div><h3>生成标准交付包</h3><p>确认质量审计和提交前检查后，生成 PDF、Word 与 LaTeX 源码。</p></div></div>')
                    formal_button = gr.Button("生成交付文件", elem_id="formal")
                    with gr.Row():
                        pdf_file = gr.File(label="正式 PDF", visible=False)
                        word_file = gr.File(label="正式 Word（DOCX）", visible=False)
                    with gr.Row():
                        tex_file = gr.File(label="LaTeX 主文件", visible=False)
                        project_file = gr.File(label="LaTeX 源码包", visible=False)

        demo_button.click(
            run_competition_demo,
            inputs=output_path,
            outputs=[
                summary, tex_file, project_file, report_file, compile_log, pdf_file, word_file, reviewer, run_state,
                overview_metrics, structure_status, asset_status, citation_status, repair_status,
                comparison_view, activity_view, delivery_dashboard,
            ],
        )
        run_button.click(
            run_agent,
            inputs=[uploaded, local_path, rule_file, target_name, journal_profile, requirement_text, target_guide, reference_article, llm_rule_analysis, llm_rule_rows, llm_rules_confirmed, bibliography_file, initial_asset_bundle, initial_annotation_bundle, formula_bundle, output_path, gr.State(True)],
            outputs=[
                summary, tex_file, project_file, report_file, compile_log, pdf_file, word_file, reviewer, run_state,
                overview_metrics, structure_status, asset_status, citation_status, repair_status,
                comparison_view, activity_view, delivery_dashboard,
            ],
        )
        formal_button.click(
            run_formal_export,
            inputs=run_state,
            outputs=[summary, tex_file, project_file, compile_log, pdf_file, word_file, reviewer, delivery_dashboard],
        )
        source_reset_outputs = [
            summary, tex_file, project_file, report_file, compile_log, pdf_file, reviewer, run_state,
            overview_metrics, structure_status, asset_status, citation_status, repair_status,
            comparison_view, activity_view, delivery_dashboard, insertion_summary,
        ]
        uploaded.change(_cancel_for_new_source, outputs=source_reset_outputs, queue=False)
        local_path.change(_cancel_for_new_source, outputs=source_reset_outputs, queue=False)
        match_button.click(match_journal, inputs=target_name, outputs=[journal_profile, journal_match])
        rule_file.change(_rule_summary, inputs=rule_file, outputs=rule_summary)
        identify_rule_button.click(
            identify_rule_document,
            inputs=target_guide,
            outputs=[llm_rule_review, llm_rule_rows, llm_rule_analysis, llm_rules_confirmed],
        )
        target_guide.change(
            _reset_rule_document_analysis,
            outputs=[llm_rule_review, llm_rule_rows, llm_rule_analysis, llm_rules_confirmed],
            queue=False,
        )
        structure_button.click(inspect_structure, inputs=run_state, outputs=structure_status)
        asset_button.click(inspect_assets, inputs=run_state, outputs=asset_status)
        citation_button.click(inspect_citations, inputs=run_state, outputs=citation_status)
        repair_button.click(inspect_repairs, inputs=run_state, outputs=repair_status)
        insert_button.click(
            run_smart_insert,
            inputs=[run_state, insert_kind, insert_content, insert_url, insert_section, insert_placement, insert_anchor],
            outputs=[insertion_summary, reviewer, asset_status],
        )
    return demo


AGENT_WORKSPACE_CSS = """
:root { --agent-ink:#172033; --agent-muted:#64748b; --agent-line:#dde4ee; --agent-canvas:#f4f7fb; --agent-panel:#ffffff; --agent-blue:#2563eb; --agent-green:#047857; --agent-amber:#b45309; }
body { background: var(--agent-canvas) !important; }
.agent-shell { max-width: 1440px; margin: 0 auto; }
.agent-head { display:flex; align-items:center; justify-content:space-between; gap:20px; margin:22px 0 14px; padding:18px 22px; border-bottom:1px solid var(--agent-line); background:transparent; }
.agent-brand { display:flex; gap:12px; align-items:center; }.agent-mark { width:34px; height:34px; display:grid; place-items:center; color:#fff; background:#2563eb; border-radius:7px; font-weight:800; font-size:12px; }
.agent-head h1 { margin:0; font-size:20px; letter-spacing:0; }.agent-head p { margin:3px 0 0; color:var(--agent-muted); font-size:13px; }.agent-local { color:var(--agent-green); font-size:12px; font-weight:700; }
#agent-pages { border:0; background:transparent; }.agent-page { padding:20px 0 36px; }.agent-title { margin:0 0 16px; }.agent-title h2 { margin:0; font-size:18px; letter-spacing:0; }.agent-title p { margin:4px 0 0; color:var(--agent-muted); font-size:13px; }
.agent-grid { display:grid; grid-template-columns:260px minmax(0,1fr) 330px; gap:14px; align-items:start; }.agent-two { display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:14px; align-items:start; }
.agent-panel { background:var(--agent-panel); border:1px solid var(--agent-line); border-radius:7px; box-shadow:0 1px 2px rgba(15,23,42,.03); }.agent-panel-pad { padding:16px; }.agent-panel h3 { margin:0 0 8px; font-size:14px; letter-spacing:0; }.agent-panel p { color:var(--agent-muted); font-size:13px; }.agent-stat { display:flex; justify-content:space-between; align-items:center; padding:11px 0; border-bottom:1px solid #edf1f6; font-size:13px; }.agent-stat:last-child { border-bottom:0; }.agent-stat strong { font-size:12px; color:var(--agent-green); }
.agent-guide { margin-top:14px; border-left:3px solid #93c5fd; padding:10px 12px; background:#f8fbff; color:#475569; font-size:12px; line-height:1.65; }.agent-guide code { color:#1d4ed8; font-size:12px; }
.agent-queue { margin:0; padding:0; list-style:none; }.agent-queue li { padding:10px 0; border-bottom:1px solid #edf1f6; font-size:13px; }.agent-queue li:last-child { border:0; }.agent-queue small { display:block; margin-top:3px; color:var(--agent-muted); }
.agent-project-row { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:12px; padding:15px 16px; border-bottom:1px solid var(--agent-line); }.agent-project-row:last-child { border-bottom:0; }.agent-project-row h3 { margin:0; font-size:14px; }.agent-project-row p { margin:4px 0 0; font-size:12px; color:var(--agent-muted); }.agent-tag { display:inline-block; padding:3px 7px; border:1px solid #bbf7d0; background:#f0fdf4; color:#047857; font-size:11px; font-weight:700; border-radius:999px; }
.agent-run { width:100%; margin-top:12px; }.agent-help .wrap { padding:0!important; }.agent-help summary { font-size:13px; color:#334155; }.agent-help pre { margin:10px 0 0; padding:10px; overflow:auto; border:1px solid #e2e8f0; background:#f8fafc; border-radius:5px; font-size:12px; line-height:1.55; }
@media (max-width: 960px) { .agent-grid,.agent-two { grid-template-columns:1fr; }.agent-head { align-items:flex-start; }.agent-local { display:none; } }
"""


def _agent_plan_html(
    source_path: str | None,
    formula_bundle: str | None,
    asset_bundle: str | None,
    bibliography_file: str | None,
    target_name: str,
) -> str:
    entries = [
        ("原稿", source_path, "DOCX / Markdown / PDF / LaTeX"),
        ("公式合集", formula_bundle, "JSON 或 ZIP，使用 [EQ:公式ID]"),
        ("图表合集", asset_bundle, "ZIP + manifest.csv，使用 [FIG:id] 或 [TABLE:id]"),
        ("参考文献", bibliography_file, "BibTeX，使用 [CITE:key]"),
    ]
    ready = sum(1 for _, path, _ in entries if path)
    rows = "".join(
        f"<li><strong>{escape(label)}</strong><small>{'已准备' if path else escape(hint)}</small></li>"
        for label, path, hint in entries
    )
    target = escape((target_name or "未选择目标格式").strip())
    return (
        "<section class='agent-panel agent-panel-pad'><h3>Agent 任务计划</h3>"
        f"<p>已准备 {ready}/4 类资料；目标：{target}</p><ul class='agent-queue'>{rows}</ul>"
        "<p>Agent 只会执行已确认规则与可验证映射；缺失或低置信度项目会进入审阅队列。</p></section>"
    )


def build_agent_workspace() -> gr.Blocks:
    rule_choices = _rule_dropdown_choices()
    profile_choices_ui = _profile_dropdown_choices()
    asset_manifest_template = TEMPLATE_DIR / "assets.manifest.csv"
    with gr.Blocks(title="PaperFormat Agent", elem_classes=["agent-shell"]) as demo:
        gr.HTML("""
        <header class='agent-head'>
          <div class='agent-brand'><div class='agent-mark'>PF</div><div><h1>PaperFormat Agent</h1><p>论文资料包到可审计排版交付</p></div></div>
          <div class='agent-local'>LOCAL WORKSPACE · API KEY STAYS IN .env</div>
        </header>
        """)
        with gr.Tabs(selected="projects", elem_id="agent-pages"):
            with gr.Tab("项目", id="projects"):
                with gr.Column(elem_classes=["agent-page"]):
                    gr.HTML("<div class='agent-title'><h2>论文任务</h2><p>创建任务后，在工作台准备资料并由 Agent 完成排版。</p></div>")
                    project_status = gr.HTML(value="""
                    <section class='agent-panel'>
                      <div class='agent-project-row'><div><h3>新建论文任务</h3><p>上传原稿、选择目标格式，然后开始 Agent 排版。</p></div><span class='agent-tag'>待开始</span></div>
                      <div class='agent-project-row'><div><h3>本地优先</h3><p>文件处理、编译和交付均在当前电脑完成。</p></div><span class='agent-tag'>已就绪</span></div>
                    </section>""")

            with gr.Tab("工作台", id="workspace"):
                with gr.Column(elem_classes=["agent-page"]):
                    gr.HTML("<div class='agent-title'><h2>任务工作台</h2><p>准备四类资料，选择目标格式，确认后交由 Agent 执行。</p></div>")
                    with gr.Row():
                        project_name = gr.Textbox(label="项目名称", placeholder="例如：多模态医学影像研究", scale=3)
                        target_name = gr.Textbox(label="目标期刊或学校", placeholder="例如：IEEE Transactions on ...", scale=4)
                        automation_mode = gr.Dropdown(label="自动化等级", choices=["严格执行", "AI 辅助", "高度自动"], value="AI 辅助", scale=2)
                    with gr.Row():
                        uploaded = gr.File(label="原稿", type="filepath", file_types=[".docx", ".pdf", ".md", ".markdown", ".tex", ".zip"], scale=3)
                        initial_asset_bundle = gr.File(label="图表合集 ZIP", type="filepath", file_types=[".zip"], scale=2)
                        formula_bundle = gr.File(label="公式合集 JSON / ZIP", type="filepath", file_types=[".json", ".zip"], scale=2)
                        bibliography_file = gr.File(label="参考文献 BibTeX", type="filepath", file_types=[".bib"], scale=2)
                    with gr.Row():
                        target_guide = gr.File(label="期刊指南或官方模板（可选，DeepSeek 识别）", type="filepath", file_types=[".pdf", ".docx", ".md", ".markdown", ".txt"], scale=3)
                        initial_annotation_bundle = gr.File(label="图表题注工作簿（可选）", type="filepath", file_types=[".xlsx", ".zip"], scale=2)
                        reference_article = gr.File(label="参考样稿（可选）", type="filepath", file_types=[".pdf", ".docx"], scale=2)
                    with gr.Row():
                        gr.DownloadButton("下载公式模板", value=str(FORMULA_TEMPLATE) if FORMULA_TEMPLATE.exists() else None, interactive=FORMULA_TEMPLATE.exists())
                        gr.DownloadButton("下载图表题注模板", value=str(ANNOTATION_TEMPLATE) if ANNOTATION_TEMPLATE.exists() else None, interactive=ANNOTATION_TEMPLATE.exists())
                        gr.DownloadButton("下载图表清单模板", value=str(asset_manifest_template) if asset_manifest_template.exists() else None, interactive=asset_manifest_template.exists())
                    with gr.Accordion("资料准备与占位符说明", open=False, elem_classes=["agent-help"]):
                        gr.HTML("""
                        <div class='agent-guide'>原稿中可直接使用明确占位符；也可先上传资料，随后在审阅页处理 Agent 给出的映射建议。<pre>
[FIG:fig-framework]    图像或示意图
[TABLE:tab-results]    表格
[EQ:eq-loss]           公式
[CITE:smith2024; wang2023]  文献引用

图表 ZIP 内使用 manifest.csv：id,type,file
fig-framework,figure,framework.png
tab-results,table,results.xlsx</pre></div>
                        """)
                    with gr.Row():
                        journal_profile = gr.Dropdown(label="内置期刊规则包", choices=profile_choices_ui, value=RULE_NONE, scale=2)
                        rule_file = gr.Dropdown(label="基础格式规则", choices=rule_choices, value=RULE_NONE, scale=2)
                        requirement_text = gr.Textbox(label="补充要求（可选）", placeholder="例如：双栏、图注置下、IEEE 引用", scale=4)
                    with gr.Row():
                        match_button = gr.Button("匹配期刊", variant="secondary", scale=1)
                        identify_rule_button = gr.Button("DeepSeek 识别规则", variant="secondary", scale=1)
                        llm_rules_confirmed = gr.Checkbox(label="采用已勾选的 AI 规则", value=False, scale=2)
                    journal_match = gr.Markdown(visible=False)
                    llm_rule_review = gr.Markdown("上传官方指南后可由 DeepSeek 提取带证据的候选规则。")
                    llm_rule_rows = gr.Dataframe(headers=["采用", "规则", "字段", "识别值", "依据", "置信度", "原文证据"], datatype=["bool", "str", "str", "str", "str", "number", "str"], type="array", interactive=True, wrap=True, max_height=240)
                    llm_rule_analysis = gr.State()
                    agent_plan = gr.HTML(value=_agent_plan_html(None, None, None, None, ""))
                    output_path = gr.Textbox(label="交付目录", value=str(OUTPUT_DIR))
                    run_button = gr.Button("开始 Agent 排版", variant="primary", elem_classes=["agent-run"])
                    run_state = gr.State()

            with gr.Tab("审阅", id="review"):
                with gr.Column(elem_classes=["agent-page"]):
                    gr.HTML("<div class='agent-title'><h2>Agent 审阅</h2><p>查看执行结果、处理例外，并在确认后生成正式交付。</p></div>")
                    with gr.Row():
                        structure_status = gr.HTML(value=_capability_feedback_html("等待任务", "完成排版后显示结构检查结果。"))
                        asset_status = gr.HTML(value=_capability_feedback_html("等待任务", "完成排版后显示图表和公式映射结果。"))
                        citation_status = gr.HTML(value=_capability_feedback_html("等待任务", "完成排版后显示文献引用映射结果。"))
                    with gr.Row():
                        comparison_view = gr.HTML(value=_initial_comparison_html(), scale=3)
                        repair_status = gr.HTML(value=_capability_feedback_html("等待任务", "完成排版后显示自动修复和待确认项。"), scale=1)
                    with gr.Accordion("执行摘要与审计轨迹", open=True):
                        summary = gr.Markdown()
                        activity_view = gr.HTML(value="<p>Agent 尚未运行。</p>")
                    with gr.Accordion("排版预览", open=True):
                        reviewer = gr.HTML(value=_reviewer_html(None))
                    with gr.Accordion("问题处理工具", open=False):
                        with gr.Row():
                            structure_button = gr.Button("重新检查结构")
                            asset_button = gr.Button("检查资产映射")
                            citation_button = gr.Button("检查引用映射")
                            repair_button = gr.Button("复核格式修复")
                    report_file = gr.File(label="格式审计报告")
                    compile_log = gr.File(label="编译日志")

            with gr.Tab("交付", id="delivery"):
                with gr.Column(elem_classes=["agent-page"]):
                    gr.HTML("<div class='agent-title'><h2>交付中心</h2><p>正式导出前会检查未解析占位符、资料缺失和编译状态。</p></div>")
                    delivery_dashboard = gr.HTML(value=_delivery_dashboard_html())
                    formal_button = gr.Button("生成正式交付文件", variant="primary")
                    with gr.Row():
                        pdf_file = gr.File(label="正式 PDF", visible=False)
                        word_file = gr.File(label="正式 Word (DOCX)", visible=False)
                    with gr.Row():
                        tex_file = gr.File(label="LaTeX 主文件", visible=False)
                        project_file = gr.File(label="LaTeX 源码包", visible=False)

        plan_inputs = [uploaded, formula_bundle, initial_asset_bundle, bibliography_file, target_name]
        for component in plan_inputs:
            component.change(_agent_plan_html, inputs=plan_inputs, outputs=agent_plan, queue=False)
        match_button.click(match_journal, inputs=target_name, outputs=[journal_profile, journal_match])
        identify_rule_button.click(identify_rule_document, inputs=target_guide, outputs=[llm_rule_review, llm_rule_rows, llm_rule_analysis, llm_rules_confirmed])
        target_guide.change(_reset_rule_document_analysis, outputs=[llm_rule_review, llm_rule_rows, llm_rule_analysis, llm_rules_confirmed], queue=False)
        run_outputs = [summary, tex_file, project_file, report_file, compile_log, pdf_file, word_file, reviewer, run_state, project_status, structure_status, asset_status, citation_status, repair_status, comparison_view, activity_view, delivery_dashboard]
        run_button.click(
            run_agent,
            inputs=[uploaded, gr.State(""), rule_file, target_name, journal_profile, requirement_text, target_guide, reference_article, llm_rule_analysis, llm_rule_rows, llm_rules_confirmed, bibliography_file, initial_asset_bundle, initial_annotation_bundle, formula_bundle, output_path, gr.State(True)],
            outputs=run_outputs,
        )
        formal_button.click(run_formal_export, inputs=run_state, outputs=[summary, tex_file, project_file, compile_log, pdf_file, word_file, reviewer, delivery_dashboard])
        structure_button.click(inspect_structure, inputs=run_state, outputs=structure_status)
        asset_button.click(inspect_assets, inputs=run_state, outputs=asset_status)
        citation_button.click(inspect_citations, inputs=run_state, outputs=citation_status)
        repair_button.click(inspect_repairs, inputs=run_state, outputs=repair_status)
    return demo


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_agent_workspace().launch(
        css=APP_CSS + AGENT_WORKSPACE_CSS,
        js=REVIEW_BRIDGE_JS,
        allowed_paths=[str(OUTPUT_DIR.resolve()), str(REVIEWER_PAGE.resolve()), str(ANNOTATION_TEMPLATE.resolve()), str(FORMULA_TEMPLATE.resolve())],
        server_name="127.0.0.1",
        server_port=7861,
        ssr_mode=False,
    )
