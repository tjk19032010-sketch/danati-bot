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
            f"👤 ПР
