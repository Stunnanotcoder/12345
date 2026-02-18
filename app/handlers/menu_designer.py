from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import texts, media
from app.navigation import Nav, Screen
from app.db.repo import Repo

router = Router()


def _is_registered(u) -> bool:
    return bool(u and u.consent == 1 and u.name and u.email and u.role)


def _t(name: str, fallback: str) -> str:
    return getattr(texts, name, fallback)


def _p(name: str, fallback: str = "PLACEHOLDER") -> str:
    return getattr(media, name, fallback)


async def _send_to_admins(bot, admin_ids: set[int], text: str) -> None:
    for aid in admin_ids:
        try:
            await bot.send_message(aid, text, disable_web_page_preview=True)
        except Exception:
            pass


def register_screens(nav: Nav, repo: Repo):
    async def screen_designer(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="🤝 Сотрудничать", callback_data="designer:apply")
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)

        text = _t(
            "DESIGNER_TEXT",
            (
                "<b>Дизайнеры и архитекторы</b>\n\n"
                "Если вы работаете с частными или коммерческими интерьерами, мы открыты к партнёрству.\n"
                "FORM & BRONZE предоставляет материалы, условия и поддержку для интеграции скульптур в проекты.\n\n"
                "Нажмите «Сотрудничать» — и мы свяжемся с вами."
            ),
        )

        return Screen(
            text=text,
            photo_file_id=_p("PHOTO_DESIGNER", _p("PHOTO_MENU", "PLACEHOLDER")),
            inline=kb.as_markup(),
            disable_web_page_preview=True,
        )

    nav.register("designer", screen_designer)


@router.callback_query(F.data == "menu:designer")
async def open_designer(cb: CallbackQuery, nav: Nav):
    await cb.answer()  # ✅ сразу, чтобы не ловить "query is too old"
    await nav.show_screen(cb.bot, cb.from_user.id, "designer", remove_reply_keyboard=True)


@router.callback_query(F.data == "designer:apply")
async def designer_apply(cb: CallbackQuery, repo: Repo, nav: Nav, admin_ids: set[int]):
    await cb.answer()  # ✅ сразу

    # гарантируем строку пользователя
    if hasattr(repo, "ensure_user_row"):
        await repo.ensure_user_row(cb.from_user.id)

    u = await repo.get_user(cb.from_user.id)

    # гость -> регистрация
    if not _is_registered(u):
        await nav.show_screen(cb.bot, cb.from_user.id, "settings:guest", remove_reply_keyboard=True)
        return

    # фиксируем интерес (если метод есть)
    try:
        if hasattr(repo, "set_designer_interest"):
            await repo.set_designer_interest(cb.from_user.id, True)
    except Exception:
        pass

    # данные (телефон НЕ обязателен)
    name = getattr(u, "name", None) or "—"
    email = getattr(u, "email", None) or "—"
    role = getattr(u, "role", None) or "—"
    phone = getattr(u, "phone", None) or "—"
    username = f"@{cb.from_user.username}" if cb.from_user.username else "—"

    admin_text = (
        "🎨 <b>Заявка на сотрудничество (дизайнер)</b>\n\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Email:</b> {email}\n"
        f"<b>Роль:</b> {role}\n"
        f"<b>Телефон:</b> {phone}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>Профиль:</b> tg://user?id={cb.from_user.id}"
    )

    await _send_to_admins(cb.bot, admin_ids, admin_text)

    thanks = _t("DESIGNER_THANKS_TEXT", "Спасибо! Заявка принята. Мы свяжемся с вами в ближайшее время.")
    await cb.bot.send_message(cb.from_user.id, thanks, disable_web_page_preview=True)
