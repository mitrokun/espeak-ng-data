#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional, Set

from wyoming.info import Attribution, Info, TtsProgram, TtsVoice, TtsVoiceSpeaker
from wyoming.server import AsyncServer, AsyncTcpServer

from . import __version__
from .download import ensure_voice_exists, find_voice, get_voices
from .handler import PiperEventHandler, ENG_AVAILABLE, RUS_AVAILABLE

_LOGGER = logging.getLogger(__name__)

# --- ЦВЕТОВОЕ ФОРМАТИРОВАНИЕ ---
class PiperColorFormatter(logging.Formatter):
    # Ваш темно-зеленый #26A269
    SYNTH_COLOR = "\033[38;2;38;162;105m"
    # Еще более тусклый серый (RGB 80,80,80)
    DIM = "\033[2m"
    RESET = "\033[0m"

    def format(self, record):
        log_message = super().format(record)
        
        # Теперь ищем сокращенное "Synth:"
        if "Synth:" in record.getMessage():
            return f"{self.SYNTH_COLOR}{log_message}{self.RESET}"
        
        return f"{self.DIM}{log_message}{self.RESET}"


def get_bcp47_lang(lang_code: Optional[str]) -> str:
    """Converts a language code to BCP-47 format (e.g., en_US -> en-US)."""
    if not lang_code:
        return ""
    return lang_code.replace("_", "-")


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--voice",
        required=True,
        help="Default Piper voice to use (e.g., en_US-lessac-medium)",
    )
    parser.add_argument("--uri", default="stdio://", help="unix:// or tcp://")
    parser.add_argument(
        "--zeroconf",
        nargs="?",
        const="piper",
        help="Enable discovery over zeroconf with optional name (default: piper)",
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        action="append",
        help="Data directory to check for downloaded models",
    )
    parser.add_argument(
        "--download-dir",
        help="Directory to download voices into (default: first data dir)",
    )
    parser.add_argument(
        "--speaker", type=str, help="Name or id of speaker for default voice"
    )
    parser.add_argument("--noise-scale", type=float, help="Generator noise")
    parser.add_argument("--length-scale", type=float, help="Phoneme length")
    parser.add_argument(
        "--noise-w-scale", "--noise-w", type=float, help="Phoneme width noise"
    )
    parser.add_argument(
        "--sentence-silence",
        type=float,
        default=0.2,
        help="Seconds of silence after each sentence",
    )
    parser.add_argument(
        "--auto-punctuation", default=".?!", help="Automatically add punctuation"
    )
    parser.add_argument(
        "--no-automatic-stress",
        action="store_true",
        help="Disable and do not load the automatic stress placement model",
    )
    parser.add_argument(
        "--no-normalization",
        action="store_true",
        help="Disable English and Russian text normalization",
    )
    parser.add_argument(
        "--yo",
        action="store_true",
        help="Enable e to yo replacement using dictionary",
    )
    parser.add_argument("--samples-per-chunk", type=int, default=1024)
    parser.add_argument(
        "--no-streaming",
        action="store_true",
        help="Disable audio streaming on sentence boundaries",
    )
    parser.add_argument(
        "--update-voices",
        action="store_true",
        help="Download latest voices.json during startup",
    )
    parser.add_argument(
        "--use-cuda",
        action="store_true",
        help="Use CUDA if available (requires onnxruntime-gpu)",
    )
    parser.add_argument(
        "--max-cached-voices",
        type=int,
        default=1,
        help="Maximum number of voices to keep loaded in memory (default: 1)",
    )
    parser.add_argument("--debug", action="store_true", help="Log DEBUG messages")
    parser.add_argument(
        "--log-format", default=logging.BASIC_FORMAT, help="Format for log messages"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="Print version and exit",
    )
    args = parser.parse_args()

    if not args.download_dir:
        args.download_dir = args.data_dir[0]

    # --- НАСТРОЙКА ЛОГИРОВАНИЯ С ЦВЕТАМИ ---
    handler = logging.StreamHandler()
    handler.setFormatter(PiperColorFormatter(args.log_format))
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if args.debug else logging.INFO)
    root_logger.addHandler(handler)
    # ---------------------------------------

    _LOGGER.debug(args)

    logging.getLogger("piper").setLevel(logging.INFO)

    if ENG_AVAILABLE:
        _LOGGER.info("English Normalizer: AVAILABLE")
    else:
        _LOGGER.warning("English Normalizer: NOT FOUND. Install `eng_to_ipa`")

    if RUS_AVAILABLE:
        _LOGGER.info("Russian Normalizer: AVAILABLE")
    else:
        _LOGGER.warning("Russian Normalizer: NOT FOUND. Install `num2words`")

    accentor = None
    if not args.no_automatic_stress:
        _LOGGER.info("Automatic stress enabled. Attempting to load Silero model...")
        try:
            from silero_stress import load_accentor
            accentor = load_accentor()
            _LOGGER.info("Silero Stress model loaded successfully.")
        except Exception:
            _LOGGER.warning(
                "Failed to load Silero Stress model. Automatic stress will be disabled."
            )
            accentor = None
    else:
        _LOGGER.info("Automatic stress is disabled. Silero model will not be loaded.")

    voices_info = get_voices(args.download_dir, update_voices=args.update_voices)
    aliases_info: Dict[str, Any] = {}
    for voice_info in voices_info.values():
        for voice_alias in voice_info.get("aliases", []):
            aliases_info[voice_alias] = {"_is_alias": True, **voice_info}

    voices_info.update(aliases_info)
    voices = [
        TtsVoice(
            name=voice_name,
            description=get_description(voice_info),
            attribution=Attribution(
                name="rhasspy", url="https://github.com/rhasspy/piper"
            ),
            installed=True,
            version=None,
            languages=[
                get_bcp47_lang(
                    voice_info.get("language", {}).get(
                        "code",
                        voice_info.get("espeak", {}).get("voice", voice_name.split("_")[0]),
                    )
                )
            ],
            speakers=(
                [
                    TtsVoiceSpeaker(name=speaker_name)
                    for speaker_name in voice_info["speaker_id_map"]
                ]
                if voice_info.get("speaker_id_map")
                else None
            ),
        )
        for voice_name, voice_info in voices_info.items()
        if not voice_info.get("_is_alias", False)
    ]

    custom_voice_names: Set[str] = set()
    if args.voice not in voices_info:
        custom_voice_names.add(args.voice)

    for data_dir in args.data_dir:
        data_dir = Path(data_dir)
        if not data_dir.is_dir():
            continue

        for onnx_path in data_dir.glob("*.onnx"):
            custom_voice_name = onnx_path.stem
            if custom_voice_name not in voices_info:
                custom_voice_names.add(custom_voice_name)

    for custom_voice_name in custom_voice_names:
        custom_voice_path, custom_config_path = find_voice(
            custom_voice_name, args.data_dir
        )
        with open(custom_config_path, "r", encoding="utf-8") as custom_config_file:
            custom_config = json.load(custom_config_file)
            custom_name = custom_config.get("dataset", custom_voice_path.stem)
            custom_quality = custom_config.get("audio", {}).get("quality")
            if custom_quality:
                description = f"{custom_name} ({custom_quality})"
            else:
                description = custom_name

            lang_code_str = custom_config.get("language", {}).get("code")
            if not lang_code_str:
                lang_code_str = custom_config.get("espeak", {}).get("voice")
                if not lang_code_str:
                    lang_code_str = custom_voice_path.stem.split("_")[0]
            
            lang_code = get_bcp47_lang(lang_code_str)
            voices.append(
                TtsVoice(
                    name=custom_name,
                    description=description,
                    version=None,
                    attribution=Attribution(name="", url=""),
                    installed=True,
                    languages=[lang_code],
                )
            )

    wyoming_info = Info(
        tts=[
            TtsProgram(
                name="piper",
                description="A fast, local, neural text to speech engine",
                attribution=Attribution(
                    name="rhasspy", url="https://github.com/rhasspy/piper"
                ),
                installed=True,
                voices=sorted(voices, key=lambda v: v.name),
                version=__version__,
                supports_synthesize_streaming=(not args.no_streaming),
            )
        ],
    )

    voice_info = voices_info.get(args.voice, {})
    voice_name = voice_info.get("key", args.voice)
    assert voice_name is not None
    ensure_voice_exists(voice_name, args.data_dir, args.download_dir, voices_info)

    server = AsyncServer.from_uri(args.uri)

    if args.zeroconf:
        if not isinstance(server, AsyncTcpServer):
            raise ValueError("Zeroconf requires tcp:// uri")

        from wyoming.zeroconf import HomeAssistantZeroconf

        tcp_server: AsyncTcpServer = server
        hass_zeroconf = HomeAssistantZeroconf(
            name=args.zeroconf, port=tcp_server.port, host=tcp_server.host
        )
        await hass_zeroconf.register_server()
        _LOGGER.debug("Zeroconf discovery enabled")

    _LOGGER.info("Ready")
    await server.run(
        partial(
            PiperEventHandler,
            wyoming_info,
            args,
            voices_info,
            accentor,
        )
    )

def get_description(voice_info: Dict[str, Any]):
    """Get a human readable description for a voice."""
    name = voice_info["name"]
    name = " ".join(name.split("_"))
    quality = voice_info["quality"]
    return f"{name} ({quality})"

def run():
    asyncio.run(main())

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass