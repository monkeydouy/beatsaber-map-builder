"""Input validation, SSRF containment and path-traversal defences."""

from __future__ import annotations

import pytest

from app.services.jobs.store import is_valid_job_id
from app.services.media.base import MediaAcquisitionError
from app.services.media.upload_source import UploadedFileSource, sanitize_filename
from app.services.media.youtube_source import canonical_url, extract_video_id


class TestYouTubeUrlValidation:
    """The URL feature must never become a generic fetcher."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "youtube.com/watch?v=dQw4w9WgXcQ",
        ],
    )
    def test_accepts_real_youtube_links(self, url):
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/watch?v=dQw4w9WgXcQ",
            "http://127.0.0.1:8000/watch?v=dQw4w9WgXcQ",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/watch?v=dQw4w9WgXcQ",
            "http://192.168.1.1/watch?v=dQw4w9WgXcQ",
            "https://evil.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
            "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
        ],
    )
    def test_rejects_other_hosts_and_private_addresses(self, url):
        with pytest.raises(MediaAcquisitionError):
            extract_video_id(url)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
            "gopher://youtube.com/watch?v=dQw4w9WgXcQ",
            "data:text/html,<script>alert(1)</script>",
        ],
    )
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(MediaAcquisitionError):
            extract_video_id(url)

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "https://youtube.com/",
            "https://youtube.com/watch",
            "https://youtube.com/watch?v=tooshort",
            "https://youtube.com/watch?v=way_too_long_to_be_an_id",
            "https://youtube.com/feed/subscriptions",
        ],
    )
    def test_rejects_urls_without_a_single_video(self, url):
        with pytest.raises(MediaAcquisitionError):
            extract_video_id(url)

    def test_playlist_links_reduce_to_the_single_video(self):
        """A playlist URL must process only the video it names."""
        video_id = extract_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123&index=4"
        )
        assert canonical_url(video_id) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_canonical_url_discards_everything_the_user_typed(self):
        """Nothing from the user's string may reach the download tool."""
        original = "https://youtube.com/watch?v=dQw4w9WgXcQ&extra=--exec-evil"
        rebuilt = canonical_url(extract_video_id(original))
        assert rebuilt == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert "extra" not in rebuilt and "exec" not in rebuilt

    def test_canonical_url_refuses_an_unvalidated_id(self):
        with pytest.raises(MediaAcquisitionError):
            canonical_url("../../etc/passwd")


class TestFilenameSanitization:
    @pytest.mark.parametrize(
        "raw",
        [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\config",
            "/absolute/path/song.mp3",
            "song\x00.mp3",
        ],
    )
    def test_strips_traversal_and_control_characters(self, raw):
        cleaned = sanitize_filename(raw)
        assert "/" not in cleaned and "\\" not in cleaned
        assert ".." not in cleaned
        assert "\x00" not in cleaned

    def test_never_returns_an_empty_name(self):
        assert sanitize_filename("") == "upload"
        assert sanitize_filename("...") == "upload"

    def test_caps_the_length(self):
        assert len(sanitize_filename("a" * 500 + ".mp3")) <= 120


class TestUploadValidation:
    def test_accepts_ordinary_audio(self):
        UploadedFileSource.validate_metadata("song.mp3", "audio/mpeg")
        UploadedFileSource.validate_metadata("song.wav", "audio/wav")

    @pytest.mark.parametrize(
        "filename",
        ["payload.exe", "script.sh", "page.html", "archive.zip", "noextension"],
    )
    def test_rejects_non_audio_extensions(self, filename):
        with pytest.raises(MediaAcquisitionError):
            UploadedFileSource.validate_metadata(filename, "audio/mpeg")

    def test_rejects_a_mismatched_content_type(self):
        with pytest.raises(MediaAcquisitionError):
            UploadedFileSource.validate_metadata("song.mp3", "text/html")


class TestJobIdValidation:
    """Job ids address directories, so only real UUIDs may pass."""

    def test_accepts_a_uuid(self):
        assert is_valid_job_id("3f2504e0-4f89-41d3-9a0c-0305e82c3301")

    @pytest.mark.parametrize(
        "candidate",
        [
            "../../etc",
            "..",
            ".",
            "a/b",
            "not-a-uuid",
            "",
            "3f2504e0-4f89-41d3-9a0c-0305e82c3301/../..",
        ],
    )
    def test_rejects_anything_else(self, candidate):
        assert not is_valid_job_id(candidate)

    def test_job_dir_refuses_to_escape_the_storage_root(self, tmp_path):
        from app.services.jobs.store import JobStore

        store = JobStore(tmp_path)
        for candidate in ("../escape", "..", "a/../../b"):
            with pytest.raises(ValueError):
                store.job_dir(candidate)


class TestTitleExtraction:
    """Titles are shown to users and written into Info.dat, not used as paths.

    They used to be routed through the filesystem sanitizer, whose ASCII
    allowlist reduced any non-Latin title to nothing — a song called 跳楼机
    arrived as "Untitled".
    """

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("跳楼机 - 爱吃大苹果.mp3", "跳楼机 - 爱吃大苹果"),
            ("Гимн - Артист.mp3", "Гимн - Артист"),
            ("恋 - 星野源.mp3", "恋 - 星野源"),
            ("Übergang.mp3", "Übergang"),
            ("Nightcall - Kavinsky.mp3", "Nightcall - Kavinsky"),
        ],
    )
    def test_titles_survive_in_any_script(self, filename, expected):
        from app.services.media.upload_source import title_from_filename

        assert title_from_filename(filename) == expected

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("01. Artist - Track Name.mp3", "Artist - Track Name"),
            ("y2mate.com - Some Song (Official MV).mp3", "Some Song"),
            ("Song_Name_128kbps.mp3", "Song Name"),
            ("Track [HD].mp3", "Track"),
            ("Track (Official Video).mp3", "Track"),
        ],
    )
    def test_ripper_debris_is_stripped(self, filename, expected):
        from app.services.media.upload_source import title_from_filename

        assert title_from_filename(filename) == expected

    @pytest.mark.parametrize("filename", ["", ".mp3", "   .mp3", "___.mp3"])
    def test_a_title_is_always_produced(self, filename):
        from app.services.media.upload_source import title_from_filename

        assert title_from_filename(filename) == "Untitled"

    def test_paths_never_leak_into_a_title(self):
        from app.services.media.upload_source import title_from_filename

        title = title_from_filename("../../etc/passwd.mp3")
        assert "/" not in title and ".." not in title

    def test_storage_names_stay_ascii_hardened(self):
        """The display title relaxed; the *path* must not have."""
        from app.services.media.upload_source import sanitize_filename

        for raw in ("../../etc/passwd.mp3", "a\\\\b\\\\c.mp3", "song\\x00.mp3"):
            cleaned = sanitize_filename(raw)
            assert "/" not in cleaned and "\\\\" not in cleaned
            assert ".." not in cleaned and "\\x00" not in cleaned
