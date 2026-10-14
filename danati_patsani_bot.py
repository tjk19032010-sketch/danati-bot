# -*- coding: utf-8 -*-
import os
import json
import time
import asyncio
import logging
from io import BytesIO
from pathlib import Path
from datetime import datetime

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from telegram import Update
from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram.error import NetworkError
from telegram.error import TimedOut
from telegram.ext import Application
from telegram.ext import CommandHandler
from telegram.ext import CallbackQueryHandler
from telegram.ext import MessageHandler
from telegram.ext import ContextTypes
from telegram.ext import filters

logging.basicConfig(level=logging.INFO)

BASE = Path(file).parent
DATA_FILE = BASE / "danati_orders.json"

BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
ADMIN_ID = os.getenv("ADMIN_ID","").strip()
ADMIN_ID = int(ADMIN_ID) if ADMIN_ID.isdigit() else 0
ADMIN_USER = os.getenv("ADMIN_USERNAME","").lstrip("@")

DC_CARD = "9762 0002 6172 0113"
DC_OWNER = "Jumaboi Khojazoda"
DC_PHONE = "989047040"

DIAMONDS = [
  ("110 алмаз - 9 сомон", "110 алмаз", 9),
  ("341 алмаз - 28 сомон", "341 алмаз", 28),
  ("572 алмаз - 48 сомон", "572 алмаз", 48),
  ("1166 алмаз - 90 сомон", "1166 алмаз", 90),
  ("2398 алмаз - 180 сомон", "2398 алмаз", 180),
  ("6160 алмаз - 455 сомон", "6160 алмаз", 455),
]
VOUCHERS = [
  ("Lite 90 - 6 сомон", "Lite 90", 6),
  ("Хафта 450 - 17 сомон", "Хафта 450", 17),
  ("Мохона 2600 - 70 сомон", "Мохона 2600", 70),
]
LEVELS = [
  ("Level 6 - 6 сомон", "Level 6", 6),
  ("Level 10 - 9 сомон", "Level 10", 9),
  ("Level 15 - 9 сомон", "Level 15", 9),
  ("Level 20 - 9 сомон", "Level 20", 9),
  ("Level 25 - 9 сомон", "Level 25", 9),
  ("Level 30 - 10 сомон", "Level 30", 10),
]

def load_data():
    if not DATA_FILE.exists():
        return {"orders":[], "next_id":1001, "wallets":{}, "topups":[], "referrals":{"seen_users":[],"invited_by":{},"earned_diram":{}}}
    try:
        d=json.loads(DATA_FILE.read_text(encoding="utf-8"))
        d.setdefault("orders",[])
        d.setdefault("wallets",{})
        d.setdefault("topups",[])
        d.setdefault("next_id",1001)
        d.setdefault("referrals",{"seen_users":[],"invited_by":{},"earned_diram":{}})
        return d
    except:
        return {"orders":[], "next_id":1001, "wallets":{}, "topups":[], "referrals":{"seen_users":[],"invited_by":{},"earned_diram":{}}}

DB=load_data()

def save_data():
    tmp=DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DB, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)

def new_id():
    n=DB["next_id"]
    DB["next_id"]+=1
    save_data()
    return n

