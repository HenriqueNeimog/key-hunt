from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast


class ExternalMediaError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrackMetadata:
    video_id: str
    title: str
    artist: str | None
    source_url: str
    thumbnail_url: str | None
    duration_seconds: int | None
    position: int | None


@dataclass(frozen=True, slots=True)
class PlaylistMetadata:
    playlist_id: str
    title: str | None
    tracks: list[TrackMetadata]


class PlaylistMetadataClient(Protocol):
    async def fetch_playlist(self, url: str, max_items: int) -> PlaylistMetadata: ...


class AudioDownloader(Protocol):
    async def download(self, video_id: str, destination_dir: Path) -> Path: ...


class YtDlpClient:
    _video_id = re.compile(r"^[A-Za-z0-9_-]{6,32}$")

    def __init__(
        self,
        *,
        metadata_timeout: int,
        download_timeout: int,
        max_audio_bytes: int,
    ) -> None:
        self._metadata_timeout = metadata_timeout
        self._download_timeout = download_timeout
        self._max_audio_bytes = max_audio_bytes

    @staticmethod
    def _run_sync(arguments: list[str], timeout_seconds: int) -> bytes:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "yt_dlp", *arguments],
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExternalMediaError(
                "media_timeout", "A operação de mídia excedeu o tempo"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").lower()
            code = "playlist_unavailable" if "private" in detail else "media_failed"
            raise ExternalMediaError(code, "Não foi possível acessar esse conteúdo público")
        return completed.stdout

    async def _run(self, arguments: list[str], timeout_seconds: int) -> bytes:
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "yt_dlp",
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except NotImplementedError:
            return await asyncio.to_thread(self._run_sync, arguments, timeout_seconds)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ExternalMediaError(
                "media_timeout", "A operação de mídia excedeu o tempo"
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").lower()
            code = "playlist_unavailable" if "private" in detail else "media_failed"
            raise ExternalMediaError(code, "Não foi possível acessar esse conteúdo público")
        return stdout

    async def fetch_playlist(self, url: str, max_items: int) -> PlaylistMetadata:
        raw = await self._run(
            [
                "--dump-single-json",
                "--flat-playlist",
                "--ignore-errors",
                "--no-warnings",
                "--playlist-end",
                str(max_items),
                "--",
                url,
            ],
            self._metadata_timeout,
        )
        try:
            payload = cast(dict[str, object], json.loads(raw))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ExternalMediaError("invalid_metadata", "Metadados inválidos") from exc
        playlist_id = payload.get("id")
        if not isinstance(playlist_id, str) or not playlist_id:
            raise ExternalMediaError("not_playlist", "A URL não representa uma playlist suportada")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ExternalMediaError("empty_playlist", "A playlist está vazia ou indisponível")
        tracks: list[TrackMetadata] = []
        for index, raw_entry in enumerate(entries, start=1):
            if not isinstance(raw_entry, dict):
                continue
            entry = cast(dict[str, object], raw_entry)
            video_id = entry.get("id")
            title = entry.get("title")
            duration = entry.get("duration")
            live_status = entry.get("live_status")
            availability = entry.get("availability")
            if (
                not isinstance(video_id, str)
                or not self._video_id.fullmatch(video_id)
                or not isinstance(title, str)
                or live_status in {"is_live", "is_upcoming"}
                or availability in {"private", "subscriber_only", "needs_auth"}
            ):
                continue
            duration_seconds = int(duration) if isinstance(duration, (int, float)) else None
            artist_raw = entry.get("channel") or entry.get("uploader")
            thumb_raw = entry.get("thumbnail")
            position_raw = entry.get("playlist_index")
            tracks.append(
                TrackMetadata(
                    video_id=video_id,
                    title=title[:512],
                    artist=artist_raw[:512] if isinstance(artist_raw, str) else None,
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    thumbnail_url=thumb_raw if isinstance(thumb_raw, str) else None,
                    duration_seconds=duration_seconds,
                    position=int(position_raw) if isinstance(position_raw, (int, float)) else index,
                )
            )
        if not tracks:
            raise ExternalMediaError(
                "empty_playlist", "Nenhuma faixa pública válida foi encontrada"
            )
        title = payload.get("title")
        return PlaylistMetadata(
            playlist_id=playlist_id,
            title=title[:512] if isinstance(title, str) else None,
            tracks=tracks,
        )

    @staticmethod
    def _find_downloaded_audio(destination_dir: Path, video_id: str) -> Path | None:
        matches = sorted(
            path
            for path in destination_dir.iterdir()
            if path.is_file() and path.name.startswith(f"{video_id}.")
        )
        for path in matches:
            if path.suffix.lower() == ".mp3":
                return path
        return matches[0] if matches else None

    @staticmethod
    def _convert_to_mp3(source: Path, destination: Path) -> None:
        if source == destination:
            return
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-acodec",
                "libmp3lame",
                "-q:a",
                "2",
                str(destination),
            ],
            check=True,
            capture_output=True,
            timeout=180,
            shell=False,
        )

    async def download(self, video_id: str, destination_dir: Path) -> Path:
        if not self._video_id.fullmatch(video_id):
            raise ExternalMediaError("invalid_video_id", "Identificador de vídeo inválido")
        await asyncio.to_thread(destination_dir.mkdir, parents=True, exist_ok=True)
        template = destination_dir / f"{video_id}.%(ext)s"
        await self._run(
            [
                "--no-playlist",
                "--no-warnings",
                "--extract-audio",
                "--audio-format",
                "mp3",
                "--audio-quality",
                "3",
                "--max-filesize",
                str(self._max_audio_bytes),
                "--output",
                str(template),
                "--",
                f"https://www.youtube.com/watch?v={video_id}",
            ],
            self._download_timeout,
        )
        downloaded = await asyncio.to_thread(self._find_downloaded_audio, destination_dir, video_id)
        if downloaded is None:
            raise ExternalMediaError("audio_missing", "O áudio não foi gerado")
        result = destination_dir / f"{video_id}.mp3"
        if downloaded.suffix.lower() != ".mp3":
            try:
                await asyncio.to_thread(self._convert_to_mp3, downloaded, result)
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise ExternalMediaError(
                    "audio_conversion_failed",
                    "Não foi possível converter o áudio para MP3",
                ) from exc
        elif downloaded != result:
            await asyncio.to_thread(downloaded.rename, result)
        exists, result_size = await asyncio.to_thread(self._file_facts, result)
        if not exists or result_size <= 0:
            raise ExternalMediaError("audio_missing", "O áudio não foi gerado")
        if result_size > self._max_audio_bytes:
            await asyncio.to_thread(result.unlink, missing_ok=True)
            raise ExternalMediaError("audio_too_large", "O áudio excede o limite configurado")
        return result

    @staticmethod
    def _file_facts(path: Path) -> tuple[bool, int]:
        if not path.is_file():
            return False, 0
        return True, path.stat().st_size
