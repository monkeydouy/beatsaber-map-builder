"""HTTP API behaviour, exercised against a real app instance."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app instance with isolated storage and no background work."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "4")
    get_settings.cache_clear()

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()


class TestHealth:
    def test_reports_status_and_capabilities(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert set(body) >= {"ffmpeg", "ytdlp", "youtube_enabled", "active_jobs"}

    def test_difficulties_are_listed_easiest_first(self, client):
        body = client.get("/api/difficulties").json()
        assert [item["value"] for item in body] == [
            "Easy", "Normal", "Hard", "Expert", "ExpertPlus",
        ]
        assert all(item["description"] for item in body)

    def test_expert_plus_is_labelled_with_a_plus(self, client):
        body = client.get("/api/difficulties").json()
        assert body[-1]["label"] == "Expert+"

    def test_styles_are_listed(self, client):
        body = client.get("/api/styles").json()
        assert {item["value"] for item in body} == {"balanced", "dance", "technical"}


class TestUploadValidation:
    def test_rejects_a_non_audio_extension(self, client):
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("payload.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_rejects_an_html_content_type(self, client):
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("song.mp3", io.BytesIO(b"data"), "text/html")},
        )
        assert response.status_code == 400

    def test_rejects_an_empty_file(self, client):
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("song.mp3", io.BytesIO(b""), "audio/mpeg")},
        )
        assert response.status_code == 400

    def test_rejects_an_oversized_file(self, client, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "max_upload_mb", 1)
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("song.mp3", io.BytesIO(b"x" * (2 * 1024 * 1024)), "audio/mpeg")},
        )
        assert response.status_code == 413

    def test_requires_a_file(self, client):
        assert client.post("/api/jobs/upload").status_code == 422


class TestYouTubeValidation:
    def test_rejects_a_non_youtube_host(self, client):
        response = client.post(
            "/api/jobs/youtube", json={"url": "https://evil.com/x", "confirmed": True}
        )
        assert response.status_code == 400

    def test_rejects_a_private_address(self, client):
        response = client.post(
            "/api/jobs/youtube",
            json={"url": "http://169.254.169.254/latest/", "confirmed": True},
        )
        assert response.status_code == 400

    def test_requires_explicit_permission_confirmation(self, client):
        response = client.post(
            "/api/jobs/youtube",
            json={"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "confirmed": False},
        )
        assert response.status_code == 400
        assert "permission" in response.json()["detail"].lower()

    def test_preview_rejects_a_bad_url(self, client):
        response = client.post(
            "/api/jobs/youtube/preview", json={"url": "not a url", "confirmed": False}
        )
        assert response.status_code == 400


class TestJobRetrieval:
    def test_unknown_job_is_404(self, client):
        response = client.get("/api/jobs/3f2504e0-4f89-41d3-9a0c-0305e82c3301")
        assert response.status_code == 404

    def test_malformed_job_id_is_404_not_500(self, client):
        for candidate in ("not-a-uuid", "..", "%2e%2e"):
            assert client.get(f"/api/jobs/{candidate}").status_code == 404

    def test_traversal_in_the_job_id_never_reaches_the_filesystem(self, client):
        response = client.get("/api/jobs/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code in (307, 404)

    def test_download_of_an_unknown_job_is_404(self, client):
        response = client.get("/api/jobs/3f2504e0-4f89-41d3-9a0c-0305e82c3301/download")
        assert response.status_code == 404


class TestGenerationRequestValidation:
    JOB = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"

    def test_unknown_job_is_404(self, client):
        response = client.post(f"/api/jobs/{self.JOB}/generate", json={})
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "payload",
        [
            {"difficulty": "Impossible"},
            {"style": "chaos"},
            {"intensity": 1.5},
            {"intensity": -0.2},
            {"seed": -1},
        ],
    )
    def test_invalid_options_are_rejected_before_the_job_is_looked_up(
        self, client, payload
    ):
        response = client.post(f"/api/jobs/{self.JOB}/generate", json=payload)
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Expert", "Expert"),
            ("expert", "Expert"),
            ("ExpertPlus", "ExpertPlus"),
            ("Expert+", "ExpertPlus"),
            ("expert_plus", "ExpertPlus"),
        ],
    )
    def test_difficulty_spellings_are_normalised(self, raw, expected):
        from app.schemas.jobs import GenerationRequest

        assert GenerationRequest(difficulty=raw).difficulty == expected

    def test_expert_is_the_default_difficulty(self):
        from app.schemas.jobs import GenerationRequest

        request = GenerationRequest()
        assert request.difficulty == "Expert"
        assert request.style == "balanced"

    def test_text_fields_are_whitespace_normalised(self):
        from app.schemas.jobs import GenerationRequest

        request = GenerationRequest(title="  Neon   Circuit  ", artist="   ")
        assert request.title == "Neon Circuit"
        assert request.artist is None


class TestErrorResponses:
    def test_validation_errors_return_a_plain_message(self, client):
        response = client.post(
            "/api/jobs/3f2504e0-4f89-41d3-9a0c-0305e82c3301/generate",
            json={"difficulty": "Nope"},
        )
        body = response.json()
        assert isinstance(body["detail"], str)
        # Never leak internals to the client.
        assert "Traceback" not in body["detail"]
        assert "/Users/" not in body["detail"]
        assert "app/" not in body["detail"]


class TestBatchUpload:
    """One bad file must not sink the rest of the batch."""

    @staticmethod
    def _file(name: str, data: bytes = b"x" * 2048, mime: str = "audio/mpeg"):
        return ("files", (name, io.BytesIO(data), mime))

    def test_accepts_several_files_at_once(self, client):
        response = client.post(
            "/api/jobs/upload/batch",
            files=[self._file("a.mp3"), self._file("b.mp3"), self._file("c.mp3")],
        )
        assert response.status_code == 201
        body = response.json()
        assert body["accepted"] == 3
        assert len({item["job_id"] for item in body["items"]}) == 3

    def test_reports_per_file_outcomes(self, client):
        response = client.post(
            "/api/jobs/upload/batch",
            files=[
                self._file("good.mp3"),
                self._file("bad.exe", mime="application/octet-stream"),
                self._file("empty.mp3", data=b""),
            ],
        )
        assert response.status_code == 201
        body = response.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 2
        by_name = {item["filename"]: item for item in body["items"]}
        assert by_name["good.mp3"]["job_id"]
        assert by_name["bad.exe"]["error"]
        assert by_name["empty.mp3"]["error"]

    def test_rejects_an_oversized_batch(self, client):
        from app.api.routes_jobs import MAX_BATCH_FILES

        files = [self._file(f"song{index}.mp3") for index in range(MAX_BATCH_FILES + 1)]
        assert client.post("/api/jobs/upload/batch", files=files).status_code == 413

    def test_requires_at_least_one_file(self, client):
        assert client.post("/api/jobs/upload/batch").status_code == 422

    def test_each_file_gets_its_own_job(self, client):
        response = client.post(
            "/api/jobs/upload/batch", files=[self._file("a.mp3"), self._file("b.mp3")]
        )
        for item in response.json()["items"]:
            assert client.get(f"/api/jobs/{item['job_id']}").status_code == 200

    def test_the_single_upload_endpoint_still_works(self, client):
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("song.mp3", io.BytesIO(b"x" * 2048), "audio/mpeg")},
        )
        assert response.status_code == 201
        assert response.json()["job_id"]


class TestBundleDownload:
    """Several finished maps as one archive."""

    def test_unknown_jobs_give_404_not_an_empty_zip(self, client):
        response = client.get(
            "/api/jobs/bundle/download?ids=3f2504e0-4f89-41d3-9a0c-0305e82c3301"
        )
        assert response.status_code == 404

    def test_requires_at_least_one_id(self, client):
        assert client.get("/api/jobs/bundle/download?ids=").status_code == 400

    def test_caps_how_many_can_be_bundled(self, client):
        from uuid import uuid4

        from app.api.routes_jobs import MAX_BATCH_FILES

        ids = ",".join(str(uuid4()) for _ in range(MAX_BATCH_FILES + 1))
        assert client.get(f"/api/jobs/bundle/download?ids={ids}").status_code == 400

    def test_the_route_is_not_shadowed_by_the_job_id_route(self, client):
        """`/jobs/bundle/download` must not be read as a job called 'bundle'."""
        response = client.get("/api/jobs/bundle/download?ids=")
        assert response.status_code == 400  # the bundle handler's own error
        assert "selected" in response.json()["detail"].lower()


class TestYouTubeBatch:
    """Several links at once, with each link's outcome reported separately."""

    ONE = "https://youtube.com/watch?v=dQw4w9WgXcQ"
    TWO = "https://youtu.be/aaaaaaaaaaa"

    def test_requires_explicit_permission_for_the_whole_batch(self, client):
        response = client.post(
            "/api/jobs/youtube/batch",
            json={"urls": [self.ONE, self.TWO], "confirmed": False},
        )
        assert response.status_code == 400
        assert "permission" in response.json()["detail"].lower()

    def test_rejects_an_empty_list(self, client):
        response = client.post(
            "/api/jobs/youtube/batch", json={"urls": [], "confirmed": True}
        )
        assert response.status_code == 422

    def test_caps_how_many_links_are_accepted(self, client):
        from app.api.routes_jobs import MAX_BATCH_URLS

        urls = [f"https://youtu.be/{'a' * 10}{index}" for index in range(MAX_BATCH_URLS + 1)]
        response = client.post(
            "/api/jobs/youtube/batch", json={"urls": urls, "confirmed": True}
        )
        assert response.status_code == 413

    def test_bad_hosts_are_reported_per_link_not_fatal(self, client):
        """SSRF rejection must be an item-level error, not a 500."""
        response = client.post(
            "/api/jobs/youtube/batch",
            json={
                "urls": [
                    "https://evil.com/watch?v=dQw4w9WgXcQ",
                    "http://169.254.169.254/latest/",
                    "file:///etc/passwd",
                ],
                "confirmed": True,
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["accepted"] == 0
        assert body["rejected"] == 3
        for item in body["items"]:
            assert item["error"]
            assert item["job_id"] is None

    def test_duplicate_links_are_collapsed(self, client):
        response = client.post(
            "/api/jobs/youtube/batch",
            json={"urls": [self.ONE, self.ONE, self.ONE], "confirmed": True},
        )
        assert response.status_code == 201
        assert len(response.json()["items"]) == 1

    def test_the_single_link_endpoint_still_works(self, client):
        response = client.post(
            "/api/jobs/youtube", json={"url": "https://evil.com/x", "confirmed": True}
        )
        assert response.status_code == 400
