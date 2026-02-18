from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Awaitable, Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.utils.safe_delete import safe_delete


@dataclass
class Screen:
    text: str
    photo_file_id: str | None = None
    video_file_id: str | None = None
    inline: InlineKeyboardMarkup | None = None
    # ReplyKeyboard (request_contact) — отправляем в ЭТО ЖЕ сообщение (без второго aux).
    reply: ReplyKeyboardMarkup | None = None
    reply_prompt: str | None = None  # оставлено для совместимости (можно не использовать)
    disable_web_page_preview: bool = True
    parse_mode: ParseMode | None = None  # если None — используем default_parse_mode Nav


Renderer = Callable[[int, dict], Awaitable[Screen]]

CAPTION_LIMIT = 1000  # безопасно для caption (Telegram 1024, HTML/ссылки могут съесть лимит)


def _safe_text(text: str | None, fallback: str = "…") -> str:
    """Telegram ругается на пустой текст. Страхуемся."""
    if text is None:
        return fallback
    t = text.strip()
    return t if t else fallback


class Nav:
    """Навигация “как браузер”:
    - history stack в памяти
    - last message ids (может быть 1-2 сообщения: медиа + текст)
    """

    def __init__(self, default_parse_mode: ParseMode = ParseMode.HTML) -> None:
        self._stack: dict[int, list[str]] = {}
        self._last_ids: dict[int, list[int]] = {}
        self._renderers: dict[str, Renderer] = {}
        self._default_parse_mode: ParseMode = default_parse_mode

    def register(self, screen_prefix: str, renderer: Renderer) -> None:
        self._renderers[screen_prefix] = renderer

    def _resolve(self, screen_id: str) -> Renderer:
        candidates = [
            (k, v)
            for k, v in self._renderers.items()
            if screen_id == k or screen_id.startswith(k + ":")
        ]
        if not candidates:
            raise KeyError(f"No renderer for screen_id={screen_id}")
        candidates.sort(key=lambda x: len(x[0]), reverse=True)
        return candidates[0][1]

    def push(self, chat_id: int, screen_id: str) -> None:
        self._stack.setdefault(chat_id, []).append(screen_id)

    def pop(self, chat_id: int) -> str | None:
        st = self._stack.get(chat_id) or []
        if not st:
            return None
        return st.pop()

    def peek(self, chat_id: int) -> str | None:
        st = self._stack.get(chat_id) or []
        return st[-1] if st else None

    def clear(self, chat_id: int) -> None:
        self._stack[chat_id] = []

    async def _delete_last(self, bot: Bot, chat_id: int) -> None:
        ids = self._last_ids.get(chat_id) or []
        for mid in ids:
            await safe_delete(bot, chat_id, mid)
        self._last_ids[chat_id] = []

    @staticmethod
    def _pick_markup(screen: Screen):
        """
        Telegram НЕ поддерживает InlineKeyboard + ReplyKeyboard одновременно.
        Если screen.reply есть — показываем её, иначе inline.
        """
        return screen.reply if screen.reply is not None else screen.inline

    async def _retry(self, fn, *args, **kwargs) -> Any:
        """
        Ретраи на сетевые обрывы Telegram.
        - TelegramRetryAfter: ждём сколько сказал Telegram
        - TelegramNetworkError: 3 попытки с backoff 1/2/4 сек
        """
        delay = 1
        for attempt in range(3):
            try:
                return await fn(*args, **kwargs)
            except TelegramRetryAfter as e:
                # Telegram сам просит подождать
                await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
            except TelegramNetworkError:
                if attempt == 2:
                    raise
                await asyncio.sleep(delay)
                delay *= 2

    async def show_screen(
        self,
        bot: Bot,
        chat_id: int,
        screen_id: str,
        ctx: dict | None = None,
        push: bool = True,
        replace_top: bool = False,
        remove_reply_keyboard: bool = False,
    ) -> None:
        ctx = ctx or {}

        # 1) удаляем прошлые сообщения бота этого "экрана"
        await self._delete_last(bot, chat_id)

        # 2) рендерим экран
        renderer = self._resolve(screen_id)
        screen = await renderer(chat_id, {"screen_id": screen_id, **ctx})

        # 3) страхуем текст
        screen_text = _safe_text(screen.text)

        # parse_mode: экранный или дефолтный
        pm: ParseMode = screen.parse_mode or self._default_parse_mode

        sent_ids: list[int] = []

        # 0) если надо убрать reply-клавиатуру (после request_contact)
        # Telegram не позволяет отправить пустой текст — шлём "…" и тут же удаляем.
        if remove_reply_keyboard:
            rm_msg = await self._retry(
                bot.send_message,
                chat_id=chat_id,
                text="…",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=pm,
            )
            await safe_delete(bot, chat_id, rm_msg.message_id)

        reply_markup = self._pick_markup(screen)

        async def _send_text_only() -> int:
            m = await self._retry(
                bot.send_message,
                chat_id=chat_id,
                text=screen_text,
                reply_markup=reply_markup,
                disable_web_page_preview=screen.disable_web_page_preview,
                parse_mode=pm,
            )
            return m.message_id

        # 4) основной контент: видео/фото+текст или только текст

        # 4.1) видео
        if screen.video_file_id and not screen.video_file_id.startswith("PLACEHOLDER"):
            # Если есть ReplyKeyboard — видео отдельно + текст с reply.
            if screen.reply is not None:
                v = await self._retry(bot.send_video, chat_id=chat_id, video=screen.video_file_id)
                sent_ids.append(v.message_id)
                sent_ids.append(await _send_text_only())
            else:
                # inline или без клавиатуры можно в caption
                if len(screen_text) <= CAPTION_LIMIT and reply_markup is not None:
                    v = await self._retry(
                        bot.send_video,
                        chat_id=chat_id,
                        video=screen.video_file_id,
                        caption=screen_text,
                        reply_markup=reply_markup,
                        parse_mode=pm,
                    )
                    sent_ids.append(v.message_id)
                elif len(screen_text) <= CAPTION_LIMIT and reply_markup is None:
                    v = await self._retry(
                        bot.send_video,
                        chat_id=chat_id,
                        video=screen.video_file_id,
                        caption=screen_text,
                        parse_mode=pm,
                    )
                    sent_ids.append(v.message_id)
                else:
                    v = await self._retry(bot.send_video, chat_id=chat_id, video=screen.video_file_id)
                    sent_ids.append(v.message_id)
                    sent_ids.append(await _send_text_only())

        # 4.2) фото
        elif screen.photo_file_id and not screen.photo_file_id.startswith("PLACEHOLDER"):
            # Если reply-клава — фото отдельно + текст с reply.
            if screen.reply is not None:
                p = await self._retry(bot.send_photo, chat_id=chat_id, photo=screen.photo_file_id)
                sent_ids.append(p.message_id)
                sent_ids.append(await _send_text_only())
            else:
                if len(screen_text) <= CAPTION_LIMIT and reply_markup is not None:
                    p = await self._retry(
                        bot.send_photo,
                        chat_id=chat_id,
                        photo=screen.photo_file_id,
                        caption=screen_text,
                        reply_markup=reply_markup,
                        parse_mode=pm,
                    )
                    sent_ids.append(p.message_id)
                elif len(screen_text) <= CAPTION_LIMIT and reply_markup is None:
                    p = await self._retry(
                        bot.send_photo,
                        chat_id=chat_id,
                        photo=screen.photo_file_id,
                        caption=screen_text,
                        parse_mode=pm,
                    )
                    sent_ids.append(p.message_id)
                else:
                    p = await self._retry(bot.send_photo, chat_id=chat_id, photo=screen.photo_file_id)
                    sent_ids.append(p.message_id)
                    sent_ids.append(await _send_text_only())

        # 4.3) только текст
        else:
            sent_ids.append(await _send_text_only())

        # 5) сохраняем последние message_id чтобы потом их удалить при следующем show_screen
        self._last_ids[chat_id] = sent_ids

        # 6) обновляем history stack
        if push:
            if replace_top:
                st = self._stack.setdefault(chat_id, [])
                if st:
                    st[-1] = screen_id
                else:
                    st.append(screen_id)
            else:
                self.push(chat_id, screen_id)

    async def back(self, bot: Bot, chat_id: int, fallback_screen: str) -> None:
        self.pop(chat_id)
        prev = self.peek(chat_id)
        if not prev:
            await self.show_screen(bot, chat_id, fallback_screen, push=True)
            return
        await self.show_screen(bot, chat_id, prev, push=False)
