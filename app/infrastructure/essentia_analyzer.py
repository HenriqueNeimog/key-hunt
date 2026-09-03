from __future__ import annotations

import importlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from app.domain.enums import MusicalScale


class AnalysisError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KeyAnalysis:
    key: str
    scale: MusicalScale
    confidence: float | None


class KeyAnalyzer(Protocol):
    def analyze(self, audio_path: Path) -> KeyAnalysis: ...


class EssentiaKeyAnalyzer:
    _valid_key = re.compile(r"^(?:[A-G](?:#|b)?)$")

    def __init__(self, ffmpeg_binary: str, timeout_seconds: int = 180) -> None:
        self._ffmpeg_binary = ffmpeg_binary
        self._timeout_seconds = timeout_seconds

    def analyze(self, audio_path: Path) -> KeyAnalysis:
        try:
            standard = importlib.import_module("essentia.standard")
        except ImportError as exc:
            raise AnalysisError("Essentia não está instalada") from exc
        with tempfile.TemporaryDirectory(prefix="key-hunt-") as temp_dir:
            wav_path = Path(temp_dir) / "analysis.wav"
            command = [
                self._ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "44100",
                str(wav_path),
            ]
            try:
                subprocess.run(  # noqa: S603 - fixed executable and argument list, never shell
                    command,
                    check=True,
                    capture_output=True,
                    timeout=self._timeout_seconds,
                    shell=False,
                )
            except FileNotFoundError as exc:
                raise AnalysisError("FFmpeg não encontrado") from exc
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise AnalysisError("Falha ao converter o áudio") from exc
            try:
                loader = standard.MonoLoader(filename=str(wav_path), sampleRate=44100)
                extractor = standard.KeyExtractor(sampleRate=44100)
                key_raw, scale_raw, strength_raw = extractor(loader())
            except Exception as exc:  # third-party boundary
                raise AnalysisError("Falha ao analisar o tom") from exc
        key = str(key_raw)
        if not self._valid_key.fullmatch(key):
            raise AnalysisError("Tom retornado pelo analisador é inválido")
        normalized_scale = str(scale_raw).lower()
        scale = (
            MusicalScale(normalized_scale)
            if normalized_scale in {"major", "minor"}
            else MusicalScale.UNKNOWN
        )
        strength = cast(float, strength_raw)
        confidence = max(0.0, min(float(strength), 1.0))
        return KeyAnalysis(key=key, scale=scale, confidence=confidence)
