"""Week 17 Day 1：文档上传与 ingestion job 状态的 API 验收。"""

import pytest

from app.core.security import create_access_token


async def test_upload_requires_auth(client) -> None:
    response = await client.post(
        "/rag/documents",
        files={"file": ("refund.md", b"# Refund\n", "text/markdown")},
    )

    assert response.status_code == 401


async def test_upload_creates_pending_job_and_can_read_status(
    client,
    auth_headers,
) -> None:
    response = await client.post(
        "/rag/documents",
        headers=auth_headers,
        files={
            "file": (
                "refund.md",
                "# 退款政策\n\n7 天内可退。".encode(),
                "text/markdown",
            )
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"].startswith("ing-")
    assert body["status"] == "pending"
    assert body["filename"] == "refund.md"
    assert body["size_bytes"] > 0
    assert len(body["content_sha256"]) == 64

    status_response = await client.get(
        f"/rag/documents/ingestion-jobs/{body['job_id']}",
        headers=auth_headers,
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["job_id"] == body["job_id"]
    assert status_body["status"] == "pending"
    assert status_body["attempts"] == 0
    assert status_body["max_attempts"] == 3


async def test_upload_rejects_unsupported_extension(
    client,
    auth_headers,
) -> None:
    response = await client.post(
        "/rag/documents",
        headers=auth_headers,
        files={"file": ("malware.exe", b"binary", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "暂不支持该文件类型" in response.json()["detail"]


async def test_upload_rejects_empty_file(client, auth_headers) -> None:
    response = await client.post(
        "/rag/documents",
        headers=auth_headers,
        files={"file": ("empty.md", b"", "text/markdown")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "上传文件不能为空"


async def test_upload_rejects_too_large_file(client, auth_headers) -> None:
    response = await client.post(
        "/rag/documents",
        headers=auth_headers,
        files={
            "file": (
                "too-large.md",
                b"a" * (5 * 1024 * 1024 + 1),
                "text/markdown",
            )
        },
    )

    assert response.status_code == 413


async def test_ingestion_job_is_scoped_to_tenant(
    client,
    create_test_user,
) -> None:
    owner = await create_test_user("job-owner")
    other = await create_test_user("job-other")
    owner_headers = {
        "Authorization": f"Bearer {create_access_token(owner.id)}"
    }
    other_headers = {
        "Authorization": f"Bearer {create_access_token(other.id)}"
    }

    create_response = await client.post(
        "/rag/documents",
        headers=owner_headers,
        files={"file": ("refund.md", b"# Refund", "text/markdown")},
    )
    job_id = create_response.json()["job_id"]

    read_response = await client.get(
        f"/rag/documents/ingestion-jobs/{job_id}",
        headers=other_headers,
    )

    assert read_response.status_code == 404


@pytest.mark.parametrize(
    "filename",
    ["manual.markdown", "faq.html", "index.htm"],
)
async def test_upload_accepts_supported_extensions(
    client,
    auth_headers,
    filename: str,
) -> None:
    response = await client.post(
        "/rag/documents",
        headers=auth_headers,
        files={"file": (filename, b"<h1>doc</h1>", "text/html")},
    )

    assert response.status_code == 202
