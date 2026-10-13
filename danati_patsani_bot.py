import os
import json
import time
import asyncio
import httpx
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / "bot.env"
DATA_FILE = BASE / "danati_orders.json"

if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")

# Реквизитҳои ҳозираро нигоҳ доштем.
DC_CARD = "9762 0002 6172 0113"
DC_OWNER = "Jumaboi Khojazoda"
DC_PHONE = "989047040"

DIAMONDS = [
    ("💎 110 алмаз — 9 сомонӣ", "110 алмаз", 9),
    ("💎 341 алмаз — 28 сомонӣ", "341 алмаз", 28),
    ("💎 572 алмаз — 48 сомонӣ", "572 алмаз", 48),
    ("💎 1166 алмаз — 90 сомонӣ", "1166 алмаз", 90),
    ("💎 2398 алмаз — 180 сомонӣ", "2398 алмаз", 180),
    ("💎 6160 алмаз — 455 сомонӣ", "6160 алмаз", 455),
]
VOUCHERS = [
    ("🎁 Lite 90 — 6 сомонӣ", "Lite 90", 6),
    ("🎁 Ҳафта 450 — 17 сомонӣ", "Ҳафта 450", 17),
    ("🎁 Моҳона 2600 — 70 сомонӣ", "Моҳона 2600", 70),
]
LEVELS = [
    ("🏆 Level 6 — 6 сомонӣ", "Level 6", 6),
    ("🏆 Level 10 — 9 сомонӣ", "Level 10", 9),
    ("🏆 Level 15 — 9 сомонӣ", "Level 15", 9),
    ("🏆 Level 20 — 9 сомонӣ", "Level 20", 9),
    ("🏆 Level 25 — 9 сомонӣ", "Level 25", 9),
    ("🏆 Level 30 — 10 сомонӣ", "Level 30", 10),
]

def load_data():
    if not DATA_FILE.exists():
        return {"orders": [], "next_id": 1001, "wallets": {}, "topups": []}
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        data.setdefault("orders", [])
        data.setdefault("wallets", {})
        data.setdefault("topups", [])
        data.setdefault("next_id", 1001)
        return data
    except (ValueError, OSError) as exc:
        logging.exception("Cannot read order database; refusing to start with empty data")
        raise RuntimeError(
            f"Базаи маълумот хонда нашуд: {DATA_FILE}. "
            "Файлро барқарор кунед; базаи холӣ сабт карда намешавад."
        ) from exc

DB = load_data()

def save_data():
    """Сабти бехатари база тавассути файли муваққатӣ."""
    temp = DATA_FILE.with_suffix(".tmp")
    content = json.dumps(DB, ensure_ascii=False, indent=2)
    with temp.open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(DATA_FILE)

def new_order_id():
    number = DB["next_id"]
    DB["next_id"] += 1
    save_data()
    return number

def money(amount):
    return f"{amount} сомонӣ"

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Донат кардан", callback_data="catalog")],
        [InlineKeyboardButton("💰 Ҳамёни ман", callback_data="wallet")],
        [InlineKeyboardButton("👥 Даъвати дӯстон", callback_data="referral")],
        [
            InlineKeyboardButton("📦 Фармоишҳои ман", callback_data="orders"),
            InlineKeyboardButton("👤 Профил", callback_data="profile")
        ],
        [
            InlineKeyboardButton("👨‍💻 Админ", callback_data="admin"),
            InlineKeyboardButton("ℹ️ Маълумот", callback_data="info")
        ],
    ])

def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Менюи асосӣ", callback_data="home")]
    ])

def product_keyboard(items, prefix):
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{i}")]
        for i, (label, _, _) in enumerate(items)
    ]
    rows += [
        [InlineKeyboardButton("⬅️ Категорияҳо", callback_data="catalog")],
        [InlineKeyboardButton("🏠 Менюи асосӣ", callback_data="home")]
    ]
    return InlineKeyboardMarkup(rows)

