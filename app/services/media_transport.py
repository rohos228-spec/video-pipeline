"""Bounded HTTP boundary used by existing provider clients in managed mode.

This is a client helper, not a proxy/worker. Production stays disabled. URLs
come from a pinned explicit profile; fixture redirects are never followed.
"""
import asyncio
import contextvars
import os
from pathlib import Path

import httpx

from app.services.media_jobs import check_url, MediaPolicyError
from app.services.observation import _attempt, record_fact

current_job = contextvars.ContextVar("managed_media_job", default=None)
ROUTES = {("POST", "/api/v1/jobs/createTask"): "generation_submit",
          ("GET", "/api/v1/jobs/recordInfo"): "status"}


async def json_request(method, path, *, payload=None, params=None, headers=None):
    # headers (Authorization) go only to the wire: record_fact below receives body/params, never headers.
    job = current_job.get()
    category = ROUTES.get((method, path))
    if job is None or category is None:
        raise MediaPolicyError("unsupported media HTTP route/method")
    cfg = job.config["media"]
    url = check_url(cfg["origin"] + path, cfg, path=path)
    await record_fact("media_request_input", {"job_id": job.job_id, "category": category},
                      body=payload if payload is not None else params)
    request_id = await job.reserve(category)
    try:
        async with asyncio.timeout(cfg["timeout_s"]), httpx.AsyncClient(timeout=cfg["timeout_s"], trust_env=False, follow_redirects=False) as client:
            async with client.stream(method, url, json=payload, params=params, headers=headers) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1024 * 1024:
                        raise MediaPolicyError("media JSON response exceeds size limit")
                result = httpx.Response(response.status_code, headers=response.headers, content=bytes(content))
        await job.settle(request_id, "received")
        if 300 <= result.status_code < 400:
            raise MediaPolicyError("media redirect denied")
        await record_fact("media_response", {"job_id": job.job_id, "category": category,
            "http_status": result.status_code}, request_id=request_id, body=result.text)
        ctx = _attempt.get()
        if ctx.scope.execution_id in ctx.observer.failures:
            raise MediaPolicyError("mandatory media response audit unavailable")
        return result
    except BaseException:
        await job.settle(request_id, "unknown")
        raise


async def download(job, url):
    cfg = job.config["media"]
    check_url(url, cfg, download=True)
    path = job.path
    path.parent.mkdir(parents=True, exist_ok=True)
    from app.services.artifact_verifier import contained_path
    contained_path(path, job.root)
    partial = contained_path(path.with_name(path.name + "." + job.job_id + ".part"), job.root)
    request_id = await job.reserve("download")
    await job.update(phase="downloading")
    try:
        count = 0
        async with asyncio.timeout(cfg["timeout_s"]), httpx.AsyncClient(timeout=cfg["timeout_s"], trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise MediaPolicyError("artifact HTTP failed or redirect denied")
                if int(response.headers.get("content-length", 0)) > cfg["max_bytes"]:
                    raise MediaPolicyError("artifact size limit exceeded")
                with partial.open("wb") as stream:
                    async for chunk in response.aiter_bytes():
                        count += len(chunk)
                        if count > cfg["max_bytes"]:
                            raise MediaPolicyError("artifact size limit exceeded")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
        if not count:
            raise MediaPolicyError("empty artifact")
        os.replace(partial, path)
        await job.settle(request_id, "received")
        await record_fact("media_download", {"job_id": job.job_id, "bytes": count,
            "local_name": path.name}, request_id=request_id)
        return path
    except BaseException:
        await job.settle(request_id, "unknown")
        # Partial evidence is preserved; it cannot pass as a final artifact.
        raise


async def verify(job, *, expected=None):
    from app.services.artifact_verifier import verify_artifact
    result = await verify_artifact(job.path, root=job.root, kind=job.row.kind, expected=expected)
    if job.phase == "verified":
        if result.get("sha256") != job.row.verification.get("sha256"):
            raise MediaPolicyError("verified artifact changed")
    else:
        await job.update(phase="verifying")
        await job.update(phase="verified" if result["status"] == "passed" else "failed",
                         verification=result)
    await record_fact("artifact_verified", {"job_id": job.job_id, "local_name": job.path.name,
        "provider_job_id": job.provider_job_id, "verification": result})
    if result["status"] != "passed":
        raise MediaPolicyError("artifact verification " + result["status"] + ": " + result.get("reason", "unknown"))
    return result
