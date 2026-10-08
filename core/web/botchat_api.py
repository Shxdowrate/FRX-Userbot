"""
core/web/botchat_api.py

HTTP-слой вкладки BotChat (`#/botchat`): всё идёт через WebPanel.api() — те же проверки Host/Origin/X-FRX.
Логика и хранилище — в core/botchat.py. Здесь: чтение (чаты, история, поиск, инфо), живой опрос,
отправка от имени бота (текст, файлы, альбомы, опросы, кубик, геопозиция, контакт), правка, удаление,
пересылка/копирование, реакции, закрепление, участники, файлы и аватарки.
"""

import asyncio
import time
from pathlib import Path

from aiohttp import web

from ..botchat import md_to_html, chat_meta, ser_message

POLL_WAIT = 25
UPLOAD_TIMEOUT = 900
UPLOAD_KINDS = {'photo', 'video', 'audio', 'voice', 'animation', 'video_note', 'document'}
REACTIONS = ['👍', '👎', '❤', '🔥', '🥰', '👏', '😁', '🤔', '🤯', '😱', '😢', '🎉', '🤩', '🙏', '👌', '😍',
             '💯', '🤣', '⚡', '🏆', '💔', '😐', '👀', '🤝', '🫡', '🤗', '😎', '🤡', '💩', '🥱', '😭', '👻']


def register(panel, router):
    from .server import ApiError                      # server.py импортирует нас лениво — цикла нет
    api = BotChatApi(panel, ApiError)
    r = router
    r.add_get('/api/botchat/state', panel.api(api.state))
    r.add_get('/api/botchat/messages', panel.api(api.messages))
    r.add_get('/api/botchat/search', panel.api(api.search))
    r.add_get('/api/botchat/info', panel.api(api.info))
    r.add_get('/api/botchat/poll', panel.api(api.poll))
    r.add_get('/api/botchat/myprofile', panel.api(api.myprofile))
    # файлы грузятся тегами <img>/<video>/<audio>, которые не умеют слать заголовок X-FRX, поэтому лежат вне /api/;
    # чужой сайт их всё равно не прочитает (нет CORS), а Sec-Fetch-Site отсекает и «слепую» подгрузку с чужих страниц
    r.add_get('/botchat/media', api.media)
    r.add_get('/botchat/avatar', api.avatar)
    for name in ('send', 'edit', 'delete', 'forward', 'react', 'pin', 'chatpin', 'read', 'action', 'open',
                 'forget', 'leave', 'member', 'rename', 'describe', 'poll_create', 'poll_stop', 'dice',
                 'location', 'contact', 'clear', 'cross', 'myprofile_set', 'mycommands_set', 'myphoto', 'myphoto_remove'):
        r.add_post(f'/api/botchat/{name}', panel.api(getattr(api, name)))
    r.add_post('/api/botchat/upload', panel.api(api.upload))
    return api


def _ffmpeg():
    from .. import media_tools
    return media_tools.available()


def _old_of(new_id):
    from ..botchat import _old_group_id
    return _old_group_id(int(new_id))


