"""Dossier export — HTML + PDF executive briefs."""
from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/dossier/{dossier_id}/html")
async def export_dossier_html(dossier_id: int) -> HTMLResponse:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "SELECT d.data, d.version, d.cost_cents, d.generated_at, b.name, b.domain "
                    "FROM dossiers d JOIN brands b ON d.brand_id = b.id "
                    "WHERE d.id = :did"
                ),
                {"did": dossier_id},
            )
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="dossier_not_found")

    data, version, cost_cents, generated_at, brand_name, domain = row
    return HTMLResponse(content=_render_html(data, brand_name, domain, version, generated_at))


def _render_html(data: dict, brand_name: str, domain: str | None, version: int, generated_at: Any) -> str:
    research = data.get("research") or {}
    competitor = data.get("competitor") or {}
    opportunity = data.get("opportunity") or {}
    score = data.get("score") or {}
    outreach = data.get("outreach") or {}

    company = research.get("company") or {}
    positioning = research.get("positioning") or {}
    digital = research.get("digital_footprint") or {}
    services = opportunity.get("services_recommended") or []
    competitors_list = competitor.get("competitors") or []
    gap_map = competitor.get("gap_map") or {}
    score_why = score.get("why") or []
    score_breakdown = score.get("breakdown") or {}

    def esc(v: Any) -> str:
        return html.escape(str(v)) if v else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Dossier: {esc(brand_name)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, 'Segoe UI', sans-serif; color: #1a1a2e; padding: 40px; max-width: 900px; margin: 0 auto; line-height: 1.6; }}
  h1 {{ font-size: 28px; margin-bottom: 4px; color: #0f172a; }}
  h2 {{ font-size: 18px; margin: 24px 0 12px; color: #334155; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
  h3 {{ font-size: 14px; margin: 16px 0 8px; color: #475569; }}
  .meta {{ color: #64748b; font-size: 13px; margin-bottom: 20px; }}
  .score-box {{ display: inline-flex; align-items: center; gap: 12px; background: #f1f5f9; border-radius: 8px; padding: 16px 24px; margin: 12px 0; }}
  .score-num {{ font-size: 48px; font-weight: 800; color: #0f172a; }}
  .tier {{ display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; text-transform: uppercase; }}
  .tier.hot {{ background: #fef2f2; color: #dc2626; }}
  .tier.warm {{ background: #fffbeb; color: #d97706; }}
  .tier.watchlist {{ background: #eff6ff; color: #2563eb; }}
  .tier.park {{ background: #f1f5f9; color: #64748b; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #e2e8f0; }}
  th {{ background: #f8fafc; color: #475569; font-weight: 600; }}
  .service-tag {{ display: inline-block; background: #eff6ff; color: #1d4ed8; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin: 2px; }}
  .gap-tag {{ display: inline-block; background: #fffbeb; color: #92400e; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin: 2px; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 4px 0; font-size: 14px; }}
  .footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 11px; }}
  @media print {{
    body {{ padding: 20px; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>
<h1>{esc(brand_name)}</h1>
<div class="meta">{esc(domain)} · v{version} · Generated {esc(str(generated_at)[:19])}</div>

<h2>Lead Score</h2>
<div class="score-box">
  <div class="score-num">{esc(score.get('total', '—'))}</div>
  <div>
    <span class="tier {esc(score.get('tier', 'park'))}">{esc(score.get('tier', '—'))}</span><br>
    <span style="font-size:12px;color:#64748b">Confidence: {esc(score.get('confidence', '—'))}</span>
  </div>
</div>
{_render_list("Why this score", score_why)}

<h2>Company Profile</h2>
<table>
  <tr><th>Legal Name</th><td>{esc(company.get('legal_name'))}</td></tr>
  <tr><th>Category</th><td>{esc(positioning.get('category'))}</td></tr>
  <tr><th>Audience</th><td>{esc(positioning.get('audience'))}</td></tr>
  <tr><th>HQ</th><td>{esc(company.get('hq'))}</td></tr>
  <tr><th>Employees (est)</th><td>{esc(company.get('employees_est'))}</td></tr>
  <tr><th>Revenue Band</th><td>{esc(company.get('revenue_band'))}</td></tr>
  <tr><th>Revenue Est (INR)</th><td>{esc(company.get('revenue_estimate_inr'))}</td></tr>
  <tr><th>Funding Stage</th><td>{esc(company.get('funding_stage'))}</td></tr>
</table>

<h2>Digital Footprint</h2>
<table>
  <tr><th>Active Channels</th><td>{esc(', '.join(digital.get('channels_active') or []))}</td></tr>
  <tr><th>Martech Stack</th><td>{esc(', '.join(digital.get('martech_stack') or []))}</td></tr>
  <tr><th>Web Perf</th><td>{esc(digital.get('web_perf_signal'))}</td></tr>
  <tr><th>SEO</th><td>{esc(digital.get('seo_signal'))}</td></tr>
  <tr><th>Paid Ads</th><td>{esc(digital.get('paid_signal'))}</td></tr>
  <tr><th>Social</th><td>{esc(digital.get('social_signal'))}</td></tr>
  <tr><th>Email</th><td>{esc(digital.get('email_signal'))}</td></tr>
  <tr><th>Content Maturity</th><td>{esc(digital.get('content_maturity'))}</td></tr>
  <tr><th>Programmatic</th><td>{esc(digital.get('programmatic_signal'))}</td></tr>
</table>

<h2>Competitors</h2>
{''.join(f'<div style="margin:4px 0;font-size:14px">{esc(c.get("name"))} {f"({esc(c.get('domain'))})" if c.get("domain") else ""}</div>' for c in competitors_list)}

<h3>Gap Map</h3>
{''.join(f'<span class="gap-tag">{esc(k)}: {esc(v)}</span>' for k, v in gap_map.items())}

<h2>Opportunities</h2>
<table>
  <tr><th>Service</th><th>Rationale</th><th>Impact</th><th>Confidence</th></tr>
  {''.join(f"<tr><td><span class='service-tag'>{esc(s.get('service'))}</span></td><td>{esc(s.get('rationale'))}</td><td>{esc(s.get('estimated_impact'))}</td><td>{esc(s.get('confidence'))}</td></tr>" for s in services)}
</table>

<div class="footer">
  Generated by Grabon Intel · Confidential · Do not distribute externally
</div>
</body>
</html>"""


def _render_list(title: str, items: list) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{html.escape(str(i))}</li>" for i in items)
    return f"<h3>{html.escape(title)}</h3><ul>{lis}</ul>"