def get_order(order_id):
    return next(
        (o for o in DB["orders"] if o["id"] == order_id),
        None
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # REFERRAL_SYSTEM_V1
    user_id = str(update.effective_user.id)
    refs = DB.setdefault("referrals", {})
    refs.setdefault("seen_users", [])
    refs.setdefault("invited_by", {})

    is_new_user = user_id not in refs["seen_users"]

    if is_new_user:
        refs["seen_users"].append(user_id)
        args = context.args or []

        if args and args[0].startswith("ref_"):
            inviter_id = args[0][4:]

            if (
                inviter_id.isdigit()
                and inviter_id != user_id
                and user_id not in refs["invited_by"]
            ):
                refs["invited_by"][user_id] = inviter_id

                # 50 дирам = 0.50 сомонӣ
                # Балансро бо дирам нигоҳ медорем, то хатои float нашавад.
                refs.setdefault("earned_diram", {})
                refs["earned_diram"][inviter_id] = (
                    int(refs["earned_diram"].get(inviter_id, 0)) + 50
                )

                # Барои ҳамёни мавҷуда 0.50 сомонӣ илова мешавад.
                wallets = DB.setdefault("wallets", {})
                old_balance = float(wallets.get(inviter_id, 0))
                wallets[inviter_id] = old_balance + 0.50

                try:
                    await context.bot.send_message(
                        chat_id=int(inviter_id),
                        text=(
                            "🎉 Дӯсти нав тавассути пайванди шумо ворид шуд!\n\n"
                            "🎁 Мукофот: 50 дирам (0,50 сомонӣ)\n"
                            "💰 Мукофот ба ҳамёни шумо илова шуд."
                        )
                    )
                except Exception:
                    logging.exception("Referral notification failed")

        save_data()

    context.user_data.clear()
    await update.message.reply_text(
        "🔥 DANATI_PATSANI\n\n"
        "🎮 Хуш омадед ба мағозаи донати Free Fire!\n\n"
        "💎 Алмазҳо\n"
        "🎁 Ваучерҳо\n"
        "🏆 Level Up\n\n"
        "📌 Маҳсулотро интихоб кунед. UID-ро дуруст нависед.\n"
        ""
        "⚠️ Фармоиш пас аз санҷиши пардохт аз ҷониби админ иҷро мешавад.",
        reply_markup=main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Барои оғоз /start-ро пахш кунед.\n"
        "Барои фармоиш аввал маҳсулотро интихоб намоед.",
        reply_markup=main_keyboard()
    )

async def _buttons_original(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except (TimedOut, NetworkError) as exc:
        logging.warning("Ҷавоби тугма аз сабаби шабака дер шуд: %s", type(exc).__name__)
    data = q.data or ""
    user = q.from_user

    if data == "home":
        context.user_data.clear()
        await q.edit_message_text(
            "🏠 Менюи асосӣ\n\nХизматрасониро интихоб кунед:",
            reply_markup=main_keyboard()
        )
        return

    if data == "catalog":
        await q.edit_message_text(
            "💎 ДОНАТ КАРДАН\n\nКатегорияро интихоб кунед:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Алмазҳо", callback_data="diamonds")],
                [InlineKeyboardButton("🎁 Ваучерҳо", callback_data="vouchers")],
                [InlineKeyboardButton("🏆 Level Up", callback_data="levels")],
                [InlineKeyboardButton("🏠 Менюи асосӣ", callback_data="home")]
            ])
        )
        return

    if data in ("diamonds", "vouchers", "levels"):
        mapping = {
            "diamonds": (DIAMONDS, "💎 НАРХНОМАИ АЛМАЗҲО", "diamond"),
            "vouchers": (VOUCHERS, "🎁 ВАУЧЕРҲО", "voucher"),
            "levels": (LEVELS, "🏆 LEVEL UP", "level"),
        }
        items, title, prefix = mapping[data]
        await q.edit_message_text(
            f"{title}\n\nМаҳсулоти дилхоҳро интихоб кунед:",
            reply_markup=product_keyboard(items, prefix)
        )
        return

    if ":" in data and data.split(":", 1)[0] in ("diamond", "voucher", "level"):
        prefix, idx = data.split(":", 1)
        items = {
            "diamond": DIAMONDS,
            "voucher": VOUCHERS,
            "level": LEVELS
        }[prefix]
        try:
            label, product_name, price = items[int(idx)]
        except (ValueError, IndexError):
            await q.edit_message_text(
                "❌ Ин маҳсулот ёфт нашуд.",
                reply_markup=back_keyboard()
            )
            return

        context.user_data["product"] = {
            "name": product_name,
            "price": price,
            "label": label
        }
        context.user_data["waiting_for"] = "uid"
        if q.message:
            context.user_data["uid_prompt_message_id"] = q.message.message_id
        await q.edit_message_text(
            f"✅ Интихоби шумо:\n{label}\n\n"
            "🆔 UID-и Free Fire-и худро фиристед.\n\n"
            "UID бояд 8–12 рақам бошад.\n"
            "Лутфан UID-и худро дуруст санҷед.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Бекор кардан", callback_data="cancel")],
                [InlineKeyboardButton("🏠 Меню", callback_data="home")]
            ])
        )
        return

    if data == "cancel":
        context.user_data.clear()
        await q.edit_message_text(
            "🏠 Менюи асосӣ\n\nФармоиш бекор шуд. Хизматрасониро интихоб кунед:",
            reply_markup=main_keyboard()
        )
        return

    if data == "order_confirm":
        product = context.user_data.get("product")
        uid = context.user_data.get("uid")
        if not product or not uid:
            await q.edit_message_text(
                "❌ Маълумоти фармоиш гум шудааст. Аз нав оғоз кунед.",
                reply_markup=main_keyboard()
            )
            return

        order_id = new_order_id()
        order = {
            "id": order_id,
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user.full_name,
            "product": product["name"],
            "price": product["price"],
            "uid": uid,
            "payment": "",
            "status": "Интизорӣ барои пардохт",
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "receipt": False
        }
        DB["orders"].append(order)
        save_data()
        context.user_data["order_id"] = order_id

        await q.edit_message_text(
            f"🛒 ФАРМОИШИ ШУМО\n\n"
            f"🔢 Рақами фармоиш: DP-{order_id}\n"
            f"🎮 Маҳсулот: {product['name']}\n"
            f"💰 Маблағ: {money(product['price'])}\n"
            f"🆔 UID: {uid}\n\n"
            "🏦 БА КАДОМ КОРТ МЕХОҲЕД ПАРДОХТ КУНЕД?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏦 Душанбе Сити", callback_data="pay:dcity")],
                [InlineKeyboardButton("💳 Алиф", callback_data="pay:alif")],
                [InlineKeyboardButton("💰 Аз ҳамён пардохт кардан", callback_data="pay:wallet")],
                [InlineKeyboardButton("❌ Бекор кардан", callback_data="cancel")]
            ])
        )
        return

    if data.startswith("pay:"):
        method_key = data.split(":", 1)[1]
        method = "Душанбе Сити" if method_key == "dcity" else "Алиф"
        order_id = context.user_data.get("order_id")
        order = get_order(order_id) if order_id else None
        if not order or order["user_id"] != user.id:
            await q.edit_message_text(
                "❌ Фармоиш ёфт нашуд. Аз /start аз нав оғоз кунед.",
                reply_markup=main_keyboard()
            )
            return

        order["payment"] = method
        order["status"] = "Интизори расиди пардохт"
        save_data()
        context.user_data["waiting_for"] = "receipt"
        context.user_data["order_id"] = order_id

        if method_key == "dcity":
            payment_text = (
                "🏦 ПАРДОХТ — ДУШАНБЕ СИТИ\n\n"
                f"🔢 Фармоиш: DP-{order_id}\n"
                f"💰 Маблағи дақиқ: {money(order['price'])}\n\n"
                f"💳 Рақами корт: {DC_CARD}\n"
                f"👤 Соҳиби корт: {DC_OWNER}\n"
                f"📱 Телефон: {DC_PHONE}\n\n"
                "1️⃣ Маблағи нишон додашударо пардохт кунед.\n"
                "2️⃣ Акси расидро дар ҳамин чат фиристед.\n\n"
                "⏳ Кӯшиш кунед, ки пардохтро дар давоми 10 дақиқа анҷом диҳед.\n"
                "⚠️ Расид танҳо баъди санҷиши админ тасдиқ мешавад."
            )
        else:
            payment_text = (
                "💳 ПАРДОХТ — АЛИФ\n\n"
                f"🔢 Фармоиш: DP-{order_id}\n"
                f"💰 Маблағи дақиқ: {money(order['price'])}\n\n"
                "Реквизитҳои Алифро аз админ гиред.\n"
                "Баъд аз пардохт акси расидро дар ҳамин чат фиристед.\n\n"
                "⚠️ Расид танҳо баъди санҷиши админ тасдиқ мешавад."
            )

        await q.edit_message_text(
            payment_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Бекор кардани фармоиш", callback_data="cancel")],
                [InlineKeyboardButton("🏠 Менюи асосӣ", callback_data="home")]
            ])
        )
        return

    if data.startswith("admin:"):
        if user.id != ADMIN_ID:
            await q.answer("Ин тугма танҳо барои админ аст.", show_alert=True)
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            await q.answer("Дархости нодуруст аст.", show_alert=True)
            return

        _, action, order_id_text = parts
        try:
            order_id = int(order_id_text)
        except ValueError:
            await q.answer("Рақами фармоиш нодуруст аст.", show_alert=True)
            return

        order = get_order(order_id)
        if not order:
            await q.answer("Фармоиш ёфт нашуд.", show_alert=True)
            return

        if action == "complete":
            if order.get("completed"):
                await q.answer("Ин фармоиш аллакай анҷом ёфтааст.", show_alert=True)
                return

            if not (
                order.get("wallet_charged")
                or order.get("status") == "Пардохт тасдиқ шуд — интизори иҷро"
                or order.get("status") == "Пардохт аз ҳамён тасдиқ шуд — интизори иҷро"
            ):
                await q.answer(
                    "Аввал пардохтро тасдиқ кунед.",
                    show_alert=True
                )
                return

            order["completed"] = True
            order["completed_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            order["status"] = "Анҷом ёфт"
            save_data()

            try:
                card = create_completion_card(order)
                await context.bot.send_photo(
                    chat_id=order["user_id"],
                    photo=card,
                    caption=(
                        "✅ Фармоиши шумо анҷом ёфт!\n"
                        f"🔢 Фармоиш: DP-{order_id}\n"
                        f"🎮 Маҳсулот: {order.get('product', '—')}\n"
                        f"🆔 UID: {order.get('uid', '—')}\n"
                        f"💰 Маблағ: {money(order.get('price', 0))}"
                    )
                )
            except Exception:
                # Агар фиристодан ноком шавад, админ метавонад аз нав кӯшиш кунад.
                order["completed"] = False
                order.pop("completed_at", None)
                order["status"] = (
                    "Пардохт аз ҳамён тасдиқ шуд — интизори иҷро"
                    if order.get("wallet_charged")
                    else "Пардохт тасдиқ шуд — интизори иҷро"
                )
                save_data()
                logging.exception("Could not send completion card")
                await q.answer(
                    "Корти расмӣ фиристода нашуд. Хатогиро санҷед ва дубора кӯшиш кунед.",
                    show_alert=True
                )
                return

            await q.answer("Фармоиш анҷом ёфт.")
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        if action == "approve":
            order["status"] = "Пардохт тасдиқ шуд — интизори иҷро"
            msg = (
                f"✅ Пардохти фармоиши DP-{order_id} тасдиқ шуд.\n"
                "Пас аз фиристодани алмос тугмаи анҷомро пахш кунед."
            )
        elif action == "reject":
            order["status"] = "Пардохт рад шуд"
            msg = (
                f"❌ Расиди фармоиши DP-{order_id} рад шуд.\n"
                "Лутфан бо админ тамос гиред."
            )
        else:
            await q.answer("Амали номаълум.", show_alert=True)
            return

        save_data()
        # Паёми такрории тасдиқро ба муштарӣ намефиристем.
        # Ҳолати фармоиш дар база нигоҳ дошта мешавад.

        try:
            caption = q.message.caption or ""
            if action == "approve":
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "💎 Алмос фиристода шуд",
                        callback_data=f"admin:complete:{order_id}"
                    )
                ]])
                suffix = "\n\n✅ Пардохт тасдиқ шуд. Баъди фиристодани алмос тугмаи анҷомро пахш кунед."
            else:
                keyboard = None
                suffix = "\n\n❌ Пардохт рад шуд."

            await q.edit_message_caption(
                caption=caption + suffix,
                reply_markup=keyboard
            )
        except Exception:
            try:
                if action == "approve":
                    await q.edit_message_reply_markup(
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "💎 Алмос фиристода шуд",
                                callback_data=f"admin:complete:{order_id}"
                            )
                        ]])
                    )
                else:
                    await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

        await q.answer("Ҳолати фармоиш нав шуд.")
        return

    if data == "orders":
        orders = [
            o for o in DB["orders"]
            if o["user_id"] == user.id
        ][-10:]
        if not orders:
            await q.edit_message_text(
                "📦 Шумо ҳоло фармоиш надоред.",
                reply_markup=back_keyboard()
            )
            return

        lines = ["📦 ФАРМОИШҲОИ ОХИРИНИ ШУМО\n"]
        for o in reversed(orders):
            lines.append(
                f"🔢 DP-{o['id']}\n"
                f"🎮 {o['product']} — {money(o['price'])}\n"
                f"🆔 UID: {o['uid']}\n"
                f"📌 Ҳолат: {o['status']}\n"
            )
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=back_keyboard()
        )
        return

    if data == "profile":
        username = f"@{user.username}" if user.username else "надорад"
        count = sum(1 for o in DB["orders"] if o["user_id"] == user.id)
        await q.edit_message_text(
            f"👤 ПРОФИЛ\n\n"
            f"Ном: {user.full_name}\n"
            f"Username: {username}\n"
            f"Telegram ID: {user.id}\n"
            f"📦 Шумораи фармоишҳо: {count}",
            reply_markup=back_keyboard()
        )
        return

    if data == "admin":
        text = (
            f"👨‍💻 Админ: @{ADMIN_USERNAME}"
            if ADMIN_USERNAME
            else "👨‍💻 Username-и админ дар bot.env насб нашудааст."
        )
        await q.edit_message_text(text, reply_markup=back_keyboard())
        return

    if data == "info":
        await q.edit_message_text(
            "ℹ️ МАЪЛУМОТ\n\n"
            "• Маҳсулотро интихоб кунед.\n"
            "• UID-и худро бодиққат ворид кунед.\n"
            "• Пардохт кунед ва расидро фиристед.\n"
            "• Админ пардохтро месанҷад.\n\n"
            "⚠️ Донат худкор нест. Пас аз тасдиқи пардохт фармоиш аз ҷониби админ баррасӣ мешавад.\n"
            "Лутфан маълумоти фармоишро дуруст ворид кунед.",
            reply_markup=back_keyboard()
        )
        return

    await q.edit_message_text(
        "Ин интихоб шинохта нашуд. Аз меню аз нав оғоз кунед.",
        reply_markup=main_keyboard()
    )

