import re

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from app import texts, media
from app.db.repo import Repo
from app.navigation import Nav, Screen

router = Router()

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^[\d\+\-\(\)\s]{7,25}$")


class InvitePhone(StatesGroup):
    wait_contact = State()
    wait_manual = State()


class VisitFlow(StatesGroup):
    wait_email = State()
    wait_phone_contact = State()
    wait_phone_manual = State()


def _t(name: str, fallback: str) -> str:
    return getattr(texts, name, fallback)


def _p(name: str) -> str:
    return getattr(media, name, "PLACEHOLDER")


def _is_registered(u) -> bool:
    return bool(u and u.consent == 1 and u.name and u.email and u.role)


def _city_address(city: str) -> str:
    default = {
        "spb": "СПб: ",
        "moscow": "Москва",
        "yerevan": "Ереван",
        "dubai": "Дубай",
    }
    d = getattr(texts, "CITY_ADDRESSES", None)
    if isinstance(d, dict) and city in d:
        return d[city]
    return default.get(city, "Адрес")


async def _notify_admins(bot, admin_ids: set[int], text: str):
    for aid in admin_ids:
        try:
            await bot.send_message(aid, text, disable_web_page_preview=True)
        except Exception:
            pass


async def _create_visit_request(repo: Repo, telegram_id: int, city: str, method: str, value: str | None):
    u = await repo.get_user(telegram_id)
    name_snapshot = u.name if u and u.name else None
    role_snapshot = u.role if u and u.role else None

    return await repo.create_visit_request(
        telegram_id=telegram_id,
        name_snapshot=name_snapshot,
        role_snapshot=role_snapshot,
        city=city,
        contact_method=method,
        contact_value=value,
    )


