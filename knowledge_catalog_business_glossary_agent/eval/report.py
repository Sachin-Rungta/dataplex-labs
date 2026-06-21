"""Markdown + JSON report rendering for eval runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def render_summary_table(per_domain: Dict[str, Dict]) -> str:
  header = (
      "| Scenario | Terms P | R | F1 | P@10 | Cats matched | Coherence (mean) | Definitions (mean) | Link recall | Rel acc |\n"
      "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n"
  )
  rows = []
  for domain, r in per_domain.items():
    t = r.get("term_metrics", {})
    cm = r.get("category_match", {})
    cc = r.get("category_coherence", {})
    df = r.get("definition_usefulness", {})
    lm = r.get("link_metrics", {})
    rows.append(
        "| {domain} | {p} | {rec} | {f1} | {p10} | {cats} | {coh} | {defs} | {lr} | {ra} |".format(
            domain=domain,
            p=f"{t.get('precision', 0):.2f}" if t else "—",
            rec=f"{t.get('recall', 0):.2f}" if t else "—",
            f1=f"{t.get('f1', 0):.2f}" if t else "—",
            p10=f"{(t.get('p_at_k') or {}).get(10, 0):.2f}" if t else "—",
            cats=(
                f"{cm.get('n_matched', 0)}/{cm.get('n_golden', 0)}"
                if cm else "—"
            ),
            coh=(
                f"{cc.get('mean'):.2f}"
                if (cc and cc.get("mean") is not None) else "—"
            ),
            defs=(
                f"{df.get('mean'):.2f}"
                if (df and df.get("mean") is not None) else "—"
            ),
            lr=(
                f"{lm.get('link_recall'):.2f}"
                if (lm and lm.get("link_recall") is not None) else "—"
            ),
            ra=(
                f"{lm.get('relationship_type_accuracy'):.2f}"
                if (lm and lm.get("relationship_type_accuracy") is not None) else "—"
            ),
        )
    )
  return header + "\n".join(rows)


def render_domain_section(domain: str, result: Dict) -> str:
  label = result.get("label") or domain
  parts: List[str] = [f"\n## {domain} — {label}\n"]
  if result.get("error"):
    parts.append(f"\n> ERROR: {result['error']}\n")
    return "".join(parts)
  if result.get("query"):
    parts.append(f"\n**Query**: {result['query']}\n")
  if result.get("mode"):
    parts.append(f"**Mode**: `{result['mode']}`\n")
  if result.get("gcs_uri"):
    parts.append(f"**GCS context**: `{result['gcs_uri']}`\n")
  if result.get("glossary_id"):
    parts.append(f"**Existing glossary**: `{result['glossary_id']}`\n")
  rec = (result.get("recommendation") or {})
  stats = result.get("graph_stats") or {}
  parts.append(
      "\n**Ingestion stats:** entries={entries}, documents={docs}, "
      "concepts={concepts}, edges={edges}\n".format(
          entries=stats.get("entries", "?"),
          docs=stats.get("documents", "?"),
          concepts=stats.get("concepts", "?"),
          edges=stats.get("edges", "?"),
      )
  )

  parts.append(
      f"**Recommended:** {len(rec.get('categories') or [])} categories,"
      f" {len(rec.get('terms') or [])} terms\n"
  )

  t = result.get("term_metrics") or {}
  if t:
    parts.append("\n### Term-level\n")
    parts.append(
        f"- Precision: {t.get('precision', 0):.3f}  "
        f"Recall: {t.get('recall', 0):.3f}  "
        f"F1: {t.get('f1', 0):.3f}\n"
    )
    parts.append(
        f"- TP: {t.get('true_positives', 0)},"
        f" FP: {t.get('false_positives', 0)},"
        f" FN: {t.get('false_negatives', 0)}\n"
    )
    pk = t.get("p_at_k") or {}
    if pk:
      parts.append(
          "- P@K: "
          + ", ".join(f"P@{k}={v:.2f}" for k, v in sorted(pk.items()))
          + "\n"
      )
    fp = t.get("false_positive_terms") or []
    if fp:
      parts.append(
          "- False positives (recommended but unrecognised by judge):\n"
          + "\n".join(f"  - {n}" for n in fp[:20]) + "\n"
      )
    fn = t.get("false_negative_terms") or []
    if fn:
      parts.append(
          "- False negatives (golden terms the agent missed):\n"
          + "\n".join(f"  - {n}" for n in fn[:20]) + "\n"
      )

  cc = result.get("category_coherence") or {}
  if cc and cc.get("mean") is not None:
    parts.append(
        f"\n### Category coherence (judge rubric, 1-5)\n"
        f"- Mean score: {cc.get('mean'):.2f}, fraction ≥4: {cc.get('frac_ge_4'):.2f}\n"
    )

  df = result.get("definition_usefulness") or {}
  if df and df.get("mean") is not None:
    parts.append(
        f"\n### Definition usefulness (judge rubric, 1-5)\n"
        f"- Mean score: {df.get('mean'):.2f}, fraction ≥4: {df.get('frac_ge_4'):.2f}\n"
    )

  lm = result.get("link_metrics") or {}
  if lm.get("n_expected_links"):
    parts.append(
        f"\n### Link metrics\n"
        f"- Expected: {lm.get('n_expected_links')}, "
        f"proposed: {lm.get('n_proposed_links')}\n"
        f"- Link recall: {lm.get('link_recall'):.3f}\n"
    )
    ra = lm.get("relationship_type_accuracy")
    if ra is not None:
      parts.append(f"- Relationship-type accuracy: {ra:.3f}\n")
    missing = lm.get("missing_links") or []
    if missing:
      parts.append(
          f"- Missing links ({len(missing)}):\n"
          + "\n".join(
              f"  - {m['golden_display_name']} → ...{m['entry_suffix']} ({m['relationship']})"
              for m in missing[:15]
          )
          + "\n"
      )

  return "".join(parts)


def render_markdown(per_domain: Dict[str, Dict], meta: Dict) -> str:
  parts: List[str] = []
  parts.append("# Business Glossary Agent — eval report\n")
  parts.append(f"\n- Generated: {meta.get('generated_at', '?')}")
  parts.append(f"\n- Project: `{meta.get('project', '?')}`")
  parts.append(f"\n- Driver model: `{meta.get('model', '?')}`")
  parts.append(f"\n- Judges: {'OFF (structural metrics only)' if meta.get('skip_judges') else 'ON'}")
  starter = meta.get("starter_glossary")
  if starter:
    parts.append(
        f"\n- Starter glossary: `{starter.get('glossary_id','?')}` "
        f"({'created' if starter.get('created') else 'reused'})"
    )
  parts.append("\n\n## Summary\n\n")
  parts.append(render_summary_table(per_domain))
  parts.append("\n\n## Per-scenario detail\n")
  for domain, result in per_domain.items():
    parts.append(render_domain_section(domain, result))
  return "".join(parts)


def write_reports(
    per_domain: Dict[str, Dict],
    output_dir: str | Path,
    meta: Dict,
) -> Dict[str, Path]:
  """Writes JSON + Markdown reports. Returns dict of {format: path}."""
  out = Path(output_dir)
  out.mkdir(parents=True, exist_ok=True)
  ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

  json_path = out / f"eval-{ts}.json"
  json_path.write_text(
      json.dumps({"meta": meta, "results": per_domain}, indent=2, default=str)
  )

  md_path = out / f"eval-{ts}.md"
  md_path.write_text(render_markdown(per_domain, meta))

  latest_md = out / "latest.md"
  latest_md.write_text(render_markdown(per_domain, meta))
  latest_json = out / "latest.json"
  latest_json.write_text(
      json.dumps({"meta": meta, "results": per_domain}, indent=2, default=str)
  )

  return {"json": json_path, "md": md_path, "latest_md": latest_md}