async def _messages_original(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if context.user_data.get("waiting_for") != "uid":
        await update.message.reply_text(
            "Барои кушодани меню /start-ро пахш кунед.",
            reply_markup=main_keyboard()
        )
        return

    uid = update.message.text.strip()

    if not uid.isdigit() or not 8 <= len(uid) <= 12:
        await update.message.reply_text(
            "❌ UID бояд аз 8 то 12 рақам бошад. Лутфан аз нав фиристед."
        )
        return

    product = context.user_data.get("product")
    if not product:
        context.user_data.clear()
        await update.message.reply_text(
            "❌ Маҳсулот ёфт нашуд. Аз /start аз нав оғоз кунед.",
            reply_markup=main_keyboard()
        )
        return

    context.user_data["uid"] = uid
    context.user_data["waiting_for"] = None

    text = (
        "🛒 ТАСДИҚИ ФАРМОИШ\n\n"
        f"🎮 Маҳсулот: {product['name']}\n"
        f"🆔 UID: {uid}\n"
        f"💰 Нарх: {money(product['price'])}\n\n"
        "⚠️ Никнейм ба таври автоматӣ санҷида намешавад. "
        "Лутфан UID-и худро дар бозӣ санҷед.\n\n"
        "Агар UID ва маҳсулот дуруст бошанд, фармоишро тасдиқ кунед."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Тасдиқ", callback_data="order_confirm")],
        [InlineKeyboardButton("❌ Бекор кардан", callback_data="cancel")]
    ])

    # Саҳифаи пешинаи ботро таҳрир мекунем.
    chat_id = update.effective_chat.id
    prompt_id = context.user_data.get("uid_prompt_message_id")

    if prompt_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=prompt_id,
                text=text,
                reply_markup=keyboard
            )
        except Exception as exc:
            # Агар таҳрир имконнопазир бошад, паёми нав нишон медиҳем.
            logging.warning(
                "Таҳрири саҳифаи UID ноком шуд: %s",
                type(exc).__name__
            )
            await update.message.reply_text(
                text,
                reply_markup=keyboard
            )
    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard
        )

    # Барои тоза нигоҳ доштани чат, паёми UID-ро нест карданӣ мешавем.
    try:
        await update.message.delete()
    except Exception as exc:
        logging.debug(
            "Паёми UID нест карда нашуд: %s",
            type(exc).__name__
        )