class BotChatApi:
    def __init__(self, panel, api_error):
        self.panel = panel
        self.Err = api_error

    # ----------------------------------------------------------- общее #

    @property
    def bc(self):
        return self.panel.core.botchat

    def _need_bot(self):
        if not self.bc.available:
            raise self.Err('стационарный бот не запущен — задайте aiogram_token (user/config.json) или создайте бота', 409)
        return self.bc.bot

    @staticmethod
    def _int(v, name='chat'):
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ValueError(f'{name}: нужно число')

    def _chat(self, d, key='chat'):
        try:
            return self._int(d.get(key), key)
        except ValueError as e:
            raise self.Err(str(e))

    async def _tg(self, coro):
        """Ошибки Telegram -> понятное сообщение для сайта."""
        try:
            return await coro
        except self.Err:
            raise
        except Exception as e:
            new = getattr(e, 'migrate_to_chat_id', None)
            if new:                                               # группа стала супергруппой: чат на сайте — тот же, id другой
                try:
                    self.bc.migrate_chat(_old_of(new), int(new))
                except Exception:
                    pass
                raise self.Err('группа стала супергруппой — чат на сайте обновлён, повторите действие', 409)
            msg = getattr(e, 'message', None) or str(e)
            if 'Bad Request:' in msg:
                msg = msg.split('Bad Request:', 1)[1].strip()
            elif 'Forbidden:' in msg:
                msg = msg.split('Forbidden:', 1)[1].strip()
            raise self.Err(f'Telegram: {msg}', 400)

    @staticmethod
    def _reply(reply_to):
        if not reply_to:
            return None
        from aiogram.types import ReplyParameters
        return ReplyParameters(message_id=int(reply_to), allow_sending_without_reply=True)

    # ---------------------------------------------------------- чтение #

    async def state(self, request):
        bc = self.bc
        me = None
        if bc.available:
            try:
                u = await bc.me()
                me = {'id': u.id, 'name': u.first_name, 'username': u.username}
            except Exception:
                pass
        return {'available': bc.available, 'me': me, 'chats': bc.chat_list(), 'ver': bc.ver,
                'stats': bc.stats(), 'reactions': REACTIONS,
                'enabled': bool(await bc._cfg('enabled', True)), 'ffmpeg': _ffmpeg()}

    async def messages(self, request):
        q = request.query
        cid = self._int(q.get('chat'))
        before = int(q['before']) if q.get('before') else None
        limit = max(1, min(200, int(q.get('limit') or 60)))
        msgs, more = self.bc.page(cid, before, limit)
        return {'chat': self.bc.get_chat_meta(cid), 'messages': msgs, 'more': more, 'ver': self.bc.ver}

    async def search(self, request):
        q = request.query
        return {'messages': self.bc.search(self._int(q.get('chat')), q.get('q') or '')}

    async def info(self, request):
        """Живые данные чата: описание, число участников, админы, закреплённое сообщение."""
        bot = self._need_bot()
        cid = self._int(request.query.get('chat'))
        chat = await self._tg(bot.get_chat(cid))
        out = {'id': cid, 'type': chat.type, 'title': getattr(chat, 'title', None), 'username': chat.username,
               'first_name': chat.first_name, 'last_name': chat.last_name,
               'description': getattr(chat, 'description', None) or getattr(chat, 'bio', None),
               'invite_link': getattr(chat, 'invite_link', None), 'members': None, 'admins': [], 'pinned': None,
               'can_manage': False}
        if chat.pinned_message:
            out['pinned'] = ser_message(chat.pinned_message, self.bc._me_id())
        if chat.type != 'private':
            try:
                out['members'] = await bot.get_chat_member_count(cid)
            except Exception:
                pass
            try:
                admins = await bot.get_chat_administrators(cid)
                out['admins'] = [{'id': a.user.id, 'name': ' '.join(x for x in (a.user.first_name, a.user.last_name) if x),
                                  'username': a.user.username, 'status': a.status,
                                  'title': getattr(a, 'custom_title', None)} for a in admins]
                me = await self.bc.me()
                out['can_manage'] = any(a['id'] == me.id for a in out['admins'])
            except Exception:
                pass
        return out

    async def poll(self, request):
        since = int(request.query.get('since') or 0)
        res = await self.bc.wait(since, POLL_WAIT)
        if res['events']:
            self.panel.touch()               # живая переписка — не простой: сайт не должен гаснуть посреди диалога
        return res

    # ------------------------------------------------------- отправка #

    async def send(self, request):
        d = await self.panel._json(request)
        cid, text = self._chat(d), (d.get('text') or '').strip()
        if not text:
            raise self.Err('пустое сообщение')
        bot = self._need_bot()
        kw = dict(reply_parameters=self._reply(d.get('reply_to')), disable_notification=bool(d.get('silent')))
        if d.get('no_preview'):
            from aiogram.types import LinkPreviewOptions
            kw['link_preview_options'] = LinkPreviewOptions(is_disabled=True)
        try:
            await self._tg(bot.send_message(cid, md_to_html(text), **kw))
        except self.Err as e:
            if "parse entities" not in e.message:
                raise
            await self._tg(bot.send_message(cid, text, parse_mode=None, **kw))      # разметка не разобралась — как есть

    async def upload(self, request):
        from aiogram.types import BufferedInputFile, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
        bot = self._need_bot()
        form = await request.post()
        cid = self._int(form.get('chat'))
        caption = (form.get('text') or '').strip()
        reply = self._reply(form.get('reply_to'))
        silent = form.get('silent') == '1'
        spoiler = form.get('spoiler') == '1'
        kinds = [k for k in form.getall('kind', [])]
        files = []
        for f in form.getall('file', []):
            if hasattr(f, 'file'):
                files.append((f.filename or 'file', f.file.read()))
        if not files:
            raise self.Err('нет файла')
        auto = []
        for i, (name, data) in enumerate(files):
            k = kinds[i] if i < len(kinds) else 'auto'
            if k == 'auto' or k not in UPLOAD_KINDS:
                low = name.lower()
                k = ('photo' if low.endswith(('.jpg', '.jpeg', '.png', '.webp')) else
                     'animation' if low.endswith('.gif') else
                     'video' if low.endswith(('.mp4', '.mov', '.mkv', '.webm', '.m4v')) else
                     'audio' if low.endswith(('.mp3', '.m4a', '.flac', '.wav')) else
                     'voice' if low.endswith(('.ogg', '.oga', '.opus')) else 'document')
            auto.append(k)
        cap = md_to_html(caption) if caption else None
        base = dict(reply_parameters=reply, disable_notification=silent)

        if len(files) > 1 and all(k in ('photo', 'video') for k in auto) or \
                len(files) > 1 and len(set(auto)) == 1 and auto[0] in ('document', 'audio'):
            media = []
            for i, ((name, data), k) in enumerate(zip(files, auto)):
                f = BufferedInputFile(data, filename=name)
                kw = {'caption': cap} if i == 0 and cap else {}
                if k == 'photo':
                    media.append(InputMediaPhoto(media=f, has_spoiler=spoiler or None, **kw))
                elif k == 'video':
                    media.append(InputMediaVideo(media=f, has_spoiler=spoiler or None, **kw))
                elif k == 'audio':
                    media.append(InputMediaAudio(media=f, **kw))
                else:
                    media.append(InputMediaDocument(media=f, **kw))
            for part in range(0, len(media), 10):
                await self._tg(bot.send_media_group(cid, media[part:part + 10], request_timeout=UPLOAD_TIMEOUT, **base))
            return None

        from .. import media_tools as mt
        T = {'request_timeout': UPLOAD_TIMEOUT}                # большие файлы и конвертация дольше стандартных 60 с aiogram

        async def conv(coro):
            try:
                return await coro
            except mt.MediaToolError as e:
                raise self.Err(str(e), 400)

        for i, ((name, data), k) in enumerate(zip(files, auto)):
            kw = dict(base)
            c = cap if i == 0 else None
            if k == 'video_note':                              # любое видео -> квадратный кружок 384×384
                data, name = await conv(mt.to_video_note(data, name)), 'circle.mp4'
            elif k == 'voice' and not mt.is_ogg_opus(data):    # запись из браузера (webm), mp3, m4a -> ogg/opus
                data, name = await conv(mt.to_voice(data, name)), 'voice.ogg'
            f = BufferedInputFile(data, filename=name)
            if k == 'photo':
                await self._tg(bot.send_photo(cid, f, caption=c, has_spoiler=spoiler or None, **kw, **T))
            elif k == 'video':
                await self._tg(bot.send_video(cid, f, caption=c, has_spoiler=spoiler or None, supports_streaming=True, **kw, **T))
            elif k == 'animation':
                await self._send_animation(bot, cid, name, data, c, spoiler, kw, T, mt)
            elif k == 'audio':
                await self._tg(bot.send_audio(cid, f, caption=c, **kw, **T))
            elif k == 'voice':
                await self._tg(bot.send_voice(cid, f, caption=c, **kw, **T))
            elif k == 'video_note':
                await self._tg(bot.send_video_note(cid, f, length=384, **kw, **T))
            else:
                await self._tg(bot.send_document(cid, f, caption=c, disable_content_type_detection=True, **kw, **T))

    async def _send_animation(self, bot, cid, name, data, caption, spoiler, kw, T, mt):
        """GIF: сперва как есть; если Telegram не принял (слишком тяжёлый/странный) — перекодируем в mp4, в крайнем случае файлом."""
        from aiogram.types import BufferedInputFile
        try:
            return await self._tg(bot.send_animation(cid, BufferedInputFile(data, filename=name), caption=caption,
                                                     has_spoiler=spoiler or None, **kw, **T))
        except self.Err as first:
            err = first
        if mt.available():
            try:
                mp4 = await mt.gif_to_mp4(data, name)
                return await self._tg(bot.send_animation(cid, BufferedInputFile(mp4, filename='animation.mp4'), caption=caption,
                                                         has_spoiler=spoiler or None, **kw, **T))
            except (mt.MediaToolError, self.Err) as e:
                err = e if isinstance(e, self.Err) else err
        try:
            return await self._tg(bot.send_document(cid, BufferedInputFile(data, filename=name), caption=caption, **kw, **T))
        except self.Err:
            raise err

    async def dice(self, request):
        d = await self.panel._json(request)
        emoji = d.get('emoji') if d.get('emoji') in ('🎲', '🎯', '🏀', '⚽', '🎳', '🎰') else '🎲'
        await self._tg(self._need_bot().send_dice(self._chat(d), emoji=emoji, reply_parameters=self._reply(d.get('reply_to'))))

    async def location(self, request):
        d = await self.panel._json(request)
        try:
            lat, lon = float(d.get('lat')), float(d.get('lon'))
        except (TypeError, ValueError):
            raise self.Err('широта и долгота — числа')
        await self._tg(self._need_bot().send_location(self._chat(d), lat, lon, reply_parameters=self._reply(d.get('reply_to'))))

    async def contact(self, request):
        d = await self.panel._json(request)
        phone, first = str(d.get('phone') or '').strip(), str(d.get('first_name') or '').strip()
        if not phone or not first:
            raise self.Err('нужны номер и имя')
        await self._tg(self._need_bot().send_contact(self._chat(d), phone, first, last_name=(d.get('last_name') or None),
                                                     reply_parameters=self._reply(d.get('reply_to'))))

    async def poll_create(self, request):
        d = await self.panel._json(request)
        q = str(d.get('question') or '').strip()
        opts = [str(o).strip() for o in (d.get('options') or []) if str(o).strip()]
        if not q or len(opts) < 2:
            raise self.Err('нужен вопрос и минимум два варианта')
        kw = dict(is_anonymous=bool(d.get('anonymous', True)), reply_parameters=self._reply(d.get('reply_to')))
        if d.get('quiz'):
            try:
                kw.update(type='quiz', correct_option_id=int(d.get('correct', 0)))
            except (TypeError, ValueError):
                raise self.Err('правильный вариант — число')
            if d.get('explanation'):
                kw['explanation'] = str(d['explanation'])[:200]
        else:
            kw['allows_multiple_answers'] = bool(d.get('multiple'))
        await self._tg(self._need_bot().send_poll(self._chat(d), q[:300], opts[:10], **kw))

    async def poll_stop(self, request):
        d = await self.panel._json(request)
        cid, mid = self._chat(d), self._int(d.get('id'), 'id')
        poll = await self._tg(self._need_bot().stop_poll(cid, mid))
        self.bc.set_poll(cid, mid, poll)

    # ------------------------------------------- действия над сообщениями #

    async def edit(self, request):
        d = await self.panel._json(request)
        cid, mid = self._chat(d), self._int(d.get('id'), 'id')
        text = (d.get('text') or '').strip()
        msg = self.bc.message(cid, mid)
        if not text and not (msg and msg.get('media')):
            raise self.Err('пустой текст')
        bot = self._need_bot()
        has_media = bool(msg and msg.get('media') and msg['media']['kind'] not in ('poll', 'dice', 'location', 'contact'))
        body = md_to_html(text) if text else ''
        try:
            if has_media:
                await self._tg(bot.edit_message_caption(chat_id=cid, message_id=mid, caption=body))
            else:
                await self._tg(bot.edit_message_text(body, chat_id=cid, message_id=mid))
        except self.Err as e:
            if 'not modified' in e.message:
                return {'same': True}
            if 'parse entities' in e.message:
                if has_media:
                    await self._tg(bot.edit_message_caption(chat_id=cid, message_id=mid, caption=text, parse_mode=None))
                else:
                    await self._tg(bot.edit_message_text(text, chat_id=cid, message_id=mid, parse_mode=None))
            else:
                raise

    async def delete(self, request):
        d = await self.panel._json(request)
        cid = self._chat(d)
        ids = sorted({self._int(i, 'id') for i in (d.get('ids') or [])})[:100]
        if not ids:
            return None
        if d.get('local'):                                    # убрать только из истории сайта
            self.bc.drop_messages(cid, ids)
            return None
        bot = self._need_bot()
        # по одному. Пачка (deleteMessages) молча пропускает то, что удалить нельзя, и по ответу не понять,
        # что именно удалилось — раньше из-за этого «удалялось только первое». Поштучно каждый успех/отказ известен точно.
        done, failed, last = [], 0, ''
        for i in ids:
            try:
                await bot.delete_message(chat_id=cid, message_id=i)
                done.append(i)
            except Exception as e:
                failed += 1
                last = getattr(e, 'message', None) or str(e)
            if len(ids) > 20:
                await asyncio.sleep(0.04)                      # не упираться в лимиты Bot API
        if done:
            self.bc.drop_messages(cid, done)
        if failed:
            hint = ' (' + last.split(':', 1)[-1].strip() + ')' if last else ''
            raise self.Err(f'Telegram не дал удалить {failed} из {len(ids)}{hint} — старше 48 часов или нет прав; '
                           'такие можно убрать только из истории сайта')

    async def forward(self, request):
        d = await self.panel._json(request)
        src, dst = self._chat(d, 'from'), self._chat(d, 'to')
        ids = sorted(self._int(i, 'id') for i in (d.get('ids') or []))[:100]
        bot = self._need_bot()
        for mid in ids:
            if d.get('copy'):
                res = await self._tg(bot.copy_message(dst, src, mid))
                old = self.bc.message(src, mid)
                if old:                                        # copy_message отдаёт только id — достраиваем копию сами
                    me = await self.bc.me()
                    new = {k: v for k, v in old.items() if k not in ('reply', 'fwd', 'rx', 'my_rx', 'edit', 'kb', 'via')}
                    new.update({'id': res.message_id, 'chat': dst, 'ts': int(time.time()), 'out': True,
                                'from': {'id': me.id, 'name': me.first_name, 'username': me.username, 'bot': True}})
                    meta = self.bc.get_chat_meta(dst) or {'id': dst, 'type': 'private', 'title': str(dst), 'username': None}
                    await self.bc.put_local(new, {k: meta[k] for k in ('id', 'type', 'title', 'username')})
            else:
                await self._tg(bot.forward_message(dst, src, mid))

    async def react(self, request):
        from aiogram.types import ReactionTypeEmoji
        d = await self.panel._json(request)
        cid, mid, emoji = self._chat(d), self._int(d.get('id'), 'id'), d.get('emoji') or None
        await self._tg(self._need_bot().set_message_reaction(
            cid, mid, reaction=[ReactionTypeEmoji(emoji=emoji)] if emoji else []))
        self.bc.set_reaction(cid, mid, emoji)

    async def pin(self, request):
        d = await self.panel._json(request)
        cid, mid = self._chat(d), self._int(d.get('id'), 'id')
        bot = self._need_bot()
        if d.get('unpin'):
            await self._tg(bot.unpin_chat_message(chat_id=cid, message_id=mid))
        else:
            await self._tg(bot.pin_chat_message(chat_id=cid, message_id=mid, disable_notification=bool(d.get('silent', True))))

    # ------------------------------------------------------------ чаты #

    async def chatpin(self, request):                          # закрепление чата в СПИСКЕ сайта
        d = await self.panel._json(request)
        self.bc.set_pinned(self._chat(d), bool(d.get('on')))

    async def read(self, request):
        d = await self.panel._json(request)
        self.bc.mark_read(self._chat(d))

    async def action(self, request):
        d = await self.panel._json(request)
        act = d.get('action') if d.get('action') in ('typing', 'upload_photo', 'record_voice', 'upload_document') else 'typing'
        await self._tg(self._need_bot().send_chat_action(self._chat(d), act))

    async def open(self, request):
        """Добавить чат в список по id или @username (для публичных чатов и тех, кто уже писал боту)."""
        d = await self.panel._json(request)
        ref = str(d.get('ref') or '').strip()
        if not ref:
            raise self.Err('укажите @username или id')
        if ref.startswith('https://t.me/'):
            ref = '@' + ref.rsplit('/', 1)[-1]
        if not ref.startswith('@') and not ref.lstrip('-').isdigit():
            ref = '@' + ref
        chat = await self._tg(self._need_bot().get_chat(int(ref) if ref.lstrip('-').isdigit() else ref))
        c = self.bc.add_chat(chat_meta(chat))
        return {'chat': self.bc.chat_summary(c)}

    async def forget(self, request):
        d = await self.panel._json(request)
        self.bc.forget_chat(self._chat(d))

    async def clear(self, request):
        d = await self.panel._json(request)
        cid = self._chat(d)
        self.bc.msgs[cid] = []
        self.bc._refresh_last(cid)
        c = self.bc.chats.get(cid)
        if c:
            self.bc._emit('reset_chat', cid, None)
            self.bc._emit('chat', cid, self.bc.chat_summary(c))
        self.bc._dirty()

    async def leave(self, request):
        d = await self.panel._json(request)
        cid = self._chat(d)
        await self._tg(self._need_bot().leave_chat(cid))
        self.bc.mark_left(cid)

    async def member(self, request):
        d = await self.panel._json(request)
        cid, uid, act = self._chat(d), self._int(d.get('user'), 'user'), d.get('action')
        bot = self._need_bot()
        if act == 'ban':
            await self._tg(bot.ban_chat_member(cid, uid))
        elif act == 'kick':                                    # убрать, но не банить
            await self._tg(bot.ban_chat_member(cid, uid))
            await self._tg(bot.unban_chat_member(cid, uid, only_if_banned=True))
        elif act == 'unban':
            await self._tg(bot.unban_chat_member(cid, uid, only_if_banned=True))
        else:
            raise self.Err('неизвестное действие')

    async def rename(self, request):
        d = await self.panel._json(request)
        title = str(d.get('title') or '').strip()
        if not title:
            raise self.Err('пустое название')
        cid = self._chat(d)
        await self._tg(self._need_bot().set_chat_title(cid, title[:128]))
        c = self.bc.chats.get(cid)
        if c:
            c['title'] = title[:128]
            self.bc._emit('chat', cid, self.bc.chat_summary(c)); self.bc._dirty()

    async def describe(self, request):
        d = await self.panel._json(request)
        await self._tg(self._need_bot().set_chat_description(self._chat(d), str(d.get('description') or '')[:255]))

    async def cross(self, request):
        """cross watch: бот видит сообщения группы глазами аккаунта пользователя (юзербота)."""
        d = await self.panel._json(request)
        cid = self._chat(d)
        c = self.bc.chats.get(cid)
        if not c or c['type'] not in ('group', 'supergroup'):
            raise self.Err('cross watch работает только в группах')
        on = bool(d.get('on'))
        if on:
            client = self.panel.core.client
            if client is None:
                raise self.Err('юзербот не подключён', 409)
            try:                                              # аккаунт должен состоять в этой группе
                await client.get_input_entity(cid)
            except Exception:
                raise self.Err('аккаунт юзербота не состоит в этой группе (или не видит её) — cross watch включить нельзя', 400)
        self.bc.set_cross(cid, on)

    # --------------------------------------------------- профиль самого бота #

    async def myprofile(self, request):
        bot = self._need_bot()
        me = await self.bc.me()
        out = {'id': me.id, 'username': me.username, 'first_name': me.first_name, 'name': '', 'description': '',
               'short_description': '', 'commands': [], 'can_photo': True}
        for key, call in (('name', bot.get_my_name), ('description', bot.get_my_description),
                          ('short_description', bot.get_my_short_description)):
            try:
                v = await call()
                out[key] = getattr(v, key, '') or ''
            except Exception:
                pass
        try:
            out['commands'] = [{'command': c.command, 'description': c.description} for c in await bot.get_my_commands()]
        except Exception:
            pass
        return out

    async def myprofile_set(self, request):
        d = await self.panel._json(request)
        bot = self._need_bot()
        n = 0
        if 'name' in d:
            await self._tg(bot.set_my_name(name=str(d['name']).strip()[:64]))
            n += 1
        if 'description' in d:
            await self._tg(bot.set_my_description(description=str(d['description']).strip()[:512]))
            n += 1
        if 'short_description' in d:
            await self._tg(bot.set_my_short_description(short_description=str(d['short_description']).strip()[:120]))
            n += 1
        if not n:
            raise self.Err('нечего сохранять')
        self.bc._me = None                                      # имя могло измениться — перечитаем при следующем обращении

    async def mycommands_set(self, request):
        import re
        from aiogram.types import BotCommand
        d = await self.panel._json(request)
        items = d.get('commands') or []
        cmds = []
        for it in items[:100]:
            name = str(it.get('command') or '').strip().lstrip('/').lower()
            desc = str(it.get('description') or '').strip()
            if not name and not desc:
                continue
            if not re.fullmatch(r'[a-z0-9_]{1,32}', name):
                raise self.Err(f'команда «{name or "?"}»: только латиница, цифры и «_», до 32 символов')
            if not 1 <= len(desc) <= 256:
                raise self.Err(f'команда /{name}: описание — от 1 до 256 символов')
            cmds.append(BotCommand(command=name, description=desc))
        bot = self._need_bot()
        if cmds:
            await self._tg(bot.set_my_commands(cmds))
        else:
            await self._tg(bot.delete_my_commands())
        return {'count': len(cmds)}

    async def myphoto(self, request):
        from aiogram.types import BufferedInputFile, InputProfilePhotoStatic
        bot = self._need_bot()
        form = await request.post()
        f = form.get('file')
        if not hasattr(f, 'file'):
            raise self.Err('нет файла')
        data = f.file.read()
        if len(data) > 10 * 1048576:
            raise self.Err('фото больше 10 МБ')
        await self._tg(bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=BufferedInputFile(data, filename='photo.jpg'))))
        self.bc.drop_avatar((await self.bc.me()).id)

    async def myphoto_remove(self, request):
        await self._tg(self._need_bot().remove_my_profile_photo())
        self.bc.drop_avatar((await self.bc.me()).id)

    # ----------------------------------------------------------- файлы #

    @staticmethod
    def _same_site(request):
        if request.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none'):
            raise web.HTTPForbidden()

    async def media(self, request):
        self._same_site(request)
        q = request.query
        try:
            cid, mid = int(q.get('chat')), int(q.get('id'))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest()
        if not self.bc.available:
            raise web.HTTPNotFound()
        try:
            path, mime = await self.bc.media_path(cid, mid, thumb=q.get('thumb') == '1')
        except Exception as e:
            text = 'файл больше 20 МБ — Bot API не отдаёт такие' if 'too big' in str(e).lower() else str(e)
            raise web.HTTPNotFound(text=text)
        if not path:
            raise web.HTTPNotFound()
        headers = {'Cache-Control': 'private, max-age=86400', 'Content-Type': mime}
        if q.get('dl') == '1':
            msg = self.bc.message(cid, mid) or {}
            name = (msg.get('media') or {}).get('name') or Path(path).name
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + __import__('urllib.parse').parse.quote(name)
        return web.FileResponse(path, headers=headers)

    async def avatar(self, request):
        self._same_site(request)
        try:
            cid = int(request.query.get('chat'))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest()
        if not self.bc.available:
            return web.Response(status=204)
        path = await self.bc.avatar_path(cid)
        if not path:
            return web.Response(status=204)          # фото нет — сайт нарисует букву (204, а не 404: без шума в консоли)
        return web.FileResponse(path, headers={'Cache-Control': 'private, max-age=3600', 'Content-Type': 'image/jpeg'})
