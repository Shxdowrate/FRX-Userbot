"""
core/media_tools.py — `import media` в модулях.

Общий медиакод FRX (ffmpeg + Pillow): BotChat и любые модули (`import media`).
  media.image_to_sticker(data) / image_to_emoji(data)   — картинка по правилам Telegram (512 / 100×100, webp)
  media.video_to_sticker(data, emoji=False)             — видеостикер/видео-эмодзи: webm VP9, ≤3 с, ≤30 к/с, ≤256 КБ
  media.to_video_note / to_voice / to_mp3 / to_gif / extract_audio / compress_video / trim / reverse / speed
  media.probe(data, name)                               — длительность, размеры, есть ли звук/видео

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
        if not _FF:                       # ffmpeg, скачанный FRX (frx_deps.py, user/data/bin/ffmpeg)
            try:
                from .paths import DATA_DIR
                cand = Path(DATA_DIR) / 'bin' / 'ffmpeg' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
                if cand.is_file():
                    _FF = str(cand)
            except Exception:
                pass
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


async def _convert(data: bytes, in_ext: str, out_ext: str, args_fn, timeout: int = 240):
    tmp = tempfile.mkdtemp(prefix='frx_media_')
    try:
        src = Path(tmp) / ('in' + (in_ext if in_ext.startswith('.') else '.' + in_ext if in_ext else '.bin'))
        dst = Path(tmp) / ('out' + out_ext)
        await asyncio.to_thread(src.write_bytes, data)
        await _run(args_fn(str(src), str(dst)), timeout)
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



# ===================================================================== #
# Для модулей: стикеры, эмодзи, аудио, видео
# ===================================================================== #

import re as _re

STICKER_MAX = 512
EMOJI_SIZE = 100
VIDEO_STICKER_MAX_BYTES = 256 * 1024


async def probe(data: bytes, name: str = 'in.bin') -> dict:
    """{'duration': сек, 'width', 'height', 'has_video', 'has_audio', 'fps'} — разбор вывода `ffmpeg -i` (ffprobe не нужен)."""
    ff = find_ffmpeg()
    if not ff:
        raise MediaToolError('нужен ffmpeg')
    tmp = tempfile.mkdtemp(prefix='frx_probe_')
    try:
        src = Path(tmp) / ('in' + (os.path.splitext(name)[1] or '.bin'))
        await asyncio.to_thread(src.write_bytes, data)
        proc = await asyncio.create_subprocess_exec(ff, '-hide_banner', '-i', str(src), stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), 30)
        text = (err or b'').decode('utf-8', 'replace')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = {'duration': 0.0, 'width': 0, 'height': 0, 'has_video': False, 'has_audio': False, 'fps': 0.0}
    m = _re.search(r'Duration: (\d+):(\d+):([\d.]+)', text)
    if m:
        out['duration'] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in text.splitlines():
        if 'Video:' in line:
            out['has_video'] = True
            m = _re.search(r'(\d{2,5})x(\d{2,5})', line)
            if m and not out['width']:
                out['width'], out['height'] = int(m.group(1)), int(m.group(2))
            m = _re.search(r'([\d.]+) fps', line)
            if m:
                out['fps'] = float(m.group(1))
        elif 'Audio:' in line:
            out['has_audio'] = True
    return out


def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        raise MediaToolError('нужен Pillow: pip install Pillow')


def _fit_image(data: bytes, box: int, square: bool):
    Image = _pil()
    import io
    im = Image.open(io.BytesIO(data))
    try:
        im.seek(0)
    except Exception:
        pass
    im = im.convert('RGBA')
    w, h = im.size
    k = box / max(w, h)
    im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    if square:
        canvas = Image.new('RGBA', (box, box), (0, 0, 0, 0))
        canvas.paste(im, ((box - im.width) // 2, (box - im.height) // 2), im)
        im = canvas
    return im


def _webp(im, limit=512 * 1024) -> bytes:
    import io
    for q in (95, 90, 80, 70, 60, 50, 40):
        buf = io.BytesIO()
        im.save(buf, 'WEBP', quality=q, method=6)
        if buf.tell() <= limit:
            return buf.getvalue()
    return buf.getvalue()


async def image_to_sticker(data: bytes) -> bytes:
    """Картинка → статичный стикер: webp, большая сторона ровно 512 px (так требует Telegram), прозрачность сохраняется."""
    return await asyncio.to_thread(lambda: _webp(_fit_image(data, STICKER_MAX, False)))


async def image_to_emoji(data: bytes) -> bytes:
    """Картинка → эмодзи: 100×100 webp, вписано по центру на прозрачном фоне."""
    return await asyncio.to_thread(lambda: _webp(_fit_image(data, EMOJI_SIZE, True), 128 * 1024))


async def video_to_sticker(data: bytes, name: str = 'in.mp4', emoji: bool = False, max_seconds: float = 3.0) -> bytes:
    """Видео/GIF/анимация → видеостикер (webm VP9, без звука, ≤3 с, ≤30 к/с, сторона 512) или видео-эмодзи (100×100).
    Подбирает битрейт, пока файл не станет ≤256 КБ."""
    ext = os.path.splitext(name)[1] or '.mp4'
    if emoji:
        vf = (f"scale={EMOJI_SIZE}:{EMOJI_SIZE}:force_original_aspect_ratio=decrease,"
              f"pad={EMOJI_SIZE}:{EMOJI_SIZE}:(ow-iw)/2:(oh-ih)/2:color=black@0,fps=30,format=yuva420p")
    else:
        vf = (f"scale='if(gte(iw,ih),{STICKER_MAX},-2)':'if(gte(iw,ih),-2,{STICKER_MAX})',fps=30,format=yuva420p")
    last = None
    dec = ['-c:v', 'libvpx-vp9'] if ext.lower() == '.webm' else []     # иначе прозрачность webm-стикеров теряется
    for kbps in (900, 600, 420, 300, 200, 140, 100, 70):
        if dec and kbps == 900:
            try:
                await _convert(data, ext, '.png', lambda s, d: dec + ['-i', s, '-frames:v', '1', d], timeout=30)
            except Exception:
                dec = []                                    # не VP9 (VP8/AV1) — обычный декодер
        out = await _convert(data, ext, '.webm', lambda s, d, kbps=kbps, dec=dec: dec + [
            '-i', s, '-t', str(max_seconds), '-an', '-vf', vf, '-c:v', 'libvpx-vp9', '-b:v', f'{kbps}k',
            '-minrate', '0', '-maxrate', f'{int(kbps * 1.4)}k', '-crf', '34', '-deadline', 'good', '-row-mt', '1',
            '-pix_fmt', 'yuva420p', d])
        last = out
        if len(out) <= VIDEO_STICKER_MAX_BYTES:
            return out
    if last and len(last) <= VIDEO_STICKER_MAX_BYTES * 1.05:
        return last
    raise MediaToolError('не получилось ужать видео до 256 КБ — возьмите отрывок покороче')


async def to_mp3(data: bytes, name: str = 'in.bin', kbps: int = 192) -> bytes:
    """Любое аудио или видео → mp3."""
    ext = os.path.splitext(name)[1] or '.bin'
    return await _convert(data, ext, '.mp3', lambda s, d: ['-i', s, '-vn', '-c:a', 'libmp3lame', '-b:a', f'{kbps}k', d])


extract_audio = to_mp3


async def to_gif(data: bytes, name: str = 'in.mp4', max_seconds: float = 60, width: int = 480) -> bytes:
    """Видео → «GIF» Telegram (mp4 без звука, зацикливается)."""
    ext = os.path.splitext(name)[1] or '.mp4'
    return await _convert(data, ext, '.mp4', lambda s, d: [
        '-i', s, '-t', str(max_seconds), '-an', '-vf', f"scale='min({width},iw)':-2,format=yuv420p", '-c:v', 'libx264',
        '-preset', 'veryfast', '-crf', '27', '-movflags', '+faststart', d])


async def compress_video(data: bytes, name: str = 'in.mp4', target_mb: float = 8.0, max_height: int = 720) -> bytes:
    """Сжать видео примерно до target_mb (битрейт считается от длительности)."""
    info = await probe(data, name)
    dur = max(1.0, info['duration'] or 1.0)
    total_kbps = max(150, int(target_mb * 8192 / dur * 0.92))
    a_kbps = 64 if info['has_audio'] else 0
    v_kbps = max(100, total_kbps - a_kbps)
    ext = os.path.splitext(name)[1] or '.mp4'
    return await _convert(data, ext, '.mp4', lambda s, d: [
        '-i', s, '-vf', f"scale=-2:'min({max_height},ih)',format=yuv420p", '-c:v', 'libx264', '-preset', 'veryfast',
        '-b:v', f'{v_kbps}k', '-maxrate', f'{int(v_kbps * 1.3)}k', '-bufsize', f'{v_kbps * 2}k',
        *(['-c:a', 'aac', '-b:a', '64k'] if a_kbps else ['-an']), '-movflags', '+faststart', d], timeout=900)


async def trim(data: bytes, name: str, start: float, end: float = None) -> bytes:
    """Вырезать кусок [start; end] (секунды) без потери формата."""
    ext = os.path.splitext(name)[1] or '.mp4'
    args = ['-ss', str(max(0.0, start))] + (['-to', str(end)] if end else [])
    return await _convert(data, ext, ext, lambda s, d: [*args, '-i', s, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                                                        '-c:a', 'aac', '-movflags', '+faststart', d] if ext in ('.mp4', '.mov', '.mkv', '.webm')
                          else [*args, '-i', s, d])


async def reverse(data: bytes, name: str = 'in.mp4') -> bytes:
    ext = os.path.splitext(name)[1] or '.mp4'
    info = await probe(data, name)
    if info['duration'] > 60:
        raise MediaToolError('перевернуть можно видео до минуты')
    return await _convert(data, ext, '.mp4', lambda s, d: ['-i', s, '-vf', 'reverse,format=yuv420p',
                                                          *(['-af', 'areverse'] if info['has_audio'] else ['-an']),
                                                          '-c:v', 'libx264', '-preset', 'veryfast', '-movflags', '+faststart', d])


async def speed(data: bytes, name: str = 'in.mp4', factor: float = 2.0) -> bytes:
    factor = max(0.25, min(4.0, float(factor)))
    ext = os.path.splitext(name)[1] or '.mp4'
    info = await probe(data, name)
    atempo, f = [], factor
    while f > 2.0:
        atempo.append('atempo=2.0')
        f /= 2.0
    while f < 0.5:
        atempo.append('atempo=0.5')
        f /= 0.5
    atempo.append(f'atempo={f:.4f}')
    if not info['has_video']:
        return await _convert(data, ext, '.mp3', lambda s, d: ['-i', s, '-af', ','.join(atempo), d])
    return await _convert(data, ext, '.mp4', lambda s, d: ['-i', s, '-vf', f'setpts=PTS/{factor},format=yuv420p',
                                                          *(['-af', ','.join(atempo)] if info['has_audio'] else ['-an']),
                                                          '-c:v', 'libx264', '-preset', 'veryfast', '-movflags', '+faststart', d])


# ===================================================================== #
# Стикерпаки и эмодзи-паки (через стационарного бота, Bot API)
# ===================================================================== #

PACK_LIMIT = {'regular': 120, 'custom_emoji': 200}


async def pack_add(bot, user_id: int, base: str, title: str, data: bytes, fmt: str = 'static', emojis=('💬',),
                   kind: str = 'regular', filename: str = None) -> dict:
    """Добавить стикер/эмодзи в пак пользователя (паки создаёт бот, их владелец — user_id).
    base — латиница/цифры/_, к имени добавится `_by_<бот>`; если пак заполнен — заводится следующий (`base_2`…).
    fmt: 'static' (webp/png) | 'video' (webm) | 'animated' (tgs). kind: 'regular' | 'custom_emoji'.
    → {'name', 'title', 'link', 'created': bool}"""
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import BufferedInputFile, InputSticker
    import re as re_
    me = await bot.get_me()
    base = re_.sub(r'[^A-Za-z0-9_]', '_', base).strip('_') or 'pack'
    if not base[0].isalpha():
        base = 'p' + base
    suffix = f"_by_{me.username}"
    ext = {'static': 'webp', 'video': 'webm', 'animated': 'tgs'}.get(fmt, 'webp')
    sticker = InputSticker(sticker=BufferedInputFile(data, filename or f"s.{ext}"), format=fmt,
                           emoji_list=list(emojis or ('💬',))[:20])
    limit = PACK_LIMIT.get(kind, 120)
    for n in range(1, 60):
        name = (f"{base}_{n}" if n > 1 else base)[:64 - len(suffix)] + suffix
        ptitle = (f"{title} {n}" if n > 1 else title)[:64]
        try:
            st = await bot.get_sticker_set(name)
        except TelegramBadRequest:
            await bot.create_new_sticker_set(user_id=user_id, name=name, title=ptitle, stickers=[sticker],
                                             sticker_type=kind)
            return {'name': name, 'title': ptitle, 'created': True, 'link': _pack_link(name, kind)}
        if len(st.stickers) >= limit:
            continue
        await bot.add_sticker_to_set(user_id=user_id, name=name, sticker=sticker)
        return {'name': name, 'title': st.title, 'created': False, 'link': _pack_link(name, kind)}
    raise MediaToolError('слишком много заполненных паков')


def _pack_link(name, kind):
    return f"https://t.me/{'addemoji' if kind == 'custom_emoji' else 'addstickers'}/{name}"


async def pack_last_document(client, name: str):
    """Последний стикер пака как документ Telethon — чтобы юзербот отправил именно стикер из пака."""
    from telethon.tl.functions.messages import GetStickerSetRequest
    from telethon.tl.types import InputStickerSetShortName
    res = await client(GetStickerSetRequest(stickerset=InputStickerSetShortName(short_name=name), hash=0))
    return res.documents[-1] if res.documents else None