async def receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_for") != "receipt":
        await update.message.reply_text(
            "Барои фиристодани расид аввал фармоиш созед ва усули пардохтро интихоб кунед.",
            reply_markup=main_keyboard()
        )
        return

    if not ADMIN_ID:
        await update.message.reply_text(
            "❌ ADMIN_ID дар bot.env дуруст насб нашудааст. Ба админ хабар диҳед."
        )
        return

    order_id = context.user_data.get("order_id")
    order = get_order(order_id) if order_id else None
    user = update.effective_user

    if not order or order["user_id"] != user.id:
        context.user_data.pop("waiting_for", None)
        await update.message.reply_text(
            "❌ Фармоиш ёфт нашуд. Аз /start аз нав оғоз кунед.",
            reply_markup=main_keyboard()
        )
        return

    photo_id = update.message.photo[-1].file_id
    caption = (
        f"🧾 РАСИД — БАРОИ САНҶИШ\n\n"
        f"🔢 Фармоиш: DP-{order['id']}\n"
        f"🎮 Маҳсулот: {order['product']}\n"
        f"💰 Маблағ: {money(order['price'])}\n"
        f"🆔 UID: {order['uid']}\n"
        f"💳 Усул: {order['payment']}\n"
        f"👤 Корбар: {user.full_name}\n"
        f"Username: @{user.username if user.username else 'надорад'}\n"
        f"Telegram ID: {user.id}\n\n"
        "⚠️ Пеш аз тасдиқ пардохтро воқеан санҷед."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Тасдиқ", callback_data=f"admin:approve:{order['id']}"
            ),
            InlineKeyboardButton(
                "❌ Рад кардан", callback_data=f"admin:reject:{order['id']}"
            )
        ]
    ])

    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=caption,
            reply_markup=keyboard
        )
    except Exception:
        logging.exception("Could not send receipt to admin")
        await update.message.reply_text(
            "❌ Расид фиристода нашуд. Баъдтар кӯшиш кунед."
        )
        return

    order["receipt"] = True
    order["status"] = "Расид фиристода шуд — интизори санҷиш"
    save_data()
    context.user_data.pop("waiting_for", None)

    await update.message.reply_text(
        f"✅ Расиди фармоиши DP-{order['id']} ба админ фиристода шуд.\n\n"
        "📌 Пас аз санҷиши пардохт админ ба шумо хабар медиҳад.",
        reply_markup=main_keyboard()
    )