def register_screens(nav: Nav, repo: Repo):
    async def screen_invite_main(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="📲 Свяжитесь со мной", callback_data="invite:me")
        kb.button(text="🏙 Визит: выбрать город", callback_data="invite:city")
        kb.button(text="📇 Контакты", callback_data="invite:contacts")
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(
            text=_t("INVITE_MAIN_TEXT", "Хотите личную связь с галереей? Выберите, как удобнее."),
            photo_file_id=_p("PHOTO_CONTACT_MAIN"),
            inline=kb.as_markup(),
        )

    async def screen_invite_me(chat_id: int, ctx: dict) -> Screen:
        inline = InlineKeyboardBuilder()
        inline.button(text="✍️ Ввести вручную", callback_data="invite:phone_manual")
        inline.button(text="⬅️ Назад", callback_data="nav:back")
        inline.button(text="🏠 Главное меню", callback_data="menu:main")
        inline.adjust(1)

        reply_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )

        return Screen(
            text=_t("INVITE_ME_TEXT", "Оставьте номер — мы свяжемся с вами в рабочее время."),
            photo_file_id=_p("PHOTO_CONTACT_PHONE"),
            inline=inline.as_markup(),
            reply=reply_kb,
        )

    async def screen_invite_phone_manual(chat_id: int, ctx: dict) -> Screen:
        inline = InlineKeyboardBuilder()
        inline.button(text="⬅️ Назад", callback_data="nav:back")
        inline.button(text="🏠 Главное меню", callback_data="menu:main")
        inline.adjust(1)
        return Screen(
            text="Введите номер телефона текстом (например: +7 999 123-45-67).",
            photo_file_id=_p("PHOTO_CONTACT_PHONE"),
            inline=inline.as_markup(),
        )

    async def screen_phone_saved(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(
            text="Спасибо! Номер записан. Мы свяжемся с вами в рабочее время.",
            inline=kb.as_markup(),
        )

    # Контакты для ГОСТЯ: только текст + меню (без лишних кнопок)
    async def screen_contacts_guest(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(
            text=_t("GUEST_CONTACTS_TEXT", "Контакты галереи:\nТелефон: +7 XXX XXX-XX-XX\nEmail: hello@example.com"),
            photo_file_id=_p("PHOTO_CONTACTS_CARD"),
            inline=kb.as_markup(),
        )

    # Контакты для зарегистрированного: назад/меню
    async def screen_contacts_registered(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="⬅️ Назад", callback_data="nav:back")
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(
            text=_t(
                "CONTACTS_TEXT",
                _t("GUEST_CONTACTS_TEXT", "Контакты галереи:\nТелефон: +7 XXX XXX-XX-XX\nEmail: hello@example.com"),
            ),
            photo_file_id=_p("PHOTO_CONTACTS_CARD"),
            inline=kb.as_markup(),
        )

    async def screen_city(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="Санкт-Петербург", callback_data="city:spb")
        kb.button(text="Москва", callback_data="city:moscow")
        kb.button(text="Ереван", callback_data="city:yerevan")
        kb.button(text="Дубай", callback_data="city:dubai")
        kb.button(text="⬅️ Назад", callback_data="nav:back")
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(
            text="Выберите город для визита:",
            photo_file_id=_p("PHOTO_CONTACT_MAIN"),
            inline=kb.as_markup(),
        )

    async def screen_method(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="Telegram", callback_data="visit_method:tg")
        kb.button(text="Телефон", callback_data="visit_method:phone")
        kb.button(text="Email", callback_data="visit_method:email")
        kb.button(text="⬅️ Назад", callback_data="nav:back")
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(
            text="Как с вами удобнее связаться?",
            photo_file_id=_p("PHOTO_CONTACT_MAIN"),
            inline=kb.as_markup(),
        )

    async def screen_visit_done(chat_id: int, ctx: dict) -> Screen:
        city = ctx.get("city", "")
        kb = InlineKeyboardBuilder()
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(
            text="Заявка на визит принята. Мы свяжемся с вами в ближайшее время.\n\n" + _city_address(city),
            inline=kb.as_markup(),
        )

    async def screen_email_ask(chat_id: int, ctx: dict) -> Screen:
        kb = InlineKeyboardBuilder()
        kb.button(text="⬅️ Назад", callback_data="nav:back")
        kb.button(text="🏠 Главное меню", callback_data="menu:main")
        kb.adjust(1)
        return Screen(text="Введите ваш e-mail для связи:", inline=kb.as_markup())

    nav.register("invite:main", screen_invite_main)
    nav.register("invite:me", screen_invite_me)
    nav.register("invite:phone_manual", screen_invite_phone_manual)
    nav.register("invite:phone_saved", screen_phone_saved)

    nav.register("invite:contacts_guest", screen_contacts_guest)
    nav.register("invite:contacts_registered", screen_contacts_registered)

    nav.register("invite:city", screen_city)
    nav.register("invite:method", screen_method)
    nav.register("invite:visit_done", screen_visit_done)
    nav.register("invite:email_ask", screen_email_ask)


# ---------- handlers ----------

@router.callback_query(F.data == "menu:invite_main")
async def open_invite_main(cb: CallbackQuery, nav: Nav):
    await cb.answer()
    await nav.show_screen(cb.bot, cb.from_user.id, "invite:main", remove_reply_keyboard=True)


@router.callback_query(F.data == "invite:contacts")
async def open_contacts(cb: CallbackQuery, repo: Repo, nav: Nav, state: FSMContext):
    await cb.answer()
    await state.clear()

    u = await repo.get_user(cb.from_user.id)
    screen = "invite:contacts_registered" if _is_registered(u) else "invite:contacts_guest"
    await nav.show_screen(cb.bot, cb.from_user.id, screen, remove_reply_keyboard=True)


@router.callback_query(F.data == "invite:city")
async def open_city(cb: CallbackQuery, nav: Nav, state: FSMContext):
    await cb.answer()
    # важно: не сносить state тут, иначе “город -> метод” может ломаться в процессе
    await nav.show_screen(cb.bot, cb.from_user.id, "invite:city", remove_reply_keyboard=True)


@router.callback_query(F.data == "invite:me")
async def invite_me(cb: CallbackQuery, nav: Nav, state: FSMContext):
    await cb.answer()
    await state.set_state(InvitePhone.wait_contact)
    await nav.show_screen(cb.bot, cb.from_user.id, "invite:me", remove_reply_keyboard=False)


@router.callback_query(F.data == "invite:phone_manual")
async def invite_phone_manual(cb: CallbackQuery, nav: Nav, state: FSMContext):
    await cb.answer()
    await state.set_state(InvitePhone.wait_manual)
    await nav.show_screen(cb.bot, cb.from_user.id, "invite:phone_manual", replace_top=True, remove_reply_keyboard=True)


# ----- INVITE (Свяжитесь со мной) -----

@router.message(InvitePhone.wait_contact, F.contact)
async def got_contact(message: Message, repo: Repo, nav: Nav, state: FSMContext, admin_ids: set[int]):
    phone = message.contact.phone_number if message.contact else None
    if not phone:
        await message.answer("Не удалось прочитать номер. Попробуйте ещё раз или введите вручную.")
        return

    await repo.update_profile(message.from_user.id, phone=phone)
    await state.clear()

    u = await repo.get_user(message.from_user.id)
    name = (u.name if u and u.name else "—")
    role = (u.role if u and u.role else "—")
    username = f"@{message.from_user.username}" if message.from_user.username else "—"

    await _notify_admins(
        message.bot,
        admin_ids,
        "📲 Запрос связи (телефон)\n"
        f"Имя: {name}\nРоль: {role}\nТелефон: {phone}\nUsername: {username}\n"
        f"Профиль: tg://user?id={message.from_user.id}",
    )

    await nav.show_screen(message.bot, message.from_user.id, "invite:phone_saved", remove_reply_keyboard=True)


@router.message(InvitePhone.wait_manual)
async def got_manual_phone(message: Message, repo: Repo, nav: Nav, state: FSMContext, admin_ids: set[int]):
    if not message.text:
        await message.answer("Отправьте номер текстом (например: +7 999 123-45-67).")
        return

    raw = message.text.strip()
    if not PHONE_RE.match(raw):
        await message.answer("Похоже, номер введён некорректно. Пример: +7 999 123-45-67")
        return

    await repo.update_profile(message.from_user.id, phone=raw)
    await state.clear()

    u = await repo.get_user(message.from_user.id)
    name = (u.name if u and u.name else "—")
    role = (u.role if u and u.role else "—")
    username = f"@{message.from_user.username}" if message.from_user.username else "—"

    await _notify_admins(
        message.bot,
        admin_ids,
        "📲 Запрос связи (номер вручную)\n"
        f"Имя: {name}\nРоль: {role}\nТелефон: {raw}\nUsername: {username}\n"
        f"Профиль: tg://user?id={message.from_user.id}",
    )

    await nav.show_screen(message.bot, message.from_user.id, "invite:phone_saved", remove_reply_keyboard=True)


# ----- VISIT FLOW -----

@router.callback_query(F.data.startswith("city:"))
async def pick_city(cb: CallbackQuery, repo: Repo, nav: Nav, state: FSMContext):
    await cb.answer()
    city = cb.data.split(":", 1)[1]

    await repo.update_profile(cb.from_user.id, city=city)
    await state.update_data(visit_city=city)

    await nav.show_screen(cb.bot, cb.from_user.id, "invite:method", remove_reply_keyboard=True)


@router.callback_query(F.data == "visit_method:tg")
async def method_tg(cb: CallbackQuery, repo: Repo, nav: Nav, state: FSMContext, admin_ids: set[int]):
    await cb.answer()
    data = await state.get_data()
    city = data.get("visit_city")
    if not city:
        await nav.show_screen(cb.bot, cb.from_user.id, "invite:city", remove_reply_keyboard=True)
        return

    username = f"@{cb.from_user.username}" if cb.from_user.username else None
    value = username or str(cb.from_user.id)

    await _create_visit_request(repo, cb.from_user.id, city, "tg", value)

    await _notify_admins(
        cb.bot,
        admin_ids,
        "🏙 Новая заявка на визит\n"
        f"Город: {city}\nМетод: tg\nКонтакт: {value}\n"
        f"Профиль: tg://user?id={cb.from_user.id}",
    )

    await state.clear()
    await nav.show_screen(cb.bot, cb.from_user.id, "invite:visit_done", ctx={"city": city}, remove_reply_keyboard=True)


@router.callback_query(F.data == "visit_method:email")
async def method_email(cb: CallbackQuery, repo: Repo, nav: Nav, state: FSMContext, admin_ids: set[int]):
    await cb.answer()
    data = await state.get_data()
    city = data.get("visit_city")
    if not city:
        await nav.show_screen(cb.bot, cb.from_user.id, "invite:city", remove_reply_keyboard=True)
        return

    u = await repo.get_user(cb.from_user.id)
    if u and u.email:
        await _create_visit_request(repo, cb.from_user.id, city, "email", u.email)

        await _notify_admins(
            cb.bot,
            admin_ids,
            "🏙 Новая заявка на визит\n"
            f"Город: {city}\nМетод: email\nКонтакт: {u.email}\n"
            f"Профиль: tg://user?id={cb.from_user.id}",
        )

        await state.clear()
        await nav.show_screen(cb.bot, cb.from_user.id, "invite:visit_done", ctx={"city": city}, remove_reply_keyboard=True)
    else:
        await state.set_state(VisitFlow.wait_email)
        await nav.show_screen(cb.bot, cb.from_user.id, "invite:email_ask", replace_top=True, remove_reply_keyboard=True)


@router.message(VisitFlow.wait_email)
async def got_visit_email(message: Message, repo: Repo, nav: Nav, state: FSMContext, admin_ids: set[int]):
    data = await state.get_data()
    city = data.get("visit_city")

    if not city:
        await state.clear()
        await nav.show_screen(message.bot, message.from_user.id, "invite:city", remove_reply_keyboard=True)
        return

    if not message.text or not EMAIL_RE.match(message.text.strip()):
        await message.answer("Похоже, e-mail введён с ошибкой. Проверьте и отправьте ещё раз.")
        return

    email = message.text.strip()
    await repo.update_profile(message.from_user.id, email=email)
    await _create_visit_request(repo, message.from_user.id, city, "email", email)

    await _notify_admins(
        message.bot,
        admin_ids,
        "🏙 Новая заявка на визит\n"
        f"Город: {city}\nМетод: email\nКонтакт: {email}\n"
        f"Профиль: tg://user?id={message.from_user.id}",
    )

    await state.clear()
    await nav.show_screen(message.bot, message.from_user.id, "invite:visit_done", ctx={"city": city}, remove_reply_keyboard=True)


@router.callback_query(F.data == "visit_method:phone")
async def method_phone(cb: CallbackQuery, repo: Repo, nav: Nav, state: FSMContext, admin_ids: set[int]):
    await cb.answer()
    data = await state.get_data()
    city = data.get("visit_city")
    if not city:
        await nav.show_screen(cb.bot, cb.from_user.id, "invite:city", remove_reply_keyboard=True)
        return

    u = await repo.get_user(cb.from_user.id)
    if u and u.phone:
        await _create_visit_request(repo, cb.from_user.id, city, "phone", u.phone)

        await _notify_admins(
            cb.bot,
            admin_ids,
            "🏙 Новая заявка на визит\n"
            f"Город: {city}\nМетод: phone\nКонтакт: {u.phone}\n"
            f"Профиль: tg://user?id={cb.from_user.id}",
        )

        await state.clear()
        await nav.show_screen(cb.bot, cb.from_user.id, "invite:visit_done", ctx={"city": city}, remove_reply_keyboard=True)
        return

    # телефона нет — попросим контакт через invite:me (reply-кнопка)
    await state.set_state(VisitFlow.wait_phone_contact)
    await nav.show_screen(cb.bot, cb.from_user.id, "invite:me", remove_reply_keyboard=False)


@router.message(VisitFlow.wait_phone_contact, F.contact)
async def got_visit_phone_contact(message: Message, repo: Repo, nav: Nav, state: FSMContext, admin_ids: set[int]):
    data = await state.get_data()
    city = data.get("visit_city")

    if not city:
        await state.clear()
        await nav.show_screen(message.bot, message.from_user.id, "invite:city", remove_reply_keyboard=True)
        return

    phone = message.contact.phone_number if message.contact else None
    if not phone:
        await message.answer("Не удалось прочитать номер. Попробуйте ещё раз или введите вручную.")
        return

    await repo.update_profile(message.from_user.id, phone=phone)
    await _create_visit_request(repo, message.from_user.id, city, "phone", phone)

    await _notify_admins(
        message.bot,
        admin_ids,
        "🏙 Новая заявка на визит\n"
        f"Город: {city}\nМетод: phone\nКонтакт: {phone}\n"
        f"Профиль: tg://user?id={message.from_user.id}",
    )

    await state.clear()
    await nav.show_screen(message.bot, message.from_user.id, "invite:visit_done", ctx={"city": city}, remove_reply_keyboard=True)