def money(a):
    return f"{a} сомони"

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Донат кардан", callback_data="catalog")],
        [InlineKeyboardButton("Хамёни ман", callback_data="wallet")],
        [InlineKeyboardButton("Даъвати дустон", callback_data="referral")],
        [InlineKeyboardButton("Фармоишхо", callback_data="orders"), InlineKeyboardButton("Профил", callback_data="profile")],
        [InlineKeyboardButton("Админ", callback_data="admin"), InlineKeyboardButton("Маълумот", callback_data="info")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Менюи асоси", callback_data="home")]])

def prod_kb(items, pref):
    rows=[[InlineKeyboardButton(l, callback_data=f"{pref}:{i}")] for i,(l,_,_) in enumerate(items)]
    rows.append([InlineKeyboardButton("Категория", callback_data="catalog")])
    rows.append([InlineKeyboardButton("Меню", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def get_order(oid):
    return next((o for o in DB["orders"] if o["id"]==oid), None)

def wallet_bal(uid):
    try:
        return max(0.0, float(DB.get("wallets",{}).get(str(uid),0)))
    except:
        return 0.0def create_card(order):
    W,H=1080,1200
    img=Image.new("RGB",(W,H),"#111111")
    d=ImageDraw.Draw(img)
    d.rectangle((20,20,W-20,H-20), outline="#ff8a24", width=4)
    f=ImageFont.load_default()
    d.text((100,100), "DANATI_PATSANI", fill="white", font=f)
    d.text((100,200), f"Order DP-{order.get('id','')}", fill="white", font=f)
    d.text((100,300), f"Product {order.get('product','')}", fill="white", font=f)
    d.text((100,400), f"UID {order.get('uid','')}", fill="white", font=f)
    d.text((100,500), "Пардохт тасдик шуд!", fill="#ff8a24", font=f)
    out=BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    out.name="card.png"
    return out

async def start(update, context):
    uid=str(update.effective_user.id)
    refs=DB.setdefault("referrals",{})
    refs.setdefault("seen_users",[])
    refs.setdefault("invited_by",{})
    refs.setdefault("earned_diram",{})
    if uid not in refs["seen_users"]:
        refs["seen_users"].append(uid)
        args=context.args or []
        if args and args[0].startswith("ref_"):
            inv=args[0][4:]
            if inv.isdigit() and inv!=uid and uid not in refs["invited_by"]:
                refs["invited_by"][uid]=inv
                refs["earned_diram"][inv]=int(refs["earned_diram"].get(inv,0))+50
                DB.setdefault("wallets",{})[inv]=float(DB["wallets"].get(inv,0))+0.50
                try:
                    await context.bot.send_message(chat_id=int(inv), text="Дусти нав! +0.5 сомони ба хамёнатон!")
                except:
                    pass
        save_data()
    context.user_data.clear()
    await update.message.reply_text("DANATI_PATSANI\nХуш омадед! Махсулотро интихоб кунед:", reply_markup=main_kb())

async def buttons(update, context):
    q=update.callback_query
    await q.answer()
    data=q.data or ""
    user=q.from_user

    if data=="home":
        await q.edit_message_text("Менюи асоси:", reply_markup=main_kb())
        return
    if data=="catalog":
        await q.edit_message_text("Категория:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Алмазхо", callback_data="diamonds")],
            [InlineKeyboardButton("Ваучерхо", callback_data="vouchers")],
            [InlineKeyboardButton("Level Up", callback_data="levels")],
            [InlineKeyboardButton("Меню", callback_data="home")]]))
        return
    if data in ("diamonds","vouchers","levels"):
        mp={"diamonds":(DIAMONDS,"Алмазхо","diamond"), "vouchers":(VOUCHERS,"Ваучерхо","voucher"), "levels":(LEVELS,"Level Up","level")}
        items,title,pref=mp[data]
        await q.edit_message_text(f"{title}:", reply_markup=prod_kb(items,pref))
        return
    if ":" in data and data.split(":")[0] in ("diamond","voucher","level"):
        pref,idx=data.split(":",1)
        items={"diamond":DIAMONDS,"voucher":VOUCHERS,"level":LEVELS}[pref]
        label,name,price=items[int(idx)]
        context.user_data["product"]={"name":name,"price":price,"label":label}
        context.user_data["waiting_for"]="uid"
        context.user_data["uid_msg"]=q.message.message_id
        await q.edit_message_text(f"Интихоб: {label}\nUID-и худро фиристед (8-12 ракам):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Бекор", callback_data="cancel")]]))
        return
    if data=="cancel":
        context.user_data.clear()
        await q.edit_message_text("Бекор шуд:", reply_markup=main_kb())
        return
    if data=="order_confirm":
        prod=context.user_data.get("product")
        uid=context.user_data.get("uid")
        if not prod or not uid:
            await q.edit_message_text("Хатоги, аз нав /start", reply_markup=main_kb())
            return
        oid=new_id()
        order={"id":oid,"user_id":user.id,"username":user.username or "","full_name":user.full_name,"product":prod["name"],"price":prod["price"],"uid":uid,"payment":"","status":"Интизори пардохт","created":datetime.now().strftime("%Y-%m-%d %H:%M"),"receipt":False}DB["orders"].append(order)
        save_data()
        context.user_data["order_id"]=oid
        await q.edit_message_text(f"Фармоиш DP-{oid}\nМахсулот {prod['name']}\nМаблаг {money(prod['price'])}\nUID {uid}\n\nКорти пардохт интихоб кун:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Душанбе Сити", callback_data="pay:dc")],[InlineKeyboardButton("Хамён", callback_data="pay:wallet")],[InlineKeyboardButton("Бекор", callback_data="cancel")]]))
        return
    if data.startswith("pay:"):
        meth=data.split(":")[1]
        oid=context.user_data.get("order_id")
        order=get_order(oid)
        if not order:
            await q.edit_message_text("Фармоиш нест", reply_markup=main_kb())
            return
        if meth=="wallet":
            bal=wallet_bal(user.id)
            if bal < order["price"]:
                await q.edit_message_text(f"Баланс кам: {money(bal)} Нарх {money(order['price'])}", reply_markup=main_kb())
                return
            DB["wallets"][str(user.id)]=bal-order["price"]
            order["payment"]="Хамён"
            order["status"]="Аз хамён пардохт - интизори админ"
            save_data()
            await q.edit_message_text(f"Аз хамён пардохт шуд DP-{oid}", reply_markup=main_kb())
            if ADMIN_ID:
                try:
                    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Фармоиш DP-{oid} аз хамён")
                except:
                    pass
            return
        order["payment"]="Душанбе Сити"
        order["status"]="Интизори расид"
        save_data()
        context.user_data["waiting_for"]="receipt"
        await q.edit_message_text(f"Пардохт Душанбе Сити\nФармоиш DP-{oid}\nМаблаг {money(order['price'])}\n\nКорт: {DC_CARD}\nСохиб: {DC_OWNER}\n\nАкси расидро фирист!", reply_markup=back_kb())
        return
    if data=="wallet":
        await q.edit_message_text(f"Хамён: {money(wallet_bal(user.id))}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Пур кардан", callback_data="wallet_add")],[InlineKeyboardButton("Меню", callback_data="home")]]))
        return
    if data=="wallet_add":
        await q.edit_message_text("Усул:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Душанбе Сити", callback_data="wallet_method:dc")],[InlineKeyboardButton("Меню", callback_data="home")]]))
        return
    if data.startswith("wallet_method:"):
        context.user_data["waiting_for"]="wallet_amount"
        await q.edit_message_text("Маблагро нависед, мисол 50:", reply_markup=back_kb())
        return
    if data=="referral":
        bot=await context.bot.get_me()
        link=f"https://t.me/{bot.username}?start=ref_{user.id}"
        refs=DB.get("referrals",{})
        inv=sum(1 for v in refs.get("invited_by",{}).values() if str(v)==str(user.id))
        earn=int(refs.get("earned_diram",{}).get(str(user.id),0))
        await q.edit_message_text(f"Даъвати дустон\nДусти даъват: {inv}\nМукофот: {earn} дирам\n\nЛинк:\n{link}", reply_markup=back_kb())
        return
    if data=="orders":
        orders=[o for o in DB["orders"] if o["user_id"]==user.id][-10:]
        if not orders:
            await q.edit_message_text("Фармоиш нест", reply_markup=back_kb())
        else:
            txt="\n".join([f"DP-{o['id']} {o['product']} {o['status']}" for o in reversed(orders)])
            await q.edit_message_text(txt, reply_markup=back_kb())
        return
    if data=="profile":
        c=sum(1 for o in DB["orders"] if o["user_id"]==user.id)
        await q.edit_message_text(f"Профил\nНом {user.full_name}\nID {user.id}\nФармоиш {c}", reply_markup=back_kb())
        return
    if data.startswith("admin:"):
        if user.id!=ADMIN_ID:
            await q.answer("Танхо админ", show_alert=True)
            return
        _,act,oid=data.split(":",2)oid=int(oid)
        order=get_order(oid)
        if not order:
            return
        if act=="approve":
            order["status"]="Тасдик шуд"
        elif act=="reject":
            order["status"]="Рад шуд"
        elif act=="complete":
            order["status"]="Анчом ёфт"
            order["completed"]=True
            try:
                card=create_card(order)
                await context.bot.send_photo(chat_id=order["user_id"], photo=card, caption=f"Фармоиш DP-{oid} анчом ёфт!")
            except:
                pass
        save_data()
        await q.answer("Нав шуд")
        return