def create_completion_card(order):
    from io import BytesIO
    from datetime import datetime
    from PIL import Image, ImageDraw, ImageFont
    import re

    W, H = 1080, 1500

    # Заминаи кӯтоҳ, торик ва неонӣ
    img = Image.new("RGB", (W, H), "#080d1d")
    d = ImageDraw.Draw(img)

    for y in range(H):
        t = y / H
        color = (
            int(7 + 10 * t),
            int(12 + 5 * t),
            int(29 + 23 * (1 - t))
        )
        d.line((0, y, W, y), fill=color)

    # Нурҳои ороишӣ
    d.ellipse((680, -180, 1200, 340), fill="#101c42")
    d.ellipse((-180, 950, 350, 1480), fill="#101735")

    # Чорчӯба
    d.rounded_rectangle(
        (18, 18, W - 18, H - 18),
        radius=38, outline="#43e5ff", width=4
    )
    d.rounded_rectangle(
        (31, 31, W - 31, H - 31),
        radius=31, outline="#7957ff", width=2
    )

    regular_paths = [
        "/system/fonts/NotoSans-Regular.ttf",
        "/system/fonts/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    bold_paths = [
        "/system/fonts/NotoSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]

    def load_font(paths, size):
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    logo_font = load_font(bold_paths, 42)
    title_font = load_font(bold_paths, 43)
    heading_font = load_font(bold_paths, 29)
    label_font = load_font(regular_paths, 22)
    value_font = load_font(bold_paths, 25)
    small_font = load_font(regular_paths, 19)
    diamond_font = load_font(bold_paths, 57)

    # Сарлавҳа
    d.rounded_rectangle(
        (52, 52, W - 52, 240),
        radius=30, fill="#111c36",
        outline="#43e5ff", width=3
    )

    # Алмос ва тоҷ
    cx, cy = 133, 148
    d.polygon(
        [
            (cx - 39, cy - 9),
            (cx - 23, cy - 32),
            (cx + 23, cy - 32),
            (cx + 39, cy - 9),
            (cx, cy + 34)
        ],
        fill="#53e6ff", outline="#ffffff"
    )
    d.line(
        [(cx - 23, cy - 32), (cx, cy + 34),
         (cx + 23, cy - 32)],
        fill="#ffffff", width=2
    )
    d.line(
        [(cx - 39, cy - 9), (cx + 39, cy - 9)],
        fill="#ffffff", width=2
    )
    d.polygon(
        [
            (cx - 32, cy - 39),
            (cx - 22, cy - 58),
            (cx - 8, cy - 42),
            (cx, cy - 65),
            (cx + 10, cy - 42),
            (cx + 23, cy - 58),
            (cx + 33, cy - 39),
            (cx + 27, cy - 30),
            (cx - 27, cy - 30)
        ],
        fill="#ffd66b", outline="#ffffff"
    )

    d.text(
        (192, 90), "DANATI_PATSANI",
        font=logo_font, fill="#ffffff"
    )
    d.text(
        (195, 145), "PREMIUM DIAMOND SERVICE",
        font=small_font, fill="#71eaff"
    )
    d.rounded_rectangle(
        (194, 180, 495, 218),
        radius=16, fill="#24365a",
        outline="#7957ff", width=2
    )
    d.text(
        (211, 187), "OFFICIAL ORDER CARD",
        font=small_font, fill="#ffffff"
    )

    # Ҳолати фармоиш
    d.rounded_rectangle(
        (52, 260, W - 52, 355),
        radius=25, fill="#10372f",
        outline="#4dffc1", width=3
    )
    d.ellipse(
        (76, 281, 121, 326),
        outline="#65ffc2", width=4
    )
    d.line(
        [(86, 303), (98, 314), (113, 291)],
        fill="#65ffc2", width=5
    )
    d.text(
        (143, 275), "ФАРМОИШ АНҶОМ ЁФТ",
        font=heading_font, fill="#70ffc8"
    )
    d.text(
        (145, 317), "DIAMONDS SENT SUCCESSFULLY",
        font=small_font, fill="#d0ffeb"
    )

    # Маълумоти фармоиш
    order_id = str(order.get("id", "—"))
    uid = str(order.get("uid", "—"))
    product_value = order.get("product", "—")

    if isinstance(product_value, dict):
        product = str(
            product_value.get("name")
            or product_value.get("title")
            or product_value.get("diamonds")
            or "—"
        )
    else:
        product = str(product_value or "—")

    payment = str(order.get("payment", "—"))

    try:
        price = str(money(order.get("price", 0)))
    except Exception:
        price = str(order.get("price", 0))

    diamonds = (
        order.get("diamonds")
        or order.get("amount")
        or order.get("count")
    )

    if not diamonds:
        match = re.search(
            r"([\d,]+)\s*(?:алмаз|diamond|diamonds)",
            product, re.IGNORECASE
        )
        diamonds = (
            match.group(1).replace(",", "")
            if match else product
        )

    diamonds_text = str(diamonds)
    if diamonds_text.isdigit():
        diamonds_text += " DIAMONDS"

    completed_at = order.get("completed_at")
    if not completed_at or str(completed_at) == "None":
        completed_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    else:
        completed_at = str(completed_at)

    # Панели алмос
    d.rounded_rectangle(
        (52, 375, W - 52, 555),
        radius=30, fill="#111f3d",
        outline="#57dfff", width=3
    )
    d.text(
        (82, 395), "YOUR DIAMONDS",
        font=heading_font, fill="#80eaff"
    )

    # Алмоси калон
    d.polygon(
        [(88, 485), (112, 454), (156, 454),
         (180, 485), (134, 525)],
        fill="#58e7ff", outline="#ffffff"
    )
    d.line(
        [(112, 454), (123, 485), (156, 454)],
        fill="#ffffff", width=2
    )
    d.line(
        [(88, 485), (180, 485)],
        fill="#ffffff", width=2
    )
    d.line(
        [(123, 485), (134, 525), (145, 485)],
        fill="#ffffff", width=2
    )

    d.text(
        (215, 452), diamonds_text,
        font=diamond_font, fill="#ffffff"
    )
    d.text(
        (220, 515), "PREMIUM DELIVERY",
        font=small_font, fill="#b9c9f2"
    )

    # Ҷадвали маълумот: 6 қатор дар фазои кӯтоҳ
    rows = [
        ("РАҚАМИ ФАРМОИШ", f"DP-{order_id}"),
        ("UID-И БОЗИНГАР", uid),
        ("МАҲСУЛОТ", product),
        ("УСУЛИ ПАРДОХТ", payment),
        ("НАРХ", price),
        ("САНА ВА ВАҚТ", completed_at),
    ]

    def fit_text(value, font, max_width):
        value = str(value)
        if d.textbbox((0, 0), value, font=font)[2] <= max_width:
            return value
        while value and d.textbbox(
            (0, 0), value + "...", font=font
        )[2] > max_width:
            value = value[:-1]
        return (value + "...") if value else "..."

    y = 575
    row_h = 91

    for label, value in rows:
        d.rounded_rectangle(
            (52, y, W - 52, y + 76),
            radius=19, fill="#111a30",
            outline="#2c466a", width=2
        )
        d.text(
            (76, y + 9), label,
            font=label_font, fill="#9eb7df"
        )

        value = fit_text(value, value_font, 470)
        box = d.textbbox((0, 0), value, font=value_font)
        value_width = box[2] - box[0]

        d.text(
            (W - 76 - value_width, y + 40),
            value, font=value_font, fill="#ffffff"
        )
        y += row_h

    # Поён
    d.line(
        (100, 1135, W - 100, 1135),
        fill="#43e5ff", width=3
    )
    d.text(
        (W // 2, 1160), "DANATI_PATSANI",
        font=heading_font, fill="#ffffff", anchor="mt"
    )
    d.text(
        (W // 2, 1205), "Ташаккур барои харид!",
        font=label_font, fill="#8cecff", anchor="mt"
    )
    d.text(
        (W // 2, 1245), "PREMIUM • TRUST • QUALITY",
        font=small_font, fill="#aab8d7", anchor="mt"
    )

    # Нишони алмос дар поён
    center_x, center_y = W // 2, 1350
    d.ellipse(
        (center_x - 42, center_y - 42,
         center_x + 42, center_y + 42),
        outline="#43e5ff", width=3
    )
    d.ellipse(
        (center_x - 31, center_y - 31,
         center_x + 31, center_y + 31),
        outline="#7957ff", width=2
    )
    d.polygon(
        [(center_x, center_y - 23),
         (center_x + 22, center_y),
         (center_x, center_y + 23),
         (center_x - 22, center_y)],
        fill="#5be8ff", outline="#ffffff"
    )

    output = BytesIO()
    img.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = f"DP-{order_id}-completed.png"
    return output
def wallet_balance(user_id):
    try:
        value = DB.setdefault("wallets", {}).get(str(user_id), 0)
        return max(0.0, float(value))
    except (TypeError, ValueError):
        logging.exception("Invalid wallet balance for user %s", user_id)
        return 0.0

def wallet_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Пур кардани ҳамён", callback_data="wallet_add")],
        [InlineKeyboardButton("🏠 Менюи асосӣ", callback_data="home")]
    ])

async def wallet_buttons(update, context):
    q = update.callback_query
    if q is None:
        return await _buttons_original(update, context)

    data = q.data or ""

    if data == "referral":
        user_id = str(q.from_user.id)
        refs = DB.setdefault("referrals", {})
        refs.setdefault("invited_by", {})
        refs.setdefault("earned_diram", {})

        bot_info = await context.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

        invited = sum(
            1 for inviter in refs["invited_by"].values()
            if str(inviter) == user_id
        )

        earned_diram = int(refs["earned_diram"].get(user_id, 0))

        await q.message.reply_text(
            "👥 ДАЪВАТИ ДӮСТОН\n\n"
            "🎁 Барои ҳар дӯсти нави даъватшуда: 50 дирам\n"
            f"👤 Дӯстони даъватшуда: {invited}\n"
            f"💰 Мукофотҳои ҷамъшуда: {earned_diram} дирам "
            f"({earned_diram / 100:.2f} сомонӣ)\n\n"
            "🔗 Пайванди шахсии шумо:\n"
            f"{link}\n\n"
            "📨 Ин пайвандро ба дӯстони худ фиристед!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Менюи асосӣ", callback_data="home")]
            ]),
            disable_web_page_preview=True
        )
        return

    user = q.from_user

    if data == "wallet":
        await q.answer()
        await q.edit_message_text(
            f"💰 ҲАМЁНИ МАН\n\nБаланс: {money(wallet_balance(user.id))}\n\n"
            "Пул танҳо баъди тасдиқи пардохт аз ҷониби админ ба баланс илова мешавад.",
            reply_markup=wallet_keyboard()
        )
        return

    if data == "wallet_add":
        await q.answer()
        context.user_data["waiting_for"] = "wallet_amount"
        await q.edit_message_text(
            "➕ ПУР КАРДАНИ ҲАМЁН\n\n"
            "Маблағро бо сомонӣ фиристед (танҳо рақам, масалан 50).\n"
            "Пас аз он реквизит ва тартиби фиристодани расид нишон дода мешавад.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Бекор кардан", callback_data="cancel")],
                [InlineKeyboardButton("🏠 Меню", callback_data="home")]
            ])
        )
        return

    if data.startswith("admin_wallet:"):
        if user.id != ADMIN_ID:
            await q.answer("Ин тугма танҳо барои админ аст.", show_alert=True)
            return
        parts = data.split(":")
        if len(parts) != 3 or parts[1] not in ("approve", "reject"):
            await q.answer("Дархости нодуруст.", show_alert=True)
            return
        action, topup_id = parts[1], parts[2]
        topup = next((x for x in DB.get("topups", [])
                      if str(x.get("id")) == topup_id), None)
        if not topup:
            await q.answer("Дархост ёфт нашуд.", show_alert=True)
            return
        if topup.get("status") != "pending":
            await q.answer("Ин дархост қаблан баррасӣ шудааст.", show_alert=True)
            return

        if action == "approve":
            wallets = DB.setdefault("wallets", {})
            key = str(topup["user_id"])
            wallets[key] = wallet_balance(topup["user_id"]) + int(topup["amount"])
            topup["status"] = "approved"
            notice = (
                f"✅ Пур кардани ҳамён тасдиқ шуд.\n"
                f"➕ Илова шуд: {money(topup['amount'])}\n"
                f"💰 Баланси нав: {money(wallet_balance(topup['user_id']))}"
            )
            result = "✅ Пур кардани ҳамён тасдиқ шуд."
        else:
            topup["status"] = "rejected"
            notice = "❌ Дархости пур кардани ҳамён рад шуд. Барои маълумот бо админ тамос гиред."
            result = "❌ Дархост рад шуд."

        save_data()
        await q.answer(result)
        try:
            await context.bot.send_message(chat_id=topup["user_id"], text=notice)
        except Exception:
            logging.exception("Wallet notification failed")
        try:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + "\n\n" + result,
                reply_markup=None
            )
        except Exception:
            pass
        return

    if data == "pay:wallet":
        await q.answer()
        order_id = context.user_data.get("order_id")
        order = get_order(order_id) if order_id else None
        if not order or order.get("user_id") != user.id:
            await q.edit_message_text(
                "❌ Фармоиш ёфт нашуд. Аз нав оғоз кунед.",
                reply_markup=main_keyboard()
            )
            return
        if order.get("wallet_charged"):
            await q.edit_message_text(
                "✅ Ин фармоиш аллакай аз ҳамён пардохт шудааст.",
                reply_markup=main_keyboard()
            )
            return
        price = int(order.get("price", 0))
        balance = wallet_balance(user.id)
        if price <= 0 or balance < price:
            await q.edit_message_text(
                f"❌ Баланс кофӣ нест.\n\n"
                f"💰 Баланс: {money(balance)}\n"
                f"🛒 Нарх: {money(price)}\n\n"
                "Аввал ҳамёнро пур кунед ё усули дигари пардохтро интихоб намоед.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Пур кардани ҳамён", callback_data="wallet_add")],
                    [InlineKeyboardButton("🏠 Меню", callback_data="home")]
                ])
            )
            return

        # Тарҳи баланс ва сабти фармоиш дар як амалиёт захира мешаванд.
        DB["wallets"][str(user.id)] = balance - price
        order["wallet_charged"] = True
        order["payment"] = "Ҳамён"
        order["status"] = "Пардохт аз ҳамён тасдиқ шуд — интизори иҷро"
        save_data()

        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"🛒 ФАРМОИШИ НАВ DP-{order['id']}\n\n"
                        f"🎮 Маҳсулот: {order['product']}\n"
                        f"💰 Пардохт аз ҳамён: {money(price)}\n"
                        f"🆔 UID: {order['uid']}\n"
                        f"👤 Корбар: {order.get('full_name', '')}\n"
                        f"Telegram ID: {user.id}\n\n"
                        "Пардохт аз ҳамён сабт шуд. Фармоишро барои иҷро баррасӣ кунед."
                    )
                )
            except Exception:
                logging.exception("Could not notify admin about wallet order")

        await q.edit_message_text(
            f"✅ Пардохт аз ҳамён сабт шуд!\n\n"
            f"🔢 Фармоиш: DP-{order['id']}\n"
            f"🎮 Маҳсулот: {order['product']}\n"
            f"💰 Пардохт: {money(price)}\n"
            f"💵 Баланси боқимонда: {money(wallet_balance(user.id))}\n\n"
            "Фармоиш ҳоло интизори баррасии админ аст.",
            reply_markup=main_keyboard()
        )
        return

    return await _buttons_original(update, context)


