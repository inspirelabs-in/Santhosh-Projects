"""Enrich assignment submissions by fetching real data from GitHub, Loom, and deployed URLs.

All functions are best-effort: failures return an error dict instead of raising,
so the LLM still gets whatever data is available.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_GH_API = "https://api.github.com"


def _gh_headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "hr-agent/1.0"}
    token = settings.github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL. Returns None if unparseable."""
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parsed = urlparse(url)
    if "github" not in (parsed.hostname or ""):
        return None
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


async def enrich_github(url: str) -> dict[str, Any]:
    """Fetch repo metadata, README, languages, and recent commits from GitHub API."""
    parsed = parse_github_url(url)
    if not parsed:
        return {"error": f"Could not parse GitHub URL: {url}"}

    owner, repo = parsed
    headers = _gh_headers()
    result: dict[str, Any] = {"repo_name": f"{owner}/{repo}", "source_url": url}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            repo_resp = await client.get(f"{_GH_API}/repos/{owner}/{repo}")
            if repo_resp.status_code == 404:
                return {"error": "Repository not found (may be private)", "repo_name": f"{owner}/{repo}"}
            if repo_resp.status_code == 403:
                return {"error": "GitHub API rate limit exceeded", "repo_name": f"{owner}/{repo}"}
            if repo_resp.status_code != 200:
                return {"error": f"GitHub API returned {repo_resp.status_code}", "repo_name": f"{owner}/{repo}"}

            data = repo_resp.json()
            result["description"] = data.get("description")
            result["stars"] = data.get("stargazers_count", 0)
            result["forks"] = data.get("forks_count", 0)
            result["primary_language"] = data.get("language")
            result["created_at"] = data.get("created_at")
            result["last_pushed"] = data.get("pushed_at")
            result["default_branch"] = data.get("default_branch", "main")
            result["is_fork"] = data.get("fork", False)
            result["open_issues"] = data.get("open_issues_count", 0)
            result["size_kb"] = data.get("size", 0)

            langs_resp = await client.get(f"{_GH_API}/repos/{owner}/{repo}/languages")
            if langs_resp.status_code == 200:
                result["languages"] = langs_resp.json()

            contents_resp = await client.get(f"{_GH_API}/repos/{owner}/{repo}/contents")
            if contents_resp.status_code == 200:
                items = contents_resp.json()
                if isinstance(items, list):
                    result["file_tree"] = [
                        {"name": f.get("name"), "type": f.get("type"), "size": f.get("size", 0)}
                        for f in items[:50]
                    ]

            readme_resp = await client.get(f"{_GH_API}/repos/{owner}/{repo}/readme")
            if readme_resp.status_code == 200:
                readme_data = readme_resp.json()
                encoding = readme_data.get("encoding", "")
                content = readme_data.get("content", "")
                if encoding == "base64" and content:
                    try:
                        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                        result["readme_text"] = decoded[:3000]
                    except Exception:  # noqa: BLE001
                        result["readme_text"] = "(decode failed)"
                else:
                    result["readme_text"] = "(not base64)"

            commits_resp = await client.get(
                f"{_GH_API}/repos/{owner}/{repo}/commits",
                params={"per_page": 10},
            )
            if commits_resp.status_code == 200:
                commits = commits_resp.json()
                result["total_commits_approx"] = int(
                    commits_resp.headers.get("x-total-count", len(commits))
                )
                result["recent_commits"] = [
                    {
                        "sha": c.get("sha", "")[:7],
                        "message": (c.get("commit", {}).get("message") or "")[:120],
                        "date": c.get("commit", {}).get("committer", {}).get("date"),
                        "author": c.get("commit", {}).get("author", {}).get("name"),
                    }
                    for c in (commits if isinstance(commits, list) else [])[:10]
                ]

    except httpx.TimeoutException:
        return {"error": "GitHub API request timed out", "repo_name": f"{owner}/{repo}"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("GitHub enrichment failed for %s: %s", url, exc)
        return {"error": f"GitHub fetch failed: {exc}", "repo_name": f"{owner}/{repo}"}

    return result


def _parse_loom_id(url: str) -> str | None:
    """Extract video ID from a Loom share URL."""
    match = re.search(r"loom\.com/share/([a-f0-9]+)", url)
    return match.group(1) if match else None


async def enrich_loom(url: str) -> dict[str, Any]:
    """Fetch Loom video metadata via oEmbed and optionally transcript via API."""
    result: dict[str, Any] = {"source_url": url}
    video_id = _parse_loom_id(url)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            oembed_resp = await client.get(
                "https://www.loom.com/v1/oembed",
                params={"url": url},
            )
            if oembed_resp.status_code == 200:
                data = oembed_resp.json()
                result["title"] = data.get("title")
                result["duration_seconds"] = data.get("duration")
                result["thumbnail_url"] = data.get("thumbnail_url")
                result["provider"] = data.get("provider_name")
            else:
                result["oembed_error"] = f"oEmbed returned {oembed_resp.status_code}"

            settings = get_settings()
            if video_id and settings.loom_api_key:
                transcript_resp = await client.get(
                    f"https://developer.loom.com/v1/videos/{video_id}/transcripts",
                    headers={
                        "Authorization": f"Bearer {settings.loom_api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if transcript_resp.status_code == 200:
                    t_data = transcript_resp.json()
                    segments = t_data if isinstance(t_data, list) else t_data.get("segments", [])
                    if segments:
                        lines = [
                            seg.get("text", "") for seg in segments if isinstance(seg, dict)
                        ]
                        result["transcript_text"] = " ".join(lines)[:5000]
                    else:
                        result["transcript_text"] = t_data.get("text", "")[:5000] if isinstance(t_data, dict) else ""

    except httpx.TimeoutException:
        return {"error": "Loom request timed out", "source_url": url}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Loom enrichment failed for %s: %s", url, exc)
        return {"error": f"Loom fetch failed: {exc}", "source_url": url}

    return result


async def check_deployed_url(url: str) -> dict[str, Any]:
    """HEAD request to deployed URL — check if it's live."""
    if not url or not url.startswith("http"):
        return {"error": "No valid deployed URL provided"}

    try:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.head(url)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {
                "url": url,
                "status_code": resp.status_code,
                "is_live": 200 <= resp.status_code < 400,
                "response_time_ms": elapsed_ms,
            }
    except httpx.TimeoutException:
        return {"url": url, "is_live": False, "error": "Timed out after 10s"}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "is_live": False, "error": str(exc)[:200]}