async def messages(update, context):
    if not update.message or not update.message.text:
        return
    if context.user_data.get("waiting_for")!="uid":
        await update.message.reply_text("Барои /start пахш кун", reply_markup=main_kb())
        return
    uid=update.message.text.strip()
    if not uid.isdigit() or not 8 <= len(uid) <= 12:
        await update.message.reply_text("UID бояд 8-12 ракам бошад")
        return
    prod=context.user_data.get("product")
    if not prod:
        await update.message.reply_text("Хатоги", reply_markup=main_kb())
        return
    context.user_data["uid"]=uid
    context.user_data["waiting_for"]=None
    await update.message.reply_text(f"Тасдик:\nМахсулот {prod['name']}\nUID {uid}\nНарх {money(prod['price'])}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Тасдик", callback_data="order_confirm")],[InlineKeyboardButton("Бекор", callback_data="cancel")]]))
    try:
        await update.message.delete()
    except:
        pass

async def receipt_photo(update, context):
    if context.user_data.get("waiting_for")!="receipt":
        return
    oid=context.user_data.get("order_id")
    order=get_order(oid)
    if not order:
        return
    if not ADMIN_ID:
        await update.message.reply_text("ADMIN_ID нест")
        return
    cap=f"Расид DP-{order['id']}\nМахсулот {order['product']}\nМаблаг {money(order['price'])}\nUID {order['uid']}\nКорбар {update.effective_user.full_name}"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("Тасдик", callback_data=f"admin:approve:{oid}"), InlineKeyboardButton("Рад", callback_data=f"admin:reject:{oid}")]])
    try:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=cap, reply_markup=kb)
    except:
        await update.message.reply_text("Расид фиристода нашуд")
        return
    order["receipt"]=True
    order["status"]="Расид фиристода шуд"
    save_data()
    context.user_data.pop("waiting_for",None)
    await update.message.reply_text(f"Расид DP-{oid} ба админ рафт", reply_markup=main_kb())