async def wallet_messages(update, context):
    message = update.effective_message
    if not message or not message.text:
        return await _messages_original(update, context)

    if context.user_data.get("waiting_for") != "wallet_amount":
        return await _messages_original(update, context)

    raw = message.text.strip()
    if not raw.isdigit():
        await message.reply_text("❌ Маблағро танҳо бо рақам фиристед, масалан 50.")
        return
    amount = int(raw)
    if amount < 1 or amount > 10000:
        await message.reply_text("❌ Маблағ бояд аз 1 то 10000 сомонӣ бошад.")
        return
    if not ADMIN_ID:
        await message.reply_text("❌ ADMIN_ID насб нашудааст. Ҳоло дархост фиристода намешавад.")
        context.user_data.pop("waiting_for", None)
        return

    topup_id = str(int(time.time() * 1000))
    topup = {
        "id": topup_id,
        "user_id": message.from_user.id,
        "username": message.from_user.username or "",
        "amount": amount,
        "status": "pending",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "receipt": False
    }
    DB.setdefault("topups", []).append(topup)
    save_data()
    context.user_data["wallet_topup_id"] = topup_id
    context.user_data["waiting_for"] = "wallet_receipt"

    await message.reply_text(
        f"➕ ПУР КАРДАНИ ҲАМЁН\n\n"
        f"💰 Маблағ: {money(amount)}\n"
        f"💳 Корт: {DC_CARD}\n"
        f"👤 Соҳиби корт: {DC_OWNER}\n"
        f"📱 Телефон: {DC_PHONE}\n\n"
        "Пас аз пардохти воқеӣ акси расидро дар ҳамин чат фиристед.\n"
        "Баланс танҳо баъди санҷиши админ зиёд мешавад.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Меню", callback_data="home")]
        ])
    )


