"""
core/media_tools.py

Конвертация вложений для BotChat средствами ffmpeg:
  * кружок (video_note): Telegram принимает только квадратное mp4 (H.264/AAC), до 60 с, 384×384 —
    любое видео обрезается по центру в квадрат и сжимается;
  * голосовое (voice): Telegram показывает «голосовым» только ogg/opus — запись из браузера (webm) и mp3/m4a перекодируются;
  * gif: слишком тяжёлые анимации сжимаются в mp4 без звука (Telegram всё равно показывает его как GIF).

ffmpeg берётся из PATH, а если его там нет — из пакета imageio-ffmpeg (если установлен). Нет ни того, ни другого —
функции бросают MediaToolError с понятным текстом, и сайт показывает его пользователю (а не молча шлёт «как есть»).
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path


class MediaToolError(Exception):
    pass


_FF = None


def find_ffmpeg():
    global _FF
    if _FF is None:
        _FF = shutil.which('ffmpeg') or ''
        if not _FF:
            try:
                import imageio_ffmpeg
                _FF = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                _FF = ''
    return _FF or None


def available() -> bool:
    return find_ffmpeg() is not None


async def _run(args, timeout=240):
    ff = find_ffmpeg()
    if not ff:
        raise MediaToolError('для этого нужен ffmpeg — установите его (https://ffmpeg.org) и перезапустите юзербот; '
                             'на Windows подойдёт `winget install ffmpeg`, либо `pip install imageio-ffmpeg`')
    proc = await asyncio.create_subprocess_exec(ff, '-hide_banner', '-loglevel', 'error', '-y', *args,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise MediaToolError('конвертация заняла слишком много времени')
    if proc.returncode != 0:
        tail = (err or b'').decode('utf-8', 'replace').strip().splitlines()[-1:] or ['неизвестная ошибка']
        raise MediaToolError('ffmpeg не смог обработать файл: ' + tail[0][:200])


async def _convert(data: bytes, in_ext: str, out_ext: str, args_fn):
    tmp = tempfile.mkdtemp(prefix='frx_media_')
    try:
        src = Path(tmp) / ('in' + (in_ext if in_ext.startswith('.') else '.' + in_ext if in_ext else '.bin'))
        dst = Path(tmp) / ('out' + out_ext)
        await asyncio.to_thread(src.write_bytes, data)
        await _run(args_fn(str(src), str(dst)))
        out = await asyncio.to_thread(dst.read_bytes)
        if not out:
            raise MediaToolError('ffmpeg вернул пустой файл')
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def to_video_note(data: bytes, name: str = 'video.mp4') -> bytes:
    """Любое видео -> квадрат 384×384, H.264/AAC, не длиннее 60 с."""
    ext = os.path.splitext(name)[1] or '.mp4'
    vf = "crop='min(iw,ih)':'min(iw,ih)',scale=384:384,setsar=1,format=yuv420p"
    return await _convert(data, ext, '.mp4', lambda s, d: [
        '-i', s, '-t', '60', '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26',
        '-c:a', 'aac', '-b:a', '96k', '-ac', '1', '-movflags', '+faststart', d])


async def to_voice(data: bytes, name: str = 'voice.webm') -> bytes:
    """Любое аудио -> ogg/opus (формат голосовых сообщений Telegram)."""
    ext = os.path.splitext(name)[1] or '.webm'
    return await _convert(data, ext, '.ogg', lambda s, d: [
        '-i', s, '-vn', '-c:a', 'libopus', '-b:a', '48k', '-ar', '48000', '-ac', '1', '-f', 'ogg', d])


async def gif_to_mp4(data: bytes, name: str = 'a.gif') -> bytes:
    ext = os.path.splitext(name)[1] or '.gif'
    return await _convert(data, ext, '.mp4', lambda s, d: [
        '-i', s, '-vf', "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", '-c:v', 'libx264', '-preset', 'veryfast',
        '-crf', '27', '-an', '-movflags', '+faststart', d])


def is_ogg_opus(data: bytes) -> bool:
    return data[:4] == b'OggS' and b'OpusHead' in data[:128]


def is_mp4_square(data: bytes) -> bool:
    return False          # проверять по заголовку дороже, чем один раз перекодировать — кружки всегда нормализуем