async def wallet_msgs(update, context):
    if context.user_data.get("waiting_for")=="wallet_amount":
        raw=update.message.text.strip()
        if not raw.isdigit():
            await update.message.reply_text("Факат ракам, мисол 50")
            return
        amt=int(raw)
        if amt<1 or amt>10000:
            await update.message.reply_text("1 то 10000")
            return
        tid=str(int(time.time()*1000))
        top={"id":tid,"user_id":update.effective_user.id,"amount":amt,"status":"pending","receipt":False}
        DB.setdefault("topups",[]).append(top)
        save_data()
        context.user_data["wallet_topup_id"]=tid
        context.user_data["waiting_for"]="wallet_receipt"
        await update.message.reply_text(f"Пур кардани хамён\nМаблаг {money(amt)}\nКорт {DC_CARD}\n\nАкси расидро фирист!")
        return
    if context.user_data.get("waiting_for")=="wallet_receipt":
        tid=context.user_data.get("wallet_topup_id")
        top=next((x for x in DB.get("topups",[]) if str(x.get("id"))==str(tid)), None)
        if not top:
            return
        cap=f"Хамён\nМаблаг {money(top['amount'])}\nКорбар {update.effective_user.full_name}"kb=InlineKeyboardMarkup([[InlineKeyboardButton("Кабул", callback_data=f"admin_wallet:approve:{tid}"), InlineKeyboardButton("Рад", callback_data=f"admin_wallet:reject:{tid}")]])
        # ин кисм содда
        await update.message.reply_text("Расиди хамён кабул шуд, интизори админ")
        context.user_data.pop("waiting_for",None)
        return
    await messages(update, context)

async def error_handler(update, context):
    logging.warning(f"Error {context.error}")

def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN нест")
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_msgs))
    app.add_error_handler(error_handler)
    print("DANATI_PATSANI running...")
    app.run_polling(drop_pending_updates=True)

if name=="main":
    main()