async def wallet_receipt_photo(update, context):
    if context.user_data.get("waiting_for") != "wallet_receipt":
        return await receipt_photo(update, context)

    message = update.effective_message
    user = update.effective_user
    topup_id = context.user_data.get("wallet_topup_id")
    topup = next((x for x in DB.get("topups", [])
                  if str(x.get("id")) == str(topup_id)), None)

    if not topup or topup.get("user_id") != user.id or topup.get("status") != "pending":
        context.user_data.pop("waiting_for", None)
        await message.reply_text("❌ Дархост ёфт нашуд. Аз меню аз нав оғоз кунед.")
        return
    if topup.get("receipt"):
        await message.reply_text("⏳ Расид аллакай фиристода шудааст ва интизори санҷиши админ аст.")
        return
    if not message.photo:
        await message.reply_text("❌ Лутфан акси расиди пардохтро фиристед.")
        return

    caption = (
        "🧾 РАСИДИ ПУР КАРДАНИ ҲАМЁН\n\n"
        f"🔢 Дархост: {topup_id}\n"
        f"💰 Маблағ: {money(topup['amount'])}\n"
        f"👤 Корбар: {user.full_name}\n"
        f"Username: @{user.username if user.username else 'надорад'}\n"
        f"Telegram ID: {user.id}\n\n"
        "⚠️ Пеш аз тасдиқ пардохтро воқеан санҷед."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Қабул кардан", callback_data=f"admin_wallet:approve:{topup_id}"),
        InlineKeyboardButton("❌ Рад кардан", callback_data=f"admin_wallet:reject:{topup_id}")
    ]])
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=message.photo[-1].file_id,
            caption=caption, reply_markup=keyboard
        )
    except Exception:
        logging.exception("Wallet receipt could not be sent")
        await message.reply_text("❌ Расид ба админ фиристода нашуд. Баъдтар кӯшиш кунед.")
        return

    topup["receipt"] = True
    save_data()
    context.user_data.pop("waiting_for", None)
    await message.reply_text(
        "✅ Расид ба админ фиристода шуд. Баланс баъди тасдиқи пардохт зиёд мешавад.",
        reply_markup=main_keyboard()
    )


async def error_handler(update, context):
    error = context.error
    if isinstance(error, (TimedOut, NetworkError)):
        logging.warning("Хатои муваққатии шабакаи Telegram: %s", type(error).__name__)
    else:
        logging.error(
            "Хатои коркарди навсозӣ",
            exc_info=(type(error), error, error.__traceback__)
        )



async def lookup_ff_nickname(uid):
    """Ҷустуҷӯи номи бозигари Free Fire аз рӯйи UID."""
    import httpx
    import logging

    uid = str(uid).strip()

    if not uid.isdigit() or not 5 <= len(uid) <= 20:
        return None

    regions = [
        "ME", "CIS", "RU", "IND", "SG", "BD",
        "PK", "ID", "BR", "US", "TH", "VN", "TW"
    ]

    urls = [
        "https://freefireapis.lat/info-player",
        "https://api2.nftoken.info/get",
    ]

    try:
        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers={"User-Agent": "DANATI-PATSANI/1.0"},
        ) as client:

            for url in urls:
                for region in regions:
                    try:
                        response = await client.get(
                            url,
                            params={"uid": uid, "region": region},
                        )

                        if response.status_code != 200:
                            continue

                        data = response.json()
                        if not isinstance(data, dict):
                            continue

                        # Ҷавобҳои API-ҳои гуногунро мехонем.
                        result = data.get("result", data)
                        if not isinstance(result, dict):
                            continue

                        info = (
                            result.get("basicInfo")
                            or result.get("basicinfo")
                            or result.get("accountInfo")
                            or result.get("accountinfo")
                            or result
                        )

                        if not isinstance(info, dict):
                            continue

                        name = (
                            info.get("nickname")
                            or info.get("accountName")
                            or info.get("accountname")
                            or info.get("Nickname")
                        )

                        found_uid = (
                            info.get("accountId")
                            or info.get("accountid")
                            or info.get("uid")
                            or info.get("UID")
                        )

                        if not isinstance(name, str) or not name.strip():
                            continue

                        # UID-и дигарро қабул намекунем.
                        if found_uid is not None and str(found_uid) != uid:
                            continue

                        logging.info(
                            "Free Fire UID lookup successful in %s",
                            region,
                        )
                        return name.strip()

                    except (
                        httpx.HTTPError,
                        ValueError,
                        TypeError,
                        AttributeError,
                    ):
                        continue

    except httpx.HTTPError as exc:
        logging.warning(
            "Free Fire lookup connection failed: %s",
            type(exc).__name__,
        )

    return None


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN дар bot.env ёфт нашуд.")
    if not ADMIN_ID:
        logging.warning("ADMIN_ID насб нашудааст; расидҳо ба админ фиристода намешаванд.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(30)
        .pool_timeout(30)
        .connection_pool_size(32)
        .get_updates_connection_pool_size(4)
        .get_updates_pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(wallet_buttons))
    app.add_handler(MessageHandler(filters.PHOTO, wallet_receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_messages))

    app.add_error_handler(error_handler)
    print("DANATI_PATSANI is running...")
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(
        poll_interval=0,
        timeout=30,
        bootstrap_retries=-1,
        drop_pending_updates=False,
    )



# UID_LOOKUP_UPDATE_V1
if __name__ == "__main__":
    main()
